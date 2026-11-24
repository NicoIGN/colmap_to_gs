// Repère commun : la première référence géographique importée fixe la vue (origine et grille).
// Chaque couche (splat, LiDAR, vecteurs) a sa propre origine et se pose dans la vue par
// une rotation + une translation.
import { layerPlacement } from "./georef.js";

const IDENTITY = [
  [1, 0, 0],
  [0, 1, 0],
  [0, 0, 1],
];
const FLIP_UP = [
  [1, 0, 0],
  [0, -1, 0],
  [0, 0, -1],
];

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function normalize(v) {
  const length = Math.hypot(v[0], v[1], v[2]);
  if (!(length > 0)) throw new Error("Axe de recalage nul.");
  return v.map((value) => value / length);
}

function mulMat(a, b) {
  return a.map((row) => [0, 1, 2].map(
    (col) => row[0] * b[0][col] + row[1] * b[1][col] + row[2] * b[2][col]
  ));
}

function mulVec(m, v) {
  return m.map((row) => dot(row, v));
}

/**
 * Pose d’un splat (repère local `local`) dans le repère de la vue `frame` :
 * p_vue = rotation · p_local + translation. Rotation orthonormalisée (Gram-Schmidt).
 */
export function frameAlignment(frame, local) {
  const { origin, linear } = layerPlacement(frame, local);

  const u0 = normalize([linear[0][0], linear[1][0], linear[2][0]]);
  const second = [linear[0][1], linear[1][1], linear[2][1]];
  const u1 = normalize(second.map((value, i) => value - dot(second, u0) * u0[i]));
  const u2 = cross(u0, u1);

  return {
    rotation: [0, 1, 2].map((row) => [u0[row], u1[row], u2[row]]),
    translation: origin,
  };
}

function quaternionFromMatrix(m) {
  const trace = m[0][0] + m[1][1] + m[2][2];
  let x;
  let y;
  let z;
  let w;

  if (trace > 0) {
    const s = Math.sqrt(trace + 1) * 2;
    w = 0.25 * s;
    x = (m[2][1] - m[1][2]) / s;
    y = (m[0][2] - m[2][0]) / s;
    z = (m[1][0] - m[0][1]) / s;
  } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
    const s = Math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2;
    w = (m[2][1] - m[1][2]) / s;
    x = 0.25 * s;
    y = (m[0][1] + m[1][0]) / s;
    z = (m[0][2] + m[2][0]) / s;
  } else if (m[1][1] > m[2][2]) {
    const s = Math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2;
    w = (m[0][2] - m[2][0]) / s;
    x = (m[0][1] + m[1][0]) / s;
    y = 0.25 * s;
    z = (m[1][2] + m[2][1]) / s;
  } else {
    const s = Math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2;
    w = (m[1][0] - m[0][1]) / s;
    x = (m[0][2] + m[2][0]) / s;
    y = (m[1][2] + m[2][1]) / s;
    z = 0.25 * s;
  }
  return [x, y, z, w];
}

/**
 * Position et rotation (quaternion x, y, z, w) à donner à l’entité du splat.
 * Le basculement flipUp s’applique après le recalage : F · (R p + t).
 */
export function splatPlacement(alignment, flipUp) {
  const flip = flipUp ? FLIP_UP : IDENTITY;
  const rotation = alignment ? alignment.rotation : IDENTITY;
  const translation = alignment ? alignment.translation : [0, 0, 0];

  return {
    position: mulVec(flip, translation),
    quaternion: quaternionFromMatrix(mulMat(flip, rotation)),
  };
}
