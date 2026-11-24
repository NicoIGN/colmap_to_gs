// Repère commun : la première référence géographique importée fixe le repère de la scène.
// Chaque autre splat reçoit une pose rigide (rotation + translation) vers ce repère.

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
const PROBE_M = 100;

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
 * Pose d’un splat (repère local `local`) dans le repère `frame` :
 * p_frame = rotation · p_local + translation.
 */
export function frameAlignment(frame, local) {
  const origin = local.worldToGeographic(0, 0, 0);
  const base = frame.geographicToWorld(origin.lat, origin.lon, origin.h);

  // Images des axes E, N, U du splat dans le repère commun, par différences finies.
  const axes = [0, 1, 2].map((axis) => {
    const probe = [0, 0, 0];
    probe[axis] = PROBE_M;
    const geo = local.worldToGeographic(...probe);
    const moved = frame.geographicToWorld(geo.lat, geo.lon, geo.h);
    return [
      (moved.x - base.x) / PROBE_M,
      (moved.y - base.y) / PROBE_M,
      (moved.h - base.h) / PROBE_M,
    ];
  });

  // Orthonormalisation (Gram-Schmidt) : absorbe les erreurs d’arrondi et de linéarisation.
  const u0 = normalize(axes[0]);
  const second = axes[1].map((value, i) => value - dot(axes[1], u0) * u0[i]);
  const u1 = normalize(second);
  const u2 = cross(u0, u1);

  return {
    rotation: [0, 1, 2].map((row) => [u0[row], u1[row], u2[row]]),
    translation: [base.x, base.y, base.h],
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
