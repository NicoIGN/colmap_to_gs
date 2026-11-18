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

// Retient les caméras les plus alignées sur la direction point de vue -> centre de scène,
// dans un cône de maxAngleDeg, entre le point de vue et le centre (maxAlong).
// Hystérésis : la caméra `keep` reste choisie tant qu'elle reste à moins de keepAngleDeg de la direction
// et que accept(i, true) est vrai ; sinon la plus centrée du cône est retenue.
// Sans direction, les plus proches d'abord.
export function selectNearestImages({
  centers,
  axes,
  viewer,
  direction = null,
  maxAlong = Infinity,
  count = 5,
  maxAngleDeg = 90,
  keepAngleDeg = maxAngleDeg,
  maxDistance = Infinity,
  accept = () => true,
  keep = -1,
  stats = null,
}) {
  const total = centers.length / 3;
  if (stats) {
    stats.total = total;
    stats.inCone = 0;
  }

  // Renvoie {dist, alignment} si la caméra i est valide au cône donné, sinon null.
  const evaluate = (i, angleDeg, relaxed) => {
    const cosMin = Math.cos((angleDeg * Math.PI) / 180);
    const dx = viewer[0] - centers[i * 3];
    const dy = viewer[1] - centers[i * 3 + 1];
    const dz = viewer[2] - centers[i * 3 + 2];
    const dist = Math.hypot(dx, dy, dz);
    // Le point de vue est le centre de cette caméra : elle reste toujours valide.
    if (dist < 1e-9) return { dist, alignment: 0 };
    if (dist > maxDistance) return null;
    let alignment = 0;
    if (direction) {
      // Caméras situées entre le point de vue et le centre de scène, dans le cône autour de cette direction.
      const cosView = direction[0] * (-dx / dist) + direction[1] * (-dy / dist) + direction[2] * (-dz / dist);
      if (cosView < cosMin) return null;
      if (dist * cosView > maxAlong) return null;
      alignment = 1 - cosView;
    } else {
      const dot = axes[i * 3] * dx + axes[i * 3 + 1] * dy + axes[i * 3 + 2] * dz;
      if (dot / dist < cosMin) return null;
    }
    return { dist, alignment };
  };

  if (keep >= 0 && keep < total && direction) {
    const kept = evaluate(keep, maxAngleDeg, true);
    const keptAngleDeg = kept ? (Math.acos(Math.min(1, Math.max(-1, 1 - kept.alignment))) * 180) / Math.PI : Infinity;
    if (kept && keptAngleDeg <= keepAngleDeg && accept(keep, true)) {
      const others = selectNearestImages({
        centers, axes, viewer, direction, maxAlong, count: count - 1,
        maxAngleDeg, maxDistance, accept, keep: -1,
      });
      return [keep, ...others.filter((i) => i !== keep)].slice(0, count);
    }
  }

  const candidates = [];
  for (let i = 0; i < total; i++) {
    const hit = evaluate(i, maxAngleDeg, false);
    if (!hit) continue;
    if (stats) stats.inCone += 1;
    if (!accept(i, false)) continue;
    candidates.push({ index: i, ...hit });
  }

  candidates.sort((a, b) => a.alignment - b.alignment || a.dist - b.dist);
  return candidates.slice(0, count).map((candidate) => candidate.index);
}
