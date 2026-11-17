import * as pc from "playcanvas";
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
// Hystérésis : une image affichée reste choisie tant que le cadre reste ≥ 60 % du seuil et dans un cône élargi.
const IMAGE_HYSTERESIS = 0.6;
const IMAGE_SLOTS = 10;
const IMAGE_MAX_WIDTH = 1024;
const TEXTURE_CACHE_SIZE = IMAGE_SLOTS;

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
  material.depthTest = !onTop;
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

export function getOverlayLayer(app) {
  const existing = app.scene.layers.getLayerByName("Overlay");
  if (existing) return existing;
  // Calque ajouté après « World » (splats) : il est dessiné par-dessus.
  const layer = new pc.Layer({ name: "Overlay" });
  app.scene.layers.push(layer);
  for (const camera of app.root.findComponents("camera")) {
    if (!camera.layers.includes(layer.id)) camera.layers = [...camera.layers, layer.id];
  }
  return layer;
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

function makeLineMesh(app, positions, color = [1, 0, 0, 1]) {
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
    makeColmapMaterial(app)
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

function buildCameraAxes(images) {
  const out = new Float32Array(images.length * 3);
  for (let i = 0; i < images.length; i++) {
    const axis = mulMat3Vec3(transpose3(qvecToMat3(images[i].qvec)), [0, 0, 1]);
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
}) {
  const sceneColmapInfo = document.getElementById("sceneColmapInfo");
  const input = document.getElementById("colmapFolderInput");
  const pointSize = document.getElementById("colmapPointSize");
  const cameraSize = document.getElementById("colmapCameraSize");

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

    if (pointSize) pointSize.disabled = !ready;
    if (cameraSize) cameraSize.disabled = !ready;
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
    const resource = makeLineMesh(app, frustums.positions, [1, 0, 0, 1]);
    const entity = new pc.Entity("COLMAP frustums");
    entity.addComponent("render", {
      type: "asset",
      meshInstances: [resource.meshInstance],
    });
    entity.enabled = model.frustumsEntity ? model.frustumsEntity.enabled : true;
    model.rootEntity.addChild(entity);
    destroyMeshEntity(model.frustumsEntity, model.frustumsResource);
    model.frustumsEntity = entity;
    model.frustumsResource = resource;
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

  async function showSlotImage(model, slot, index) {
    const token = {};
    slot.token = token;
    const image = model.colmapImages[index];
    const camera = model.cameras.get(image.cameraId);
    if (!camera) {
      console.warn("[images] caméra absente pour l'image", image.name, image.cameraId);
      return;
    }

    let texture = null;
    try {
      texture = await getTexture(image.name);
    } catch (error) {
      console.warn("[COLMAP] Image non chargée :", image.name, error);
      return;
    }
    if (!texture || disposed || slot.token !== token) return;
    console.log("[images] image affichée", { name: image.name, texture: texture.width + "x" + texture.height });

    // Quad posé sur le plan lointain du frustum, texturé avec l'image.
    const quad = frustumCorners(image, camera, model.depth * IMAGE_FRUSTUM_SCALE).slice(1);
    const mesh = new pc.Mesh(app.graphicsDevice);
    mesh.setPositions(new Float32Array(quad.flat()));
    mesh.setUvs(0, new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]));
    mesh.setIndices(new Uint16Array([0, 1, 2, 0, 2, 3]));
    mesh.update(pc.PRIMITIVE_TRIANGLES, true);

    if (!slot.material) {
      slot.material = new pc.StandardMaterial();
      slot.material.useLighting = false;
      slot.material.emissive = new pc.Color(1, 1, 1);
      slot.material.cull = pc.CULLFACE_NONE;
    }
    slot.material.emissiveMap = texture;
    slot.material.update();

    const meshInstance = new pc.MeshInstance(mesh, slot.material);
    meshInstance.cull = false;
    if (slot.entity.render) {
      slot.entity.render.meshInstances = [meshInstance];
    } else {
      slot.entity.addComponent("render", { type: "asset", meshInstances: [meshInstance] });
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
      const viewer = getFlipUp?.() ? [p[0], -p[1], -p[2]] : [p[0], p[1], p[2]];
      // Direction point de vue -> centre de scène (repli : axe de la caméra).
      const t = getViewerTarget?.() ?? null;
      const flip = getFlipUp?.();
      const target = t ? (flip ? [t[0], -t[1], -t[2]] : t) : null;
      let direction = target
        ? [target[0] - viewer[0], target[1] - viewer[1], target[2] - viewer[2]]
        : null;
      const len = direction ? Math.hypot(...direction) : 0;
      if (len > 1e-9) {
        direction = direction.map((v) => v / len);
      } else {
        const d = getViewerDirection?.() ?? [0, 0, -1];
        direction = flip ? [d[0], -d[1], -d[2]] : d;
      }
      const stats = { total: 0, inCone: 0 };
      const picked = selectNearestImages({
        centers: model.cameraCenters,
        axes: model.axes,
        viewer,
        direction,
        maxAlong: target ? Math.hypot(target[0] - viewer[0], target[1] - viewer[1], target[2] - viewer[2]) : Infinity,
        count: IMAGE_SHOWN,
        maxAngleDeg: 35,
        keep: model.slots[0]?.shown ?? -1,
        accept: (i, relaxed = false) => {
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
      const key = picked.join(",");
      if (key !== model.pickedKey) {
        model.pickedKey = key;
        console.log(
          "[images] caméras retenues :", picked.length, "/", IMAGE_SLOTS,
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
      assignSlots(model, picked);
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
        setVisible(visible) { model.pointsEntity.enabled = visible; },
        setHighlight(on) { setMeshHighlight(model.pointsResource, on); },
      },
      {
        name: "Caméras",
        setVisible(visible) { model.cameraCentersEntity.enabled = visible; },
        setHighlight(on) { setMeshHighlight(model.cameraCentersResource, on); },
      },
      {
        name: "Frustums",
        setVisible(visible) { model.frustumsEntity.enabled = visible; },
        setHighlight(on) { setMeshHighlight(model.frustumsResource, on); },
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
    let shown = 0;

    for (const i of indices) {
      const image = images[i];
      const camera = cameras.get(image.cameraId);
      if (!camera) continue;

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
      shown,
      total: images.length,
    };
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
    const frustums = buildFrustumLines(images, cameras, () => depth, new Set());
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
      colmapImages: images,
      cameras,
      depth,
      cameraCenters,
      axes: buildCameraAxes(images),
      imagesOn: false,
      imagesMatched: imageIndex ? images.filter((image) => findImageFile(imageIndex, image.name)).length : 0,
      slots: [],
    };
    app.root.addChild(rootEntity);
    try {
      model.pointsResource = makePointMesh(
        app, points.positions, points.colors, Number(pointSize?.value ?? 2)
      );
      model.pointsEntity = new pc.Entity("COLMAP points");
      model.pointsEntity.addComponent("render", {
        type: "asset",
        meshInstances: [model.pointsResource.meshInstance],
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

      model.frustumsResource = makeLineMesh(app, frustums.positions, [1, 0, 0, 1]);
      model.frustumsEntity = new pc.Entity("COLMAP frustums");
      model.frustumsEntity.addComponent("render", {
        type: "asset",
        meshInstances: [model.frustumsResource.meshInstance],
      });
      rootEntity.addChild(model.frustumsEntity);
      rootEntity.setLocalEulerAngles(getFlipUp?.() ? 180 : 0, 0, 0);

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
    }
    const candidates = [...groups.entries()]
      .filter(([, group]) => group.cameras && group.images && group.points)
      .map(([dir, group]) => ({ dir, ...group }))
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
    try {
      for (const candidate of candidates) {
        setStatus(`Chargement COLMAP : ${candidate.dir || "(racine)"}`);
        if (await loadCandidate(candidate)) added++;
      }
      if (!added) return;
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
  refreshPointSizes();

  refreshUI();
  refreshInfo();

  return {
    loadFromFileList,
    setImageFiles,
    frame,
    clear,
    refreshUI,

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
