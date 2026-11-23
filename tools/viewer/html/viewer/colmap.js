import * as pc from "playcanvas";
import { registerOverlayMaterial } from "./overlay-depth.js";
import {
  findImageFile,
  indexImageFiles,
  isImageFileName,
  selectNearestImages,
} from "./colmap-images.js";

// Mode Images : frustums normaux = 1 % de l'étendue ; cadre des images affichées = x5 (5 %).
const IMAGE_FRUSTUM_SCALE = 5;
// Une image s'affiche dès que son cadre (taille nominale) atteint cette taille à l'écran.
const IMAGE_MIN_SCREEN_PX = 32;
// Nombre d'images affichées à la fois ; les textures des 10 dernières restent en mémoire.
const IMAGE_SHOWN = 1;
// Candidats classés par distance, parmi lesquels on cherche celui dont le cadre agrandi couvre le centre.
const IMAGE_CANDIDATES = 16;
// Hystérésis : une image affichée reste choisie tant que son cadre reste ≥ 80 % du seuil.
const IMAGE_HYSTERESIS = 0.8;
// Une image candidate doit regarder à moins de cet angle de la direction de vue.
const IMAGE_AXIS_DEG = 35;
// Panorama vu de l'intérieur de sa sphère : il est affiché si son centre est à moins de cet angle du sens de la vue.
const PANORAMA_VIEW_DEG = 30;
// Un panorama courant n'est remplacé que si un autre est nettement plus centré.
const PANORAMA_HYSTERESIS_DEG = 5;
const IMAGE_SLOTS = 10;
const IMAGE_MAX_WIDTH = 1024;
const TEXTURE_CACHE_SIZE = IMAGE_SLOTS;
const PANORAMA_SEGMENTS = 96;
// Sphère et cube 360° réduits de 33 % par rapport au cadre agrandi.
const PANORAMA_SIZE_FACTOR = 0.67;
// Rayon du marqueur (sphère ou cube) d'un panorama non affiché, en fraction de la profondeur des frustums.
const PANORAMA_MARKER_FACTOR = 0.5;
const PANORAMA_RINGS = 48;
const PANORAMA_CUBE_FACE = 512;
const PANORAMA_CUBE_SOURCE_WIDTH = 2048;

// Faces d'un cube dans le repère caméra : centre, axe droit et axe bas de chaque face à 90°.
const CUBE_FACES = [
  { center: [0, 0, 1], right: [1, 0, 0], down: [0, 1, 0] },
  { center: [1, 0, 0], right: [0, 0, -1], down: [0, 1, 0] },
  { center: [0, 0, -1], right: [-1, 0, 0], down: [0, 1, 0] },
  { center: [-1, 0, 0], right: [0, 0, 1], down: [0, 1, 0] },
  { center: [0, -1, 0], right: [1, 0, 0], down: [0, 0, 1] },
  { center: [0, 1, 0], right: [1, 0, 0], down: [0, 0, -1] },
];

/**
 * Couche COLMAP sparse (PlayCanvas)
 * - points3D.bin -> points
 * - images.bin   -> centres caméra (points rouges)
 * - images + cameras.bin -> frustums (lignes rouges)
 */

const MODEL_NAMES = {
  0: "SIMPLE_PINHOLE",
  1: "PINHOLE",
  2: "SIMPLE_RADIAL",
  3: "RADIAL",
  4: "OPENCV",
  5: "OPENCV_FISHEYE",
  6: "FULL_OPENCV",
  7: "FOV",
  8: "SIMPLE_RADIAL_FISHEYE",
  9: "RADIAL_FISHEYE",
  10: "THIN_PRISM_FISHEYE",
  17: "EQUIRECTANGULAR",
};

export function colmapBounds(positionSets, flip = false) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const positions of positionSets) {
    for (let i = 0; i + 2 < positions.length; i += 3) {
      const point = [positions[i], positions[i + 1], positions[i + 2]];
      if (!point.every(Number.isFinite)) continue;
      for (let axis = 0; axis < 3; axis++) {
        const value = flip && axis > 0 ? -point[axis] : point[axis];
        min[axis] = Math.min(min[axis], value);
        max[axis] = Math.max(max[axis], value);
      }
    }
  }
  return Number.isFinite(min[0]) ? { min, max } : null;
}

export function cameraFrustumDepth(centers, fallbackRadius) {
  const spans = [0, 1, 2].map((axis) => {
    const values = [];
    for (let i = axis; i < centers.length; i += 3) {
      if (Number.isFinite(centers[i])) values.push(centers[i]);
    }
    values.sort((a, b) => a - b);
    if (values.length < 2) return 0;
    const trim = Math.floor(values.length * 0.02);
    return values[values.length - 1 - trim] - values[trim];
  });
  const extent = Math.max(...spans);
  const reference = extent > 0 ? extent : fallbackRadius;
  if (!Number.isFinite(reference) || reference <= 0) {
    throw new Error("Échelle COLMAP indéterminée pour les frustums.");
  }
  return reference * 0.01;
}

const MODEL_PARAM_COUNT = {
  SIMPLE_PINHOLE: 3,
  PINHOLE: 4,
  SIMPLE_RADIAL: 4,
  RADIAL: 5,
  OPENCV: 8,
  OPENCV_FISHEYE: 8,
  FULL_OPENCV: 12,
  FOV: 5,
  SIMPLE_RADIAL_FISHEYE: 4,
  RADIAL_FISHEYE: 5,
  THIN_PRISM_FISHEYE: 12,
  EQUIRECTANGULAR: 2,
};

function readU64(view, state) {
  const value = view.getBigUint64(state.offset, true);
  state.offset += 8;
  return Number(value);
}

function readU32(view, state) {
  const value = view.getUint32(state.offset, true);
  state.offset += 4;
  return value;
}

function readI32(view, state) {
  const value = view.getInt32(state.offset, true);
  state.offset += 4;
  return value;
}

function readF64(view, state) {
  const value = view.getFloat64(state.offset, true);
  state.offset += 8;
  return value;
}

function readStringZ(view, state) {
  const bytes = [];

  while (state.offset < view.byteLength) {
    const value = view.getUint8(state.offset++);
    if (value === 0) break;
    bytes.push(value);
  }

  return new TextDecoder("utf-8").decode(new Uint8Array(bytes));
}

function parseCamerasBin(buffer) {
  const view = new DataView(buffer);
  const state = { offset: 0 };
  const count = readU64(view, state);

  const cameras = new Map();

  for (let i = 0; i < count; i++) {
    const cameraId = readU32(view, state);
    const modelId = readI32(view, state);
    const width = readU64(view, state);
    const height = readU64(view, state);

    const modelName = MODEL_NAMES[modelId];
    if (!modelName) {
      throw new Error(`Modèle caméra COLMAP inconnu: ${modelId}`);
    }

    const paramCount = MODEL_PARAM_COUNT[modelName];
    const params = new Array(paramCount);

    for (let k = 0; k < paramCount; k++) {
      params[k] = readF64(view, state);
    }

    cameras.set(cameraId, {
      cameraId,
      modelName,
      width,
      height,
      params,
    });
  }

  return cameras;
}

function parseImagesBin(buffer) {
  const view = new DataView(buffer);
  const state = { offset: 0 };
  const count = readU64(view, state);

  const images = [];

  for (let i = 0; i < count; i++) {
    const imageId = readU32(view, state);
    const qvec = [
      readF64(view, state),
      readF64(view, state),
      readF64(view, state),
      readF64(view, state),
    ];

    const tvec = [
      readF64(view, state),
      readF64(view, state),
      readF64(view, state),
    ];

    const cameraId = readU32(view, state);
    const name = readStringZ(view, state);

    const numPoints2D = readU64(view, state);
    state.offset += numPoints2D * 24; // x,y,double + point3D_id,int64

    images.push({ imageId, qvec, tvec, cameraId, name });
  }

  return images;
}

function parsePoints3DBin(buffer, maxPoints = 300000) {
  const view = new DataView(buffer);
  const state = { offset: 0 };
  const count = readU64(view, state);

  const step = Math.max(1, Math.floor(count / Math.max(1, maxPoints)));
  const shownCapacity = Math.ceil(count / step);

  const positions = new Float32Array(shownCapacity * 3);
  const colors = new Float32Array(shownCapacity * 3);

  let writeIndex = 0;

  for (let i = 0; i < count; i++) {
    readU64(view, state); // point3D_id

    const x = readF64(view, state);
    const y = readF64(view, state);
    const z = readF64(view, state);

    const r = view.getUint8(state.offset++);
    const g = view.getUint8(state.offset++);
    const b = view.getUint8(state.offset++);

    readF64(view, state); // error

    const trackLength = readU64(view, state);
    state.offset += trackLength * 8; // image_id + point2D_idx

    if (i % step !== 0) continue;

    positions[writeIndex * 3 + 0] = x;
    positions[writeIndex * 3 + 1] = y;
    positions[writeIndex * 3 + 2] = z;

    colors[writeIndex * 3 + 0] = r / 255;
    colors[writeIndex * 3 + 1] = g / 255;
    colors[writeIndex * 3 + 2] = b / 255;

    writeIndex++;
  }

  return {
    positions: positions.subarray(0, writeIndex * 3),
    colors: colors.subarray(0, writeIndex * 3),
    originalCount: count,
    shownCount: writeIndex,
  };
}

