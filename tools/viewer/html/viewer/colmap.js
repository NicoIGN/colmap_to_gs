import * as pc from "playcanvas";

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
  const radius = Math.hypot(...spans) / 2;
  const reference = radius > 0 ? radius : fallbackRadius;
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

function makeColmapMaterial(app, { points = false, pointSize = 3 } = {}) {
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

      varying vec4 vColor;

      void main(void) {
        vColor = vertex_color;
        gl_Position =
          matrix_viewProjection *
          matrix_model *
          vec4(vertex_position, 1.0);
        gl_PointSize = uPointSize;
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

  material.cull = pc.CULLFACE_NONE;
  material.depthTest = true;
  material.depthWrite = false;
  material.blendType = pc.BLEND_NONE;
  material.update();

  return material;
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

function makePointMesh(app, positions, colors, pointSize = 3) {
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
    makeColmapMaterial(app, { points: true, pointSize })
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

export function createColmapLayer({
  app,
  setStatus,
  getSceneRadius,
  getFlipUp,
  registerLayer,
  registerGroup,
  onBoundsChanged,
}) {
  const sceneColmapInfo = document.getElementById("sceneColmapInfo");
  const input = document.getElementById("colmapFolderInput");
  const pointSize = document.getElementById("colmapPointSize");
  const cameraSize = document.getElementById("colmapCameraSize");
  const removeBtn = document.getElementById("colmapRemove");

  let disposed = false;
  let nextModelId = 1;
  const models = new Map();
  const removers = [];

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
    if (removeBtn) removeBtn.disabled = !ready;
  }

  function destroyMeshEntity(entity, resource) {
    if (entity?.parent) entity.parent.removeChild(entity);
    entity?.destroy();
    resource?.mesh?.destroy?.();
    resource?.material?.destroy?.();
  }

  function destroyModel(model) {
    model.unregister?.();
    destroyMeshEntity(model.pointsEntity, model.pointsResource);
    destroyMeshEntity(model.cameraCentersEntity, model.cameraCentersResource);
    destroyMeshEntity(model.frustumsEntity, model.frustumsResource);
    if (model.rootEntity.parent) model.rootEntity.parent.removeChild(model.rootEntity);
    model.rootEntity.destroy();
  }

  function getCombinedBounds(flip = Boolean(getFlipUp?.())) {
    const bounds = [...models.values()].map((model) => model.bounds);
    if (!bounds.length) return null;
    return colmapBounds(
      bounds.flatMap((bound) => [bound.min, bound.max]),
      flip
    );
  }

  function clear() {
    for (const model of models.values()) destroyModel(model);
    models.clear();
    onBoundsChanged?.(null);
    refreshUI();
    refreshInfo();
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

  function buildFrustumLines(images, cameras, near) {
    const maxFrustums = 2000;
    const step = Math.max(
      1,
      Math.floor(images.length / Math.max(1, maxFrustums))
    );

    const positions = [];
    let shown = 0;

    for (let i = 0; i < images.length; i += step) {
      const image = images[i];
      const camera = cameras.get(image.cameraId);
      if (!camera) continue;

      const { fx, fy } = getFxFy(camera);
      const w = camera.width;
      const h = camera.height;

      const halfW = (w * near) / (2 * fx);
      const halfH = (h * near) / (2 * fy);

      const cornersCam = [
        [0, 0, 0],
        [-halfW, -halfH, near],
        [halfW, -halfH, near],
        [halfW, halfH, near],
        [-halfW, halfH, near],
      ];

      const rcw = qvecToMat3(image.qvec);
      const rwc = transpose3(rcw);
      const center = getCameraCenter(image);

      const toWorld = (point) => {
        const rotated = mulMat3Vec3(rwc, point);
        return [
          rotated[0] + center[0],
          rotated[1] + center[1],
          rotated[2] + center[2],
        ];
      };

      const p0 = toWorld(cornersCam[0]);
      const p1 = toWorld(cornersCam[1]);
      const p2 = toWorld(cornersCam[2]);
      const p3 = toWorld(cornersCam[3]);
      const p4 = toWorld(cornersCam[4]);

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
    const frustums = buildFrustumLines(images, cameras, depth);
    const id = `colmap-${nextModelId++}`;
    const rootEntity = new pc.Entity(`COLMAP sparse ${candidate.dir || id}`);
    const model = {
      id,
      bounds: modelBounds,
      images: images.length,
      pointsTotal: points.originalCount,
      pointsShown: points.shownCount,
      frustumsTotal: frustums.total,
      frustumsShown: frustums.shown,
      rootEntity,
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
        app, cameraCenters, centerColors, Number(cameraSize?.value ?? 4)
      );
      model.cameraCentersEntity = new pc.Entity("COLMAP camera centers");
      model.cameraCentersEntity.addComponent("render", {
        type: "asset",
        meshInstances: [model.cameraCentersResource.meshInstance],
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

      const label = candidate.dir.split("/").filter(Boolean).slice(-3).join("/") || "racine";
      const children = [
        {
          name: "Points 3D",
          setVisible(visible) { model.pointsEntity.enabled = visible; },
        },
        {
          name: "Centres caméra",
          setVisible(visible) { model.cameraCentersEntity.enabled = visible; },
        },
        {
          name: "Frustums",
          setVisible(visible) { model.frustumsEntity.enabled = visible; },
        },
      ];
      if (registerGroup) {
        model.unregister = registerGroup({
          id,
          name: `COLMAP · ${label}`,
          children,
        });
      } else {
        const unregister = children.map((child) => registerLayer?.({
          id: `${id}-${child.name.toLowerCase().replaceAll(" ", "-")}`,
          name: `${child.name} · ${label}`,
          setVisible: child.setVisible,
        })).filter(Boolean);
        model.unregister = () => unregister.forEach((remove) => remove());
      }

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

  listen(removeBtn, "click", () => {
    clear();
    setStatus("Modèles COLMAP retirés.");
  });

  refreshUI();
  refreshInfo();

  return {
    loadFromFileList,
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
