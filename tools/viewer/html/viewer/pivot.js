// Pivot de rotation sous le clic (Alt + clic gauche) pour les données sans picking GPU :
// médiane des profondeurs des points projetés près du clic, soit l'altitude moyenne du plan visé.

const MAX_SAMPLES_PER_SOURCE = 200000;
const CLICK_RADIUS_PX = 40;

// camera : { matrix, fov (degrés), horizontalFov } où matrix est la pose monde de la caméra.
function screenRay(camera, px, py, width, height) {
  const m = camera.matrix;
  const origin = [m[12], m[13], m[14]];
  const right = [m[0], m[1], m[2]];
  const up = [m[4], m[5], m[6]];
  const forward = [-m[8], -m[9], -m[10]];
  const tanHalf = Math.tan((camera.fov * Math.PI) / 360);
  const aspect = width / height;
  const sx = camera.horizontalFov ? tanHalf : tanHalf * aspect;
  const sy = camera.horizontalFov ? tanHalf / aspect : tanHalf;

  const ndcX = (px / width) * 2 - 1;
  const ndcY = 1 - (py / height) * 2;
  const dir = [0, 1, 2].map((k) => forward[k] + right[k] * ndcX * sx + up[k] * ndcY * sy);
  const dirLength = Math.hypot(...dir);
  const unit = dir.map((v) => v / dirLength);
  const cosForward = unit[0] * forward[0] + unit[1] * forward[1] + unit[2] * forward[2];
  return { origin, right, up, forward, sx, sy, ndcX, ndcY, unit, cosForward, width, height };
}

// Sommet le plus proche du clic (en pixels écran) ; départage par profondeur minimale.
// sources : [{ label, positions: Float32Array (xyz local), matrix: number[16] colonne-majeure }]
// Retourne { point (monde), label } ou null si aucun sommet dans radiusPx.
export function findVertexAtPixel({ sources, camera, px, py, width, height, radiusPx = CLICK_RADIUS_PX }) {
  const ray = screenRay(camera, px, py, width, height);
  const { origin, right, up, forward, sx, sy, ndcX, ndcY } = ray;
  let best = null;
  let bestDistSq = radiusPx * radiusPx;
  let bestDepth = Infinity;

  for (const source of sources) {
    const count = source.positions.length / 3;
    const w = source.matrix;
    for (let i = 0; i < count; i++) {
      const x = source.positions[i * 3];
      const y = source.positions[i * 3 + 1];
      const z = source.positions[i * 3 + 2];
      const wx = w[0] * x + w[4] * y + w[8] * z + w[12];
      const wy = w[1] * x + w[5] * y + w[9] * z + w[13];
      const wz = w[2] * x + w[6] * y + w[10] * z + w[14];
      const vx = wx - origin[0];
      const vy = wy - origin[1];
      const vz = wz - origin[2];
      const depth = vx * forward[0] + vy * forward[1] + vz * forward[2];
      if (depth <= 1e-6) continue;
      const px2 = (vx * right[0] + vy * right[1] + vz * right[2]) / (depth * sx);
      const py2 = (vx * up[0] + vy * up[1] + vz * up[2]) / (depth * sy);
      const dxPx = ((px2 - ndcX) * width) / 2;
      const dyPx = ((py2 - ndcY) * height) / 2;
      const distSq = dxPx * dxPx + dyPx * dyPx;
      if (distSq > bestDistSq) continue;
      if (distSq === bestDistSq && depth >= bestDepth) continue;
      bestDistSq = distSq;
      bestDepth = depth;
      best = { point: [wx, wy, wz], label: source.label };
    }
  }
  return best;
}

// sources : [{ label, positions: Float32Array (xyz local), matrix: number[16] colonne-majeure }]
// camera : { matrix, fov (degrés), horizontalFov, aspect } où matrix est la pose monde de la caméra.
export function findPivotAtPixel({ sources, camera, px, py, width, height }) {
  const { origin, right, up, forward, sx, sy, ndcX, ndcY, unit, cosForward } =
    screenRay(camera, px, py, width, height);

  for (const source of sources) {
    const count = source.positions.length / 3;
    if (!count) continue;
    const w = source.matrix;
    const stride = Math.max(1, Math.ceil(count / MAX_SAMPLES_PER_SOURCE));
    const depths = [];

    for (let i = 0; i < count; i += stride) {
      const x = source.positions[i * 3];
      const y = source.positions[i * 3 + 1];
      const z = source.positions[i * 3 + 2];
      const vx = w[0] * x + w[4] * y + w[8] * z + w[12] - origin[0];
      const vy = w[1] * x + w[5] * y + w[9] * z + w[13] - origin[1];
      const vz = w[2] * x + w[6] * y + w[10] * z + w[14] - origin[2];
      const depth = vx * forward[0] + vy * forward[1] + vz * forward[2];
      if (depth <= 1e-6) continue;
      const px2 = (vx * right[0] + vy * right[1] + vz * right[2]) / (depth * sx);
      const py2 = (vx * up[0] + vy * up[1] + vz * up[2]) / (depth * sy);
      const dxPx = ((px2 - ndcX) * width) / 2;
      const dyPx = ((py2 - ndcY) * height) / 2;
      if (dxPx * dxPx + dyPx * dyPx <= CLICK_RADIUS_PX * CLICK_RADIUS_PX) depths.push(depth);
    }

    if (!depths.length) continue;
    depths.sort((a, b) => a - b);
    const median = depths[depths.length >> 1];
    const t = median / cosForward;
    return {
      point: [origin[0] + unit[0] * t, origin[1] + unit[1] * t, origin[2] + unit[2] * t],
      label: source.label,
    };
  }
  return null;
}
