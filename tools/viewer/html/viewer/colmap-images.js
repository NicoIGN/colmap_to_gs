export function isImageFileName(name) {
  return /\.(jpe?g|png)$/i.test(name ?? "");
}

function normalizePath(path) {
  return String(path ?? "").replace(/\\/g, "/").replace(/^\/+/, "");
}

// Indexe les images déposées par nom relatif COLMAP (sans le dossier racine
// déposé), avec repli sur le nom de fichier seul.
export function indexImageFiles(files) {
  const index = new Map();
  for (const file of files) {
    if (!isImageFileName(file.name)) continue;
    const parts = normalizePath(file.webkitRelativePath || file.name).split("/");
    const relative = parts.length > 1 ? parts.slice(1).join("/") : parts[0];
    if (!index.has(relative)) index.set(relative, file);
    const base = parts[parts.length - 1];
    if (!index.has(base)) index.set(base, file);
  }
  return index;
}

export function findImageFile(index, name) {
  const path = normalizePath(name);
  return index.get(path) ?? index.get(path.split("/").pop()) ?? null;
}

// Retient les caméras proches du point de vue, situées devant leur axe optique
// (dans un cône de maxAngleDeg autour de l'axe), les plus proches d'abord.
export function selectNearestImages({
  centers,
  axes,
  viewer,
  count = 5,
  maxAngleDeg = 90,
  maxDistance = Infinity,
  accept = () => true,
}) {
  const cosMin = Math.cos((maxAngleDeg * Math.PI) / 180);
  const candidates = [];
  const total = centers.length / 3;

  for (let i = 0; i < total; i++) {
    const dx = viewer[0] - centers[i * 3];
    const dy = viewer[1] - centers[i * 3 + 1];
    const dz = viewer[2] - centers[i * 3 + 2];
    const dist = Math.hypot(dx, dy, dz);
    if (dist < 1e-9 || dist > maxDistance) continue;
    const dot = axes[i * 3] * dx + axes[i * 3 + 1] * dy + axes[i * 3 + 2] * dz;
    if (dot / dist < cosMin) continue;
    if (!accept(i)) continue;
    candidates.push({ index: i, dist });
  }

  candidates.sort((a, b) => a.dist - b.dist);
  return candidates.slice(0, count).map((candidate) => candidate.index);
}