/**
 * COLMAP qvec = [qw,qx,qy,qz] rotation world->camera.
 */
function qvecToMat3(qvec) {
  let [qw, qx, qy, qz] = qvec;
  const norm = Math.hypot(qw, qx, qy, qz) || 1;

  qw /= norm;
  qx /= norm;
  qy /= norm;
  qz /= norm;

  return [
    1 - 2 * (qy * qy + qz * qz),
    2 * (qx * qy - qz * qw),
    2 * (qx * qz + qy * qw),

    2 * (qx * qy + qz * qw),
    1 - 2 * (qx * qx + qz * qz),
    2 * (qy * qz - qx * qw),

    2 * (qx * qz - qy * qw),
    2 * (qy * qz + qx * qw),
    1 - 2 * (qx * qx + qy * qy),
  ];
}

function sub3(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function normalize3(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}

function transpose3(m) {
  return [
    m[0], m[3], m[6],
    m[1], m[4], m[7],
    m[2], m[5], m[8],
  ];
}

function mulMat3Vec3(m, v) {
  return [
    m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
    m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
    m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
  ];
}

function getCameraCenter(image) {
  const rcw = qvecToMat3(image.qvec);
  const rwc = transpose3(rcw);
  const rwcT = mulMat3Vec3(rwc, image.tvec);

  return [-rwcT[0], -rwcT[1], -rwcT[2]];
}

function getFxFy(camera) {
  const p = camera.params;

  switch (camera.modelName) {
    case "SIMPLE_PINHOLE":
    case "SIMPLE_RADIAL":
    case "RADIAL":
    case "SIMPLE_RADIAL_FISHEYE":
    case "RADIAL_FISHEYE":
      return { fx: p[0], fy: p[0] };

    case "PINHOLE":
    case "OPENCV":
    case "OPENCV_FISHEYE":
    case "FULL_OPENCV":
    case "THIN_PRISM_FISHEYE":
      return { fx: p[0], fy: p[1] };

    case "FOV":
      return { fx: p[0], fy: p[0] };

    // Cadre nominal de 90° horizontal, utilisé pour la sélection et les frustums.
    case "EQUIRECTANGULAR":
      return { fx: camera.width / 2, fy: camera.width / 2 };

    default:
      return { fx: camera.width, fy: camera.height };
  }
}

export function colmapPointPixels(size, pixelRatio = 1, maximum = 64) {
  return Math.min(maximum, Math.max(1, size * Math.min(pixelRatio, 2)));
}

export function thickenLineSegments(positions, thickness) {
  const output = [];
  for (let i = 0; i + 5 < positions.length; i += 6) {
    const a = [positions[i], positions[i + 1], positions[i + 2]];
    const b = [positions[i + 3], positions[i + 4], positions[i + 5]];
    const direction = b.map((value, axis) => value - a[axis]);
    const length = Math.hypot(...direction);
    if (!Number.isFinite(length) || length === 0) continue;
    for (let axis = 0; axis < 3; axis++) direction[axis] /= length;
    const reference = Math.abs(direction[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0];
    let side = [
      direction[1] * reference[2] - direction[2] * reference[1],
      direction[2] * reference[0] - direction[0] * reference[2],
      direction[0] * reference[1] - direction[1] * reference[0],
    ];
    const sideLength = Math.hypot(...side);
    side = side.map((value) => value * thickness / (2 * sideLength));
    const corners = [
      a.map((value, axis) => value + side[axis]),
      a.map((value, axis) => value - side[axis]),
      b.map((value, axis) => value + side[axis]),
      b.map((value, axis) => value - side[axis]),
    ];
    for (const index of [0, 1, 2, 2, 1, 3]) output.push(...corners[index]);
  }
  return new Float32Array(output);
}

function setPointSize(app, material, size) {
  const gl = app.graphicsDevice.gl;
  const range = gl?.getParameter(gl.ALIASED_POINT_SIZE_RANGE);
  material.setParameter("uPointSize", colmapPointPixels(
    size, window.devicePixelRatio || 1, Number(range?.[1]) || 64
  ));
}

function makeColmapMaterial(app, { points = false, pointSize = 3, onTop = false } = {}) {
  const material = new pc.ShaderMaterial({
    uniqueName: points
      ? "ColmapCircularPointsMaterial"
      : "ColmapLinesMaterial",

    attributes: {
      vertex_position: pc.SEMANTIC_POSITION,
      vertex_color: pc.SEMANTIC_COLOR,
    },

    vertexGLSL: `
      attribute vec3 vertex_position;
      attribute vec4 vertex_color;

      uniform mat4 matrix_model;
      uniform mat4 matrix_viewProjection;
      uniform float uPointSize;
      uniform float uHighlight;

      varying vec4 vColor;

      void main(void) {
        vColor = mix(vertex_color, vec4(1.0, 0.92, 0.0, 1.0), uHighlight);
        gl_Position =
          matrix_viewProjection *
          matrix_model *
          vec4(vertex_position, 1.0);
        gl_PointSize = uPointSize * (1.0 + 1.5 * uHighlight);
      }
    `,

    fragmentGLSL: points
      ? `
        precision highp float;
        varying vec4 vColor;
        void main(void) {
          vec2 offset = gl_PointCoord - vec2(0.5);
          if (dot(offset, offset) > 0.25) discard;
          gl_FragColor = vColor;
        }
      `
      : `
        precision highp float;
        varying vec4 vColor;
        void main(void) {
          gl_FragColor = vColor;
        }
      `,
  });

  setPointSize(app, material, pointSize);
  material.setParameter("uHighlight", 0);

  material.cull = pc.CULLFACE_NONE;
  if (onTop) registerOverlayMaterial(material, "colmap");
  else material.depthTest = true;
  material.depthWrite = false;
  material.blendType = pc.BLEND_NONE;
  material.update();

  return material;
}

export function setMeshHighlight(resource, on) {
  resource?.material.setParameter("uHighlight", on ? 1 : 0);
}

function makeColmapMesh(app, positions, rgba, primitive, material) {
  const mesh = new pc.Mesh(app.graphicsDevice);

  mesh.setPositions(positions);
  mesh.setColors(rgba);
  mesh.update(primitive, true);

  const meshInstance = new pc.MeshInstance(mesh, material);

  // Évite les disparitions liées à un culling trop agressif.
  meshInstance.cull = false;

  return { mesh, material, meshInstance };
}

function getNamedLayer(app, name) {
  const existing = app.scene.layers.getLayerByName(name);
  if (existing) return existing;
  // Calques ajoutés après « World » (splats) : ils sont dessinés par-dessus, dans l'ordre de création.
  const layer = new pc.Layer({ name });
  app.scene.layers.push(layer);
  for (const camera of app.root.findComponents("camera")) {
    if (!camera.layers.includes(layer.id)) camera.layers = [...camera.layers, layer.id];
  }
  return layer;
}

export function getOverlayLayer(app) {
  return getNamedLayer(app, "Overlay");
}

// Calque des images, dessiné après « Overlay » pour passer au-dessus des frustums et sommets caméra.
function getImageLayer(app) {
  getOverlayLayer(app);
  return getNamedLayer(app, "ImageOverlay");
}

export function makePointMesh(app, positions, colors, pointSize = 3, { onTop = false } = {}) {
  const count = positions.length / 3;
  const rgba = new Float32Array(count * 4);

  for (let i = 0; i < count; i++) {
    rgba[i * 4 + 0] = colors[i * 3 + 0] ?? 1;
    rgba[i * 4 + 1] = colors[i * 3 + 1] ?? 1;
    rgba[i * 4 + 2] = colors[i * 3 + 2] ?? 1;
    rgba[i * 4 + 3] = 1;
  }

  return makeColmapMesh(
    app,
    positions,
    rgba,
    pc.PRIMITIVE_POINTS,
    makeColmapMaterial(app, { points: true, pointSize, onTop })
  );
}

function makeLineMesh(app, positions, color = [1, 0, 0, 1], { onTop = false } = {}) {
  const bounds = [[Infinity, -Infinity], [Infinity, -Infinity], [Infinity, -Infinity]];
  for (let i = 0; i < positions.length; i += 3) {
    for (let axis = 0; axis < 3; axis++) {
      bounds[axis][0] = Math.min(bounds[axis][0], positions[i + axis]);
      bounds[axis][1] = Math.max(bounds[axis][1], positions[i + axis]);
    }
  }
  const sceneDiameter = Math.hypot(...bounds.map(([min, max]) => max - min));
  const thickness = Math.max(Number.EPSILON, sceneDiameter * 0.00015);
  positions = thickenLineSegments(positions, thickness);
  const count = positions.length / 3;
  const rgba = new Float32Array(count * 4);

  for (let i = 0; i < count; i++) {
    rgba[i * 4 + 0] = color[0];
    rgba[i * 4 + 1] = color[1];
    rgba[i * 4 + 2] = color[2];
    rgba[i * 4 + 3] = color[3];
  }

  return makeColmapMesh(
    app,
    positions,
    rgba,
    pc.PRIMITIVE_LINES,
    makeColmapMaterial(app, { onTop })
  );
}

// Coins en coordonnées du modèle : [centre, haut-gauche, haut-droite, bas-droite, bas-gauche] à la profondeur near.
function frustumCorners(image, camera, near) {
  const { fx, fy } = getFxFy(camera);
  const halfW = (camera.width * near) / (2 * fx);
  const halfH = (camera.height * near) / (2 * fy);
  const rwc = transpose3(qvecToMat3(image.qvec));
  const center = getCameraCenter(image);
  const local = [
    [0, 0, 0],
    [-halfW, -halfH, near],
    [halfW, -halfH, near],
    [halfW, halfH, near],
    [-halfW, halfH, near],
  ];
  return local.map((point) => {
    const rotated = mulMat3Vec3(rwc, point);
    return [rotated[0] + center[0], rotated[1] + center[1], rotated[2] + center[2]];
  });
}

// Ajoute au tableau `out` les segments (paires de sommets) d'un cube filaire ou d'une sphère
// symbolisée par ses parallèles et méridiens, centrés sur `center`.
function appendPanoramaMarker(out, center, radius, cube) {
  const point = (x, y, z) => [center[0] + x * radius, center[1] + y * radius, center[2] + z * radius];
  const segment = (a, b) => out.push(...point(...a), ...point(...b));
  if (cube) {
    const corners = [];
    for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) corners.push([x, y, z]);
    for (const a of corners) {
      for (const b of corners) {
        const differing = (a[0] !== b[0]) + (a[1] !== b[1]) + (a[2] !== b[2]);
        // Arête : deux coins ne différant que d'un axe, traitée une seule fois.
        if (differing === 1 && corners.indexOf(a) < corners.indexOf(b)) segment(a, b);
      }
    }
    return;
  }
  const rings = 6;
  const meridians = 8;
  const samples = 24;
  const onSphere = (theta, phi) => [Math.sin(theta) * Math.cos(phi), Math.cos(theta), Math.sin(theta) * Math.sin(phi)];
  // Parallèles (cercles à latitude constante), hors pôles.
  for (let r = 1; r < rings; r++) {
    const theta = (r / rings) * Math.PI;
    for (let s = 0; s < samples; s++) {
      segment(onSphere(theta, (s / samples) * 2 * Math.PI), onSphere(theta, ((s + 1) / samples) * 2 * Math.PI));
    }
  }
  // Méridiens (demi-grands cercles passant par les pôles).
  for (let m = 0; m < meridians; m++) {
    const phi = (m / meridians) * 2 * Math.PI;
    for (let s = 0; s < samples / 2; s++) {
      segment(onSphere((s / (samples / 2)) * Math.PI, phi), onSphere(((s + 1) / (samples / 2)) * Math.PI, phi));
    }
  }
}

function isPanorama(camera) {
  return camera.modelName === "EQUIRECTANGULAR";
}

// Le panorama COLMAP est tourné de 180° autour de l'axe vertical par rapport à la géométrie : on inverse x et z.
function applyPanoramaYawFix(local) {
  local[0] = -local[0];
  local[2] = -local[2];
}

// Sphère 360° vue de l'intérieur. Repère caméra : x à droite, y vers le bas, z dans le sens du regard.
// Longitude 0 au centre de l'image ; latitude +90° en haut de l'image (v = 0).
function panoramaSphere(image, radius, mirror = false) {
  const rwc = transpose3(qvecToMat3(image.qvec));
  const center = getCameraCenter(image);
  const positions = [];
  const uvs = [];
  for (let r = 0; r <= PANORAMA_RINGS; r++) {
    const lat = Math.PI / 2 - (r / PANORAMA_RINGS) * Math.PI;
    for (let s = 0; s <= PANORAMA_SEGMENTS; s++) {
      const lon = -Math.PI + (s / PANORAMA_SEGMENTS) * 2 * Math.PI;
      const local = [
        Math.cos(lat) * Math.sin(lon) * radius,
        -Math.sin(lat) * radius,
        Math.cos(lat) * Math.cos(lon) * radius,
      ];
      applyPanoramaYawFix(local);
      const rotated = mulMat3Vec3(rwc, local);
      positions.push(rotated[0] + center[0], rotated[1] + center[1], rotated[2] + center[2]);
      const u = s / PANORAMA_SEGMENTS;
      uvs.push(mirror ? 1 - u : u, r / PANORAMA_RINGS);
    }
  }
  const row = PANORAMA_SEGMENTS + 1;
  const indices = [];
  for (let r = 0; r < PANORAMA_RINGS; r++) {
    for (let s = 0; s < PANORAMA_SEGMENTS; s++) {
      const a = r * row + s;
      const b = a + row;
      indices.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  return {
    positions: new Float32Array(positions),
    uvs: new Float32Array(uvs),
    indices: new Uint16Array(indices),
  };
}

// Atlas 3×2 des six faces du cube (RGBA, faces de faceSize px) échantillonné depuis une équirectangulaire.
// Les faces suivent CUBE_FACES dans l'ordre, rangées ligne par ligne.
export function equirectToCubeAtlas(rgba, width, height, faceSize = PANORAMA_CUBE_FACE, mirror = false) {
  const atlasWidth = faceSize * 3;
  const atlas = new Uint8ClampedArray(atlasWidth * faceSize * 2 * 4);
  CUBE_FACES.forEach((face, index) => {
    const offsetX = (index % 3) * faceSize;
    const offsetY = Math.floor(index / 3) * faceSize;
    for (let v = 0; v < faceSize; v++) {
      const b = (2 * (v + 0.5)) / faceSize - 1;
      for (let u = 0; u < faceSize; u++) {
        const a = (2 * (u + 0.5)) / faceSize - 1;
        const x = face.center[0] + a * face.right[0] + b * face.down[0];
        const y = face.center[1] + a * face.right[1] + b * face.down[1];
        const z = face.center[2] + a * face.right[2] + b * face.down[2];
        const theta = Math.atan2(x, z);
        const phi = Math.atan2(-y, Math.hypot(x, z));
        const sx = Math.min(width - 1, Math.floor((theta / (2 * Math.PI) + 0.5) * width));
        const sxMirrored = mirror ? width - 1 - sx : sx;
        const sy = Math.min(height - 1, Math.floor((0.5 - phi / Math.PI) * height));
        const src = (sy * width + sxMirrored) * 4;
        const dst = ((offsetY + v) * atlasWidth + offsetX + u) * 4;
        atlas[dst] = rgba[src];
        atlas[dst + 1] = rgba[src + 1];
        atlas[dst + 2] = rgba[src + 2];
        atlas[dst + 3] = 255;
      }
    }
  });
  return { data: atlas, width: atlasWidth, height: faceSize * 2 };
}

// Six quads (un par face) posés sur les faces du cube centré sur la caméra, UV dans l'atlas.
function panoramaCube(image, radius) {
  const rwc = transpose3(qvecToMat3(image.qvec));
  const center = getCameraCenter(image);
  const positions = [];
  const uvs = [];
  const indices = [];
  CUBE_FACES.forEach((face, index) => {
    const col = index % 3;
    const row = Math.floor(index / 3);
    const u0 = col / 3;
    const u1 = (col + 1) / 3;
    const v0 = row / 2;
    const v1 = (row + 1) / 2;
    const corners = [
      [-1, -1, u0, v0],
      [1, -1, u1, v0],
      [1, 1, u1, v1],
      [-1, 1, u0, v1],
    ];
    for (const [a, b, u, v] of corners) {
      const local = [0, 1, 2].map((k) => (face.center[k] + a * face.right[k] + b * face.down[k]) * radius);
      applyPanoramaYawFix(local);
      const world = mulMat3Vec3(rwc, local);
      positions.push(world[0] + center[0], world[1] + center[1], world[2] + center[2]);
      uvs.push(u, v);
    }
    const base = index * 4;
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
  });
  return {
    positions: new Float32Array(positions),
    uvs: new Float32Array(uvs),
    indices: new Uint16Array(indices),
  };
}

// Vrai si les quatre coins du cadre affiché sont tous devant le point de vue, dans le sens du regard.
function imageInFrontOfViewer(model, index, viewer, forward) {
  const image = model.colmapImages[index];
  const camera = model.cameras.get(image.cameraId);
  if (!camera) return false;
  return frustumCorners(image, camera, model.depth * IMAGE_FRUSTUM_SCALE)
    .slice(1)
    .every((c) => (c[0] - viewer[0]) * forward[0] + (c[1] - viewer[1]) * forward[1] + (c[2] - viewer[2]) * forward[2] > 0);
}

// Panorama : angle entre le sens de la vue et le centre de la sphère, et vrai si la sphère affichée (rayon agrandi)
// couvre le centre de la vue. Depuis l'intérieur, on compare l'angle à PANORAMA_VIEW_DEG.
function panoramaView(model, index, viewer, forward) {
  const cx = model.cameraCenters[index * 3];
  const cy = model.cameraCenters[index * 3 + 1];
  const cz = model.cameraCenters[index * 3 + 2];
  const v = [cx - viewer[0], cy - viewer[1], cz - viewer[2]];
  const dist = Math.hypot(...v);
  const radius = model.depth * IMAGE_FRUSTUM_SCALE * PANORAMA_SIZE_FACTOR;
  if (dist < 1e-9) return { angle: 0, covers: true };
  const cosAngle = (v[0] * forward[0] + v[1] * forward[1] + v[2] * forward[2]) / dist;
  const angle = Math.acos(Math.min(1, Math.max(-1, cosAngle)));
  const covers = dist <= radius
    ? angle <= (PANORAMA_VIEW_DEG * Math.PI) / 180
    : angle <= Math.asin(radius / dist);
  return { angle, covers };
}

// Vrai si le point de vue est du côté du sommet caméra par rapport au plan du cadre à la profondeur `near` ;
// faux s'il est au-delà, c.-à-d. qu'il regarde l'image par derrière.
function viewerOnApexSide(image, near, viewer) {
  const center = getCameraCenter(image);
  const p = mulMat3Vec3(qvecToMat3(image.qvec), [viewer[0] - center[0], viewer[1] - center[1], viewer[2] - center[2]]);
  return p[2] < near;
}

// Vrai si le rayon de vue (depuis le point de vue, vers l'avant) traverse le cadre agrandi à la profondeur `near`.
export function coversViewCenter(image, camera, near, viewer, forward) {
  const { fx, fy } = getFxFy(camera);
  const halfW = (camera.width * near) / (2 * fx);
  const halfH = (camera.height * near) / (2 * fy);
  if (!viewerOnApexSide(image, near, viewer)) return false;
  const rcw = qvecToMat3(image.qvec);
  const center = getCameraCenter(image);
  const p = mulMat3Vec3(rcw, [viewer[0] - center[0], viewer[1] - center[1], viewer[2] - center[2]]);
  const d = mulMat3Vec3(rcw, forward);
  if (Math.abs(d[2]) < 1e-9) return false;
  const s = (near - p[2]) / d[2];
  if (s <= 0) return false;
  return Math.abs(p[0] + s * d[0]) <= halfW && Math.abs(p[1] + s * d[1]) <= halfH;
}

function buildCameraAxes(images, cameras) {
  const out = new Float32Array(images.length * 3);
  for (let i = 0; i < images.length; i++) {
    // Le centre d'un panorama est en -z (voir applyPanoramaYawFix).
    const camera = cameras.get(images[i].cameraId);
    const sign = camera && isPanorama(camera) ? -1 : 1;
    const axis = mulMat3Vec3(transpose3(qvecToMat3(images[i].qvec)), [0, 0, sign]);
    out[i * 3] = axis[0];
    out[i * 3 + 1] = axis[1];
    out[i * 3 + 2] = axis[2];
  }
  return out;
}

export function createColmapLayer({
  app,
  setStatus,
  getSceneRadius,
  getFlipUp,
  getViewerPosition,
  getViewerDirection,
  getViewerTarget,
  worldToScreen,
  pixelScaleAt,
  registerLayer,
  registerGroup,
  onBoundsChanged,
  onGauge,
}) {
  const sceneColmapInfo = document.getElementById("sceneColmapInfo");
  const input = document.getElementById("colmapFolderInput");
  const pointSize = document.getElementById("colmapPointSize");
  const cameraSize = document.getElementById("colmapCameraSize");
  const panoramaModeRow = document.getElementById("colmapPanoramaModeRow");
  const panoramaModeSelect = document.getElementById("colmapPanoramaMode");
  const panoramaMirrorInput = document.getElementById("colmapPanoramaMirror");
  let panoramaMode = panoramaModeSelect?.value ?? "cube";
  let panoramaMirror = panoramaMirrorInput?.checked ?? true;
  const frustumSizeInput = document.getElementById("colmapFrustumSize");
  const imageTransparencyInput = document.getElementById("colmapImageTransparency");
  let frustumFactor = Number(frustumSizeInput?.value ?? 100) / 100;
  let imageOpacity = Number(imageTransparencyInput?.value ?? 100) / 100;

  let disposed = false;
  let nextModelId = 1;
  const models = new Map();
  const removers = [];
  let imageIndex = null;
  const textureCache = new Map();

  const refreshPointSizes = () => {
    for (const model of models.values()) {
      for (const [input, resource] of [
        [pointSize, model.pointsResource],
        [cameraSize, model.cameraCentersResource],
      ]) {
        if (!input) continue;
        const output = document.getElementById(`${input.id}Value`);
        if (output) output.value = `${input.value} px`;
        if (resource) setPointSize(app, resource.material, Number(input.value));
      }
    }
  };

  function listen(element, type, handler) {
    if (!element) return;
    element.addEventListener(type, handler);
    removers.push(() => element.removeEventListener(type, handler));
  }

  function setInfo(text) {
    if (sceneColmapInfo) sceneColmapInfo.textContent = text;
  }

  function getTotals() {
    return [...models.values()].reduce((totals, model) => {
      totals.images += model.images;
      totals.pointsTotal += model.pointsTotal;
      totals.pointsShown += model.pointsShown;
      totals.frustumsTotal += model.frustumsTotal;
      totals.frustumsShown += model.frustumsShown;
      return totals;
    }, {
      images: 0,
      pointsTotal: 0,
      pointsShown: 0,
      frustumsTotal: 0,
      frustumsShown: 0,
    });
  }

  function refreshInfo() {
    if (!models.size) {
      setInfo("Aucun modèle chargé");
      return;
    }

    const loaded = getTotals();
    setInfo(
      `Modèles: ${models.size.toLocaleString("fr-FR")} · ` +
      `Images: ${loaded.images.toLocaleString("fr-FR")} · ` +
      `Points: ${loaded.pointsShown.toLocaleString("fr-FR")} / ${loaded.pointsTotal.toLocaleString("fr-FR")} · ` +
      `Frustums: ${loaded.frustumsShown.toLocaleString("fr-FR")} / ${loaded.frustumsTotal.toLocaleString("fr-FR")}`
    );
  }

  function applyFlipUp(flip) {
    for (const model of models.values()) {
      model.rootEntity.setLocalEulerAngles(flip ? 180 : 0, 0, 0);
    }
  }

  function refreshUI() {
    const ready = models.size > 0;

    if (pointSize) pointSize.disabled = false;
    if (cameraSize) cameraSize.disabled = !ready;
    if (panoramaModeRow) {
      const hasPanorama = [...models.values()].some((model) =>
        [...model.cameras.values()].some(isPanorama)
      );
      panoramaModeRow.hidden = !hasPanorama;
    }
  }

  function setSliderOutput(input, suffix) {
    const output = document.getElementById(`${input.id}Value`);
    if (output) output.value = `${input.value} ${suffix}`;
  }

  // Taille des frustums en % de l'échelle actuelle ; reconstruit les frustums et recharge les images affichées.
  function setFrustumFactor(percent) {
    frustumFactor = percent / 100;
    for (const model of models.values()) {
      model.depth = model.baseDepth * frustumFactor;
      model.slots?.forEach(resetSlot);
      model.pickedKey = null;
      rebuildFrustums(model, new Set());
    }
  }

  function applySlotOpacity(material) {
    material.opacity = imageOpacity;
    material.blendType = imageOpacity < 1 ? pc.BLEND_NORMAL : pc.BLEND_NONE;
    material.depthWrite = imageOpacity >= 1;
    material.update();
  }

  function setImageOpacity(percent) {
    imageOpacity = percent / 100;
    for (const model of models.values()) {
      for (const slot of model.slots ?? []) {
        if (slot.material) applySlotOpacity(slot.material);
      }
    }
  }

  function setPanoramaMode(mode) {
    if (panoramaMode === mode) return;
    panoramaMode = mode;
    for (const model of models.values()) {
      model.slots?.forEach(resetSlot);
      rebuildFrustums(model, new Set());
    }
  }

  function setPanoramaMirror(on) {
    if (panoramaMirror === on) return;
    panoramaMirror = on;
    for (const model of models.values()) {
      model.slots?.forEach(resetSlot);
      model.pickedKey = null;
    }
  }

  function destroyMeshEntity(entity, resource) {
    if (entity?.parent) entity.parent.removeChild(entity);
    entity?.destroy();
    resource?.mesh?.destroy?.();
    resource?.material?.destroy?.();
  }

  function destroyModel(model) {
    model.unregister?.();
    for (const slot of model.slots ?? []) destroySlot(slot);
    destroyMeshEntity(model.pointsEntity, model.pointsResource);
    destroyMeshEntity(model.cameraCentersEntity, model.cameraCentersResource);
    destroyMeshEntity(model.frustumsEntity, model.frustumsResource);
    destroyMeshEntity(null, model.markersResource);
    if (model.rootEntity.parent) model.rootEntity.parent.removeChild(model.rootEntity);
    model.rootEntity.destroy();
  }

  function destroySlot(slot) {
    slot.label?.remove();
    destroyMeshEntity(slot.entity, { mesh: slot.mesh, material: slot.material });
    slot.mesh = null;
    slot.material = null;
  }

  function trimTextureCache(limit = 0) {
    const inUse = new Set(
      [...models.values()].flatMap((model) => (model.slots ?? []).map((slot) => slot.texture))
    );
    for (const [name, texture] of textureCache) {
      if (textureCache.size <= limit) break;
      if (inUse.has(texture)) continue;
      textureCache.delete(name);
      texture.destroy?.();
    }
  }

  function getCombinedBounds(flip = Boolean(getFlipUp?.())) {
    const bounds = [...models.values()].map((model) => model.bounds);
    if (!bounds.length) return null;
    return colmapBounds(
      bounds.flatMap((bound) => [bound.min, bound.max]),
      flip
    );
  }

  function removeModel(id) {
    const model = models.get(id);
    if (!model) return;
    destroyModel(model);
    models.delete(id);
    onBoundsChanged?.(models.size ? getCombinedBounds() : null, false);
    refreshUI();
    refreshInfo();
  }

  function clear() {
    for (const model of models.values()) destroyModel(model);
    models.clear();
    trimTextureCache(0);
    onBoundsChanged?.(null);
    refreshUI();
    refreshInfo();
  }

  function rebuildFrustums(model, enlarged = model.enlarged ?? new Set()) {
    model.enlarged = enlarged;
    const depthOf = (i) => model.depth * (enlarged.has(i) ? IMAGE_FRUSTUM_SCALE : 1);
    const frustums = buildFrustumLines(model.colmapImages, model.cameras, depthOf, enlarged);
    const group = makeFrustumMeshes(frustums);
    group.entity.enabled = model.frustumsEntity ? model.frustumsEntity.enabled : true;
    model.rootEntity.addChild(group.entity);
    destroyMeshEntity(model.frustumsEntity, model.frustumsResource);
    destroyMeshEntity(null, model.markersResource);
    model.frustumsEntity = group.entity;
    model.frustumsResource = group.resource;
    model.markersResource = group.markersResource;
    model.frustumsShown = frustums.shown;
  }

  function ensureSlots(model) {
    if (model.slots.length) return;
    const labelParent = document.getElementById("viewer") ?? document.body;
    for (let i = 0; i < IMAGE_SLOTS; i++) {
      const entity = new pc.Entity(`COLMAP image ${i + 1}`);
      entity.enabled = false;
      model.rootEntity.addChild(entity);
      const label = document.createElement("div");
      Object.assign(label.style, {
        position: "absolute",
        display: "none",
        pointerEvents: "none",
        transform: "translate(-100%, 0)",
        padding: "1px 4px",
        borderRadius: "3px",
        background: "rgba(0, 0, 0, 0.6)",
        color: "#fff",
        font: "11px sans-serif",
        whiteSpace: "nowrap",
        zIndex: "5",
      });
      labelParent.appendChild(label);
      model.slots.push({ entity, image: null, shown: null, texture: null, mesh: null, material: null, token: null, label });
    }
  }

  function resetSlot(slot) {
    slot.image = null;
    slot.shown = null;
    slot.texture = null;
    slot.token = {};
    slot.entity.enabled = false;
    if (slot.label) slot.label.style.display = "none";
  }

  // Projette un point du repère COLMAP vers l'écran (pixels CSS), ou null si derrière la caméra.
  function toWorld(model, point) {
    return model.rootEntity.getWorldTransform().transformPoint(
      new pc.Vec3(point[0], point[1], point[2]),
      new pc.Vec3()
    );
  }

  // Pose de l'image COLMAP en coordonnées de la vue : point de vue = centre de la caméra,
  // cible = point situé sur l'axe optique.
  function imageViewPose(model, image) {
    const rwc = transpose3(qvecToMat3(image.qvec));
    const center = getCameraCenter(image);
    const point = (v) => [center[0] + v[0], center[1] + v[1], center[2] + v[2]];
    const cameraWorld = toWorldArray(model, center);
    // Les panoramas sont dessinés avec x et z inversés (applyPanoramaYawFix) : le centre de l'image est donc en -z.
    const camera = model.cameras.get(image.cameraId);
    const axis = camera && isPanorama(camera) ? [0, 0, -1] : [0, 0, 1];
    const forward = normalize3(sub3(toWorldArray(model, point(mulMat3Vec3(rwc, axis))), cameraWorld));
    const radius = Math.max(getSceneRadius?.() ?? 1, 1e-6);
    // Léger recul derrière le centre de la caméra pour que son image reste devant l'observateur.
    const back = radius * 0.02;
    const targetDistance = radius * 0.2;
    return {
      position: cameraWorld.map((value, axis) => value - forward[axis] * back),
      target: cameraWorld.map((value, axis) => value + forward[axis] * targetDistance),
    };
  }

  function toWorldArray(model, point) {
    return toWorld(model, point).toArray();
  }

  function toScreen(model, point) {
    const out = new pc.Vec3();
    return worldToScreen(toWorld(model, point), out) ? out : null;
  }

  // Taille à l'écran (px) du cadre nominal d'une caméra, mesurée au centre du cadre :
  // fonctionne même quand ses coins sont derrière le point de vue. 0 si le cadre est derrière.
  function nominalScreenSize(model, index) {
    const image = model.colmapImages[index];
    const camera = model.cameras.get(image.cameraId);
    if (!camera) return 0;
    // Indice 0 = sommet de la pyramide ; 1..4 = coins du cadre.
    const frame = frustumCorners(image, camera, model.depth * IMAGE_FRUSTUM_SCALE)
      .slice(1)
      .map((c) => toWorld(model, c));
    const center = frame
      .reduce((acc, c) => acc.add(c), new pc.Vec3())
      .mulScalar(1 / frame.length);
    const extent = Math.max(frame[0].distance(frame[1]), frame[1].distance(frame[2]));
    return extent * pixelScaleAt(center);
  }

  // Replace sur le cadre agrandi les libellés des images affichées (coin supérieur droit).
  function updateSlotLabels(model) {
    const glCanvas = app.graphicsDevice.canvas;
    for (const slot of model.slots) {
      if (!slot.label) continue;
      const image = slot.shown === null ? null : model.colmapImages[slot.shown];
      const camera = image && model.cameras.get(image.cameraId);
      // Coin 2 de frustumCorners : (+x, -y) soit le coin haut-droit de l'image (y vers le bas).
      const corner = camera ? frustumCorners(image, camera, model.depth * IMAGE_FRUSTUM_SCALE)[2] : null;
      const s = corner && toScreen(model, corner);
      if (!s) {
        slot.label.style.display = "none";
        continue;
      }
      slot.label.textContent = image.name;
      slot.label.style.left = `${glCanvas.offsetLeft + s.x}px`;
      slot.label.style.top = `${glCanvas.offsetTop + s.y}px`;
      slot.label.style.display = "block";
    }
  }

  // Agrandit uniquement les cadres des images réellement affichées.
  function refreshEnlarged(model) {
    const shown = new Set(model.slots.map((slot) => slot.shown).filter((i) => i !== null));
    const previous = model.enlarged ?? new Set();
    const same = shown.size === previous.size && [...shown].every((i) => previous.has(i));
    if (!same) rebuildFrustums(model, shown);
  }

  async function getTexture(name) {
    const cached = textureCache.get(name);
    if (cached) {
      textureCache.delete(name);
      textureCache.set(name, cached);
      return cached;
    }
    const file = imageIndex && findImageFile(imageIndex, name);
    if (!file) {
      console.warn("[images] fichier introuvable dans le dossier déposé :", name);
      return null;
    }
    const bitmap = await createImageBitmap(file.blob ?? file, {
      resizeWidth: IMAGE_MAX_WIDTH,
      resizeQuality: "medium",
    });
    const texture = new pc.Texture(app.graphicsDevice, {
      width: bitmap.width,
      height: bitmap.height,
      format: pc.PIXELFORMAT_RGBA8,
      mipmaps: false,
      addressU: pc.ADDRESS_CLAMP_TO_EDGE,
      addressV: pc.ADDRESS_CLAMP_TO_EDGE,
    });
    texture.setSource(bitmap);
    bitmap.close?.();
    textureCache.set(name, texture);
    trimTextureCache(TEXTURE_CACHE_SIZE);
    return texture;
  }

  async function getCubeTexture(name) {
    const key = `${name}|cube|${panoramaMirror ? "mirror" : "direct"}`;
    const cached = textureCache.get(key);
    if (cached) {
      textureCache.delete(key);
      textureCache.set(key, cached);
      return cached;
    }
    const file = imageIndex && findImageFile(imageIndex, name);
    if (!file) {
      console.warn("[images] fichier introuvable dans le dossier déposé :", name);
      return null;
    }
    const bitmap = await createImageBitmap(file.blob ?? file, {
      resizeWidth: PANORAMA_CUBE_SOURCE_WIDTH,
      resizeQuality: "medium",
    });
    const source = document.createElement("canvas");
    source.width = bitmap.width;
    source.height = bitmap.height;
    const sourceContext = source.getContext("2d", { willReadFrequently: true });
    sourceContext.drawImage(bitmap, 0, 0);
    bitmap.close?.();
    const pixels = sourceContext.getImageData(0, 0, source.width, source.height).data;
    const atlas = equirectToCubeAtlas(pixels, source.width, source.height, undefined, panoramaMirror);
    const atlasCanvas = document.createElement("canvas");
    atlasCanvas.width = atlas.width;
    atlasCanvas.height = atlas.height;
    atlasCanvas.getContext("2d").putImageData(new ImageData(atlas.data, atlas.width, atlas.height), 0, 0);
    const texture = new pc.Texture(app.graphicsDevice, {
      width: atlas.width,
      height: atlas.height,
      format: pc.PIXELFORMAT_RGBA8,
      mipmaps: false,
      addressU: pc.ADDRESS_CLAMP_TO_EDGE,
      addressV: pc.ADDRESS_CLAMP_TO_EDGE,
    });
    texture.setSource(atlasCanvas);
    textureCache.set(key, texture);
    trimTextureCache(TEXTURE_CACHE_SIZE);
    return texture;
  }

  async function showSlotImage(model, slot, index) {
    const token = {};
    slot.token = token;
    const image = model.colmapImages[index];
    const camera = model.cameras.get(image.cameraId);
    if (!camera) {
      console.warn("[images] caméra absente pour l'image", image.name, image.cameraId);
      return;
    }

    const cube = isPanorama(camera) && panoramaMode === "cube";
    let texture = null;
    try {
      texture = cube ? await getCubeTexture(image.name) : await getTexture(image.name);
    } catch (error) {
      console.warn("[COLMAP] Image non chargée :", image.name, error);
      return;
    }
    if (!texture || disposed || slot.token !== token) return;
    console.log("[images] image affichée", { name: image.name, texture: texture.width + "x" + texture.height });

    // Quad posé sur le plan lointain du frustum, sphère ou cube pour un panorama 360°, texturé avec l'image.
    const mesh = new pc.Mesh(app.graphicsDevice);
    const radius = model.depth * IMAGE_FRUSTUM_SCALE * (isPanorama(camera) ? PANORAMA_SIZE_FACTOR : 1);
    let geometry;
    if (cube) {
      geometry = panoramaCube(image, radius);
    } else if (isPanorama(camera)) {
      geometry = panoramaSphere(image, radius, panoramaMirror);
    } else {
      const quad = frustumCorners(image, camera, radius).slice(1);
      geometry = {
        positions: new Float32Array(quad.flat()),
        uvs: new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]),
        indices: new Uint16Array([0, 1, 2, 0, 2, 3]),
      };
    }
    mesh.setPositions(geometry.positions);
    mesh.setUvs(0, geometry.uvs);
    mesh.setIndices(geometry.indices);
    mesh.update(pc.PRIMITIVE_TRIANGLES, true);

    if (!slot.material) {
      slot.material = new pc.StandardMaterial();
      slot.material.useLighting = false;
      slot.material.emissive = new pc.Color(1, 1, 1);
      slot.material.cull = pc.CULLFACE_NONE;
    }
    slot.material.emissiveMap = texture;
    applySlotOpacity(slot.material);

    const meshInstance = new pc.MeshInstance(mesh, slot.material);
    meshInstance.cull = false;
    if (slot.entity.render) {
      slot.entity.render.meshInstances = [meshInstance];
    } else {
      slot.entity.addComponent("render", {
        type: "asset",
        meshInstances: [meshInstance],
        layers: [getImageLayer(app).id],
      });
    }
    slot.entity.enabled = true;
    slot.texture = texture;
    slot.shown = index;
    slot.mesh?.destroy?.();
    slot.mesh = mesh;
    refreshEnlarged(model);
  }

  function assignSlots(model, selected) {
    model.slots.forEach((slot, i) => {
      const index = selected[i];
      if (index === undefined) {
        resetSlot(slot);
        return;
      }
      if (slot.image === index) return;
      slot.image = index;
      showSlotImage(model, slot, index);
    });
  }

  function setImagesMode(model, on) {
    if (model.imagesOn === on) return;
    model.imagesOn = on;
    console.log("[images] mode images", on ? "activé" : "désactivé", "modèle", model.label, "images associées :", model.imagesMatched);
    if (on) {
      ensureSlots(model);
    } else {
      model.slots.forEach(resetSlot);
    }
    rebuildFrustums(model, new Set());
  }

  // Appelée à chaque frame : sélectionne les caméras dont le cadre atteint IMAGE_MIN_SCREEN_PX.
  function frame() {
    if (disposed || !getViewerPosition) return;
    for (const model of models.values()) {
      if (!model.imagesOn) {
        if (!model.idleLogged) {
          model.idleLogged = true;
          console.log("[images] mode Images désactivé pour", model.label, "(cocher la case Images du modèle)");
        }
        continue;
      }
      const p = getViewerPosition();
      // Le modèle est tourné de 180° autour de X quand « flipUp » est actif.
      const flip = getFlipUp?.();
      const viewer = flip ? [p[0], -p[1], -p[2]] : [p[0], p[1], p[2]];
      const look = getViewerDirection?.() ?? [0, 0, -1];
      const forward = flip ? [look[0], -look[1], -look[2]] : look;
      const isPano = (index) => {
        const camera = model.cameras.get(model.colmapImages[index].cameraId);
        return !!camera && isPanorama(camera);
      };
      const stats = { total: 0, inCone: 0 };
      // Mode photographe : la caméra la plus proche du point de vue, qui regarde dans le sens de la vue.
      const picked = selectNearestImages({
        centers: model.cameraCenters,
        axes: model.axes,
        viewer,
        direction: forward,
        maxAlong: Infinity,
        count: IMAGE_CANDIDATES,
        maxAngleDeg: 180,
        refAxis: forward,
        maxRefAngleDeg: IMAGE_AXIS_DEG,
        rankByDistance: true,
        accept: (i, relaxed = false) => {
          if (isPano(i) || !imageInFrontOfViewer(model, i, viewer, forward)) return false;
          const size = nominalScreenSize(model, i);
          if (!relaxed) stats.maxSize = Math.max(stats.maxSize ?? 0, size);
          const dist = Math.hypot(
            viewer[0] - model.cameraCenters[i * 3],
            viewer[1] - model.cameraCenters[i * 3 + 1],
            viewer[2] - model.cameraCenters[i * 3 + 2]
          );
          if (!stats.nearest || dist < stats.nearest.dist) {
            stats.nearest = { name: model.colmapImages[i].name, dist, size, index: i };
          }
          return size >= IMAGE_MIN_SCREEN_PX * (relaxed ? IMAGE_HYSTERESIS : 1);
        },
        stats,
      });
      // Garde l'image courante tant que son cadre agrandi couvre le centre de la vue ; sinon bascule sur la
      // meilleure candidate qui le couvre. Sans candidate couvrante, garde l'image courante si elle est encore devant.
      const current = model.slots[0]?.shown ?? null;
      // Panoramas : le plus centré dans la vue l'emporte ; l'image courante n'est gardée que si elle reste assez centrée.
      let bestPano = null;
      for (let i = 0; i < model.colmapImages.length; i++) {
        if (!isPano(i)) continue;
        const view = panoramaView(model, i, viewer, forward);
        if (view.covers && (bestPano === null || view.angle < bestPano.angle)) bestPano = { index: i, angle: view.angle };
      }
      let panoPick = bestPano?.index ?? null;
      if (bestPano !== null && current !== null && isPano(current)) {
        const cur = panoramaView(model, current, viewer, forward);
        if (cur.covers && cur.angle <= bestPano.angle + (PANORAMA_HYSTERESIS_DEG * Math.PI) / 180) panoPick = current;
      }
      const pinholeCurrent = current !== null && !isPano(current) ? current : null;
      const covers = (index) => {
        const image = model.colmapImages[index];
        const camera = model.cameras.get(image.cameraId);
        return !!camera && coversViewCenter(image, camera, model.depth * IMAGE_FRUSTUM_SCALE, viewer, forward);
      };
      const coveringBest = picked.find(covers) ?? null;
      let chosen = picked.slice(0, IMAGE_SHOWN);
      if (panoPick !== null) chosen = [panoPick];
      else if (pinholeCurrent !== null && covers(pinholeCurrent)) chosen = [pinholeCurrent];
      else if (coveringBest !== null) chosen = [coveringBest];
      else if (
        pinholeCurrent !== null &&
        imageInFrontOfViewer(model, pinholeCurrent, viewer, forward) &&
        viewerOnApexSide(model.colmapImages[pinholeCurrent], model.depth * IMAGE_FRUSTUM_SCALE, viewer)
      ) chosen = [pinholeCurrent];
      const key = chosen.join(",");
      if (key !== model.pickedKey) {
        model.pickedKey = key;
        console.log(
          "[images] caméras retenues :", chosen.length, "/", IMAGE_SLOTS,
          `| ${stats.total} caméras, ${stats.inCone} dans le cône 35°, taille max ${(stats.maxSize ?? 0).toFixed(1)} px, cadre ≥ ${IMAGE_MIN_SCREEN_PX} px`,
          "| point de vue", viewer.map((v) => v.toFixed(2))
        );
        if (stats.nearest) {
          const n = stats.nearest;
          const c = model.cameraCenters;
          console.log(
            "[images] caméra la plus proche (dans le cône) :", n.name,
            "| distance", n.dist.toFixed(3), "| cadre", n.size.toFixed(1), "px",
            "| centre", [c[n.index * 3], c[n.index * 3 + 1], c[n.index * 3 + 2]].map((v) => v.toFixed(3)),
            "| profondeur cadre", model.depth.toFixed(4)
          );
        }
      }
      assignSlots(model, chosen);
      refreshEnlarged(model);
      updateSlotLabels(model);
    }
    // Libère les textures des images qui ne sont plus dans la pile.
    trimTextureCache(TEXTURE_CACHE_SIZE);
  }

  function registerModel(model) {
    model.unregister?.();
    model.unregister = null;
    const { id, label } = model;

    const children = [
      {
        name: "Points",
        visible: false,
        setVisible(visible) { model.pointsEntity.enabled = visible; },
        setHighlight(on) { setMeshHighlight(model.pointsResource, on); },
      },
      {
        name: "Caméras",
        visible: false,
        setVisible(visible) { model.cameraCentersEntity.enabled = visible; },
        setHighlight(on) { setMeshHighlight(model.cameraCentersResource, on); },
      },
      {
        name: "Frustums",
        setVisible(visible) { model.frustumsEntity.enabled = visible; },
        setHighlight(on) {
          setMeshHighlight(model.frustumsResource, on);
          setMeshHighlight(model.markersResource, on);
        },
      },
    ];
    if (imageIndex) {
      children.push({
        name: "Images",
        visible: model.imagesOn,
        setVisible(visible) { setImagesMode(model, visible); },
        setHighlight() {},
      });
    }

    if (registerGroup) {
      model.unregister = registerGroup({
        id,
        name: `COLMAP · ${label}`,
        compact: true,
        children,
        onRemove: () => removeModel(id),
      });
    } else {
      const unregister = children.map((child) => registerLayer?.({
        id: `${id}-${child.name.toLowerCase().replaceAll(" ", "-")}`,
        name: `${child.name} · ${label}`,
        setVisible: child.setVisible,
      })).filter(Boolean);
      model.unregister = () => unregister.forEach((remove) => remove());
    }
  }

  async function setImageFiles(files) {
    if (disposed) return;
    const index = indexImageFiles(files);
    const unique = new Set(index.values()).size;
    if (!unique) {
      setStatus("Aucune image .jpg/.png trouvée dans le dossier déposé.", true);
      return;
    }

    imageIndex = index;
    console.log("[images] dossier indexé :", index.size, "fichiers image");
    for (const model of models.values()) {
      model.slots.forEach(resetSlot);
      refreshEnlarged(model);
      model.imagesMatched = model.colmapImages.filter((image) => findImageFile(index, image.name)).length;
      if (model.imagesMatched) setImagesMode(model, true);
    }
    trimTextureCache(0);
    for (const model of models.values()) registerModel(model);
    refreshInfo();

    if (!models.size) {
      setStatus(`${unique.toLocaleString("fr-FR")} images prêtes. Déposez maintenant le modèle COLMAP.`, "success");
      return;
    }
    const matched = [...models.values()].reduce((sum, model) => sum + model.imagesMatched, 0);
    const total = getTotals().images;
    setStatus(
      `Images : ${matched.toLocaleString("fr-FR")} / ${total.toLocaleString("fr-FR")} caméras associées.`,
      matched ? "success" : true
    );
  }

  function buildCameraCenters(images) {
    const out = new Float32Array(images.length * 3);

    for (let i = 0; i < images.length; i++) {
      const c = getCameraCenter(images[i]);
      out[i * 3 + 0] = c[0];
      out[i * 3 + 1] = c[1];
      out[i * 3 + 2] = c[2];
    }

    return out;
  }

  function buildFrustumLines(images, cameras, depthOf, enlarged) {
    const maxFrustums = 2000;
    const step = Math.max(
      1,
      Math.floor(images.length / Math.max(1, maxFrustums))
    );

    // Les images affichées sont toujours tracées, même hors du sous-échantillonnage.
    const indices = new Set();
    for (let i = 0; i < images.length; i += step) indices.add(i);
    for (const i of enlarged) indices.add(i);

    const positions = [];
    const markers = [];
    let shown = 0;
    const cube = panoramaMode === "cube";

    for (const i of indices) {
      const image = images[i];
      const camera = cameras.get(image.cameraId);
      if (!camera) continue;

      if (isPanorama(camera)) {
        const radius = depthOf(i) * (enlarged.has(i) ? PANORAMA_SIZE_FACTOR : PANORAMA_MARKER_FACTOR);
        appendPanoramaMarker(markers, getCameraCenter(image), radius, cube);
        shown++;
        continue;
      }

      const [p0, p1, p2, p3, p4] = frustumCorners(image, camera, depthOf(i));

      const segments = [
        p0, p1, p0, p2, p0, p3, p0, p4,
        p1, p2, p2, p3, p3, p4, p4, p1,
      ];

      for (const p of segments) {
        positions.push(p[0], p[1], p[2]);
      }

      shown++;
    }

    return {
      positions: new Float32Array(positions),
      markers,
      shown,
      total: images.length,
    };
  }

  // Frustums (lignes) et marqueurs de panoramas (sphères ou cubes), ce dernier en entité enfant.
  function makeFrustumMeshes(frustums) {
    const entity = new pc.Entity("COLMAP frustums");
    let resource = null;
    if (frustums.positions.length) {
      resource = makeLineMesh(app, frustums.positions, [1, 0, 0, 1], { onTop: true });
      entity.addComponent("render", {
        type: "asset",
        meshInstances: [resource.meshInstance],
        layers: [getOverlayLayer(app).id],
      });
    }
    let markersResource = null;
    if (frustums.markers.length) {
      const count = frustums.markers.length / 3;
      const rgba = new Float32Array(count * 4);
      for (let i = 0; i < count; i++) rgba.set([1, 0, 0, 1], i * 4);
      markersResource = makeColmapMesh(
        app,
        new Float32Array(frustums.markers),
        rgba,
        pc.PRIMITIVE_LINES,
        makeColmapMaterial(app, { onTop: true })
      );
      const markersEntity = new pc.Entity("COLMAP panorama markers");
      markersEntity.addComponent("render", {
        type: "asset",
        meshInstances: [markersResource.meshInstance],
        layers: [getOverlayLayer(app).id],
      });
      entity.addChild(markersEntity);
    }
    return { entity, resource, markersResource };
  }

  async function loadCandidate(candidate) {
    const [cameraBuffer, imagesBuffer, pointsBuffer] = await Promise.all([
      candidate.cameras.arrayBuffer(),
      candidate.images.arrayBuffer(),
      candidate.points.arrayBuffer(),
    ]);
    if (disposed) return null;

    const cameras = parseCamerasBin(cameraBuffer);
    const images = parseImagesBin(imagesBuffer);
    const points = parsePoints3DBin(pointsBuffer, 300000);
    const cameraCenters = buildCameraCenters(images);
    const modelBounds = colmapBounds([points.positions, cameraCenters]);
    if (!modelBounds) {
      throw new Error(`Le modèle COLMAP ${candidate.dir || "(racine)"} ne contient aucune position valide.`);
    }
    const depth = cameraFrustumDepth(cameraCenters, getSceneRadius?.());
    const frustums = buildFrustumLines(images, cameras, () => depth * frustumFactor, new Set());
    const id = `colmap-${nextModelId++}`;
    const rootEntity = new pc.Entity(`COLMAP sparse ${candidate.dir || id}`);
    const label = candidate.dir.split("/").filter(Boolean).slice(-3).join("/") || "racine";
    const extent = modelBounds.max.map((value, axis) => value - modelBounds.min[axis]);
    const model = {
      id,
      label,
      bounds: modelBounds,
      images: images.length,
      pointsTotal: points.originalCount,
      pointsShown: points.shownCount,
      frustumsTotal: frustums.total,
      frustumsShown: frustums.shown,
      rootEntity,
      pivotPositions: points.positions,
      colmapImages: images,
      cameras,
      depth: depth * frustumFactor,
      baseDepth: depth,
      cameraCenters,
      axes: buildCameraAxes(images, cameras),
      imagesOn: false,
      imagesMatched: imageIndex ? images.filter((image) => findImageFile(imageIndex, image.name)).length : 0,
      slots: [],
    };
    app.root.addChild(rootEntity);
    try {
      model.pointsResource = makePointMesh(
        app, points.positions, points.colors, Number(pointSize?.value ?? 2), { onTop: true }
      );
      model.pointsEntity = new pc.Entity("COLMAP points");
      model.pointsEntity.addComponent("render", {
        type: "asset",
        meshInstances: [model.pointsResource.meshInstance],
        layers: [getOverlayLayer(app).id],
      });
      rootEntity.addChild(model.pointsEntity);

      const centerColors = new Float32Array(cameraCenters.length);
      for (let i = 0; i < cameraCenters.length / 3; i++) {
        centerColors[i * 3] = 1;
      }
      model.cameraCentersResource = makePointMesh(
        app, cameraCenters, centerColors, Number(cameraSize?.value ?? 4), { onTop: true }
      );
      model.cameraCentersEntity = new pc.Entity("COLMAP camera centers");
      model.cameraCentersEntity.addComponent("render", {
        type: "asset",
        meshInstances: [model.cameraCentersResource.meshInstance],
        layers: [getOverlayLayer(app).id],
      });
      rootEntity.addChild(model.cameraCentersEntity);

      const group = makeFrustumMeshes(frustums);
      model.frustumsResource = group.resource;
      model.markersResource = group.markersResource;
      model.frustumsEntity = group.entity;
      rootEntity.addChild(model.frustumsEntity);
      rootEntity.setLocalEulerAngles(getFlipUp?.() ? 180 : 0, 0, 0);

      if (model.imagesMatched) setImagesMode(model, true);
      registerModel(model);
      models.set(id, model);
      return model;
    } catch (error) {
      destroyModel(model);
      throw error;
    }
  }

  async function loadFromFileList(fileList) {
    if (disposed || !fileList?.length) return;
    const groups = new Map();
    for (const file of Array.from(fileList)) {
      const rel = (file.webkitRelativePath || file.name).replace(/\\/g, "/");
      const idx = rel.lastIndexOf("/");
      const dir = idx >= 0 ? rel.slice(0, idx) : "";
      const base = idx >= 0 ? rel.slice(idx + 1) : rel;
      if (!groups.has(dir)) groups.set(dir, {});
      const group = groups.get(dir);
      if (base === "cameras.bin") group.cameras = file;
      if (base === "images.bin") group.images = file;
      if (base === "points3D.bin") group.points = file;
      if (base === "gauge.txt") group.gauge = file;
    }
    const findGauge = (dir) => {
      let current = dir;
      while (true) {
        const gauge = groups.get(current)?.gauge;
        if (gauge) return gauge;
        if (!current) return null;
        const idx = current.lastIndexOf("/");
        current = idx >= 0 ? current.slice(0, idx) : "";
      }
    };
    const candidates = [...groups.entries()]
      .filter(([, group]) => group.cameras && group.images && group.points)
      .map(([dir, group]) => ({ dir, ...group, gauge: findGauge(dir) }))
      .sort((a, b) => a.dir.localeCompare(b.dir));

    if (!candidates.length) {
      setStatus(
        "Aucun sous-dossier valide trouvé (cameras.bin + images.bin + points3D.bin).",
        true
      );
      return;
    }

    const shouldFrame = models.size === 0;
    let added = 0;
    let gaugeFile = null;
    try {
      for (const candidate of candidates) {
        setStatus(`Chargement COLMAP : ${candidate.dir || "(racine)"}`);
        if (await loadCandidate(candidate)) {
          added++;
          if (candidate.gauge) gaugeFile = candidate.gauge;
        }
      }
      if (!added) return;
      if (gaugeFile && onGauge) {
        try {
          await onGauge(gaugeFile);
        } catch (error) {
          console.warn("[colmap] gauge.txt ignoré :", error);
        }
      }
      refreshUI();
      refreshInfo();
      onBoundsChanged?.(getCombinedBounds(), shouldFrame);
      const loaded = getTotals();
      setStatus(
        `${added} modèle(s) COLMAP ajouté(s) : ${loaded.images.toLocaleString("fr-FR")} caméras, ` +
        `${loaded.pointsShown.toLocaleString("fr-FR")} points affichés.`,
        "success"
      );
    } catch (error) {
      console.error("[COLMAP] Erreur d’import :", error);
      refreshUI();
      refreshInfo();
      onBoundsChanged?.(getCombinedBounds(), false);
      setStatus(`Import COLMAP impossible : ${error?.message || error}`, true);
    }
  }

    if (!input) {
      throw new Error("[COLMAP] #colmapFolderInput introuvable.");
    }

    listen(input, "change", async () => {
      const files = Array.from(input.files ?? []);

      console.log("[COLMAP] Sélection reçue", {
        disposed,
        count: files.length,
      });

      try {
        await loadFromFileList(files);
      } catch (error) {
        console.error("[COLMAP] Erreur du gestionnaire :", error);
        setStatus(`Import COLMAP impossible : ${error.message}`, true);
      } finally {
        input.value = "";
      }
    });

  listen(pointSize, "input", refreshPointSizes);
  listen(cameraSize, "input", refreshPointSizes);
  listen(frustumSizeInput, "input", () => {
    setSliderOutput(frustumSizeInput, "%");
    setFrustumFactor(Number(frustumSizeInput.value));
  });
  listen(imageTransparencyInput, "input", () => {
    setSliderOutput(imageTransparencyInput, "%");
    setImageOpacity(Number(imageTransparencyInput.value));
  });
  refreshPointSizes();

  refreshUI();
  refreshInfo();

  if (panoramaModeSelect) {
    const onChange = () => setPanoramaMode(panoramaModeSelect.value);
    panoramaModeSelect.addEventListener("change", onChange);
    removers.push(() => panoramaModeSelect.removeEventListener("change", onChange));
  }
  if (panoramaMirrorInput) {
    const onChange = () => setPanoramaMirror(panoramaMirrorInput.checked);
    panoramaMirrorInput.addEventListener("change", onChange);
    removers.push(() => panoramaMirrorInput.removeEventListener("change", onChange));
  }

  return {
    loadFromFileList,
    setImageFiles,
    frame,
    clear,
    refreshUI,

    getShownImagePose() {
      for (const model of models.values()) {
        const index = model.slots[0]?.shown;
        if (index === null || index === undefined) continue;
        return imageViewPose(model, model.colmapImages[index]);
      }
      return null;
    },

    getPivotSources() {
      return [...models.values()]
        .filter((model) => model.rootEntity?.enabled && model.pivotPositions)
        .map((model) => ({ label: "COLMAP", positions: model.pivotPositions, entity: model.rootEntity }));
    },

    setFlipUp(flip) {
      applyFlipUp(Boolean(flip));
      if (models.size) onBoundsChanged?.(getCombinedBounds(Boolean(flip)), false);
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      for (const remove of removers) remove();
      removers.length = 0;
      clear();
    },
  };
}
