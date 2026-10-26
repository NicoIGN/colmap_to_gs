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

  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  let maxPointSize = 64;

  const gl = app.graphicsDevice.gl;
  if (gl) {
    const range = gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE);
    if (range?.length === 2) {
      maxPointSize = Number(range[1]) || maxPointSize;
    }
  }

  material.setParameter(
    "uPointSize",
    Math.min(maxPointSize, Math.max(1, pointSize * pixelRatio))
  );

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
}) {
  const sceneColmapInfo = document.getElementById("sceneColmapInfo");
  const input = document.getElementById("colmapFolderInput");
  const cbPoints = document.getElementById("colmapPointsVisible");
  const cbCameras = document.getElementById("colmapCamerasVisible");
  const cbFrustums = document.getElementById("colmapFrustumsVisible");
  const removeBtn = document.getElementById("colmapRemove");

  let disposed = false;

  let rootEntity = null;
  let pointsEntity = null;
  let cameraCentersEntity = null;
  let frustumsEntity = null;

  let pointsResource = null;
  let cameraCentersResource = null;
  let frustumsResource = null;

  let loaded = null;

  const removers = [];
    console.log("[COLMAP] Nouvelle instance créée", { disposed });

  function listen(element, type, handler) {
    if (!element) return;
    element.addEventListener(type, handler);
    removers.push(() => element.removeEventListener(type, handler));
  }

  function setInfo(text) {
    if (sceneColmapInfo) sceneColmapInfo.textContent = text;
  }

  function refreshInfo() {
    if (!loaded) {
      setInfo("Aucun modèle chargé");
      return;
    }

    setInfo(
      `Images: ${loaded.images.toLocaleString("fr-FR")} · ` +
      `Points: ${loaded.pointsShown.toLocaleString("fr-FR")} / ${loaded.pointsTotal.toLocaleString("fr-FR")} · ` +
      `Frustums: ${loaded.frustumsShown.toLocaleString("fr-FR")} / ${loaded.frustumsTotal.toLocaleString("fr-FR")}`
    );
  }

  function applyFlipUp(flip) {
    if (!rootEntity) return;
    rootEntity.setLocalEulerAngles(flip ? 180 : 0, 0, 0);
  }

  function refreshUI() {
    const ready = Boolean(loaded);

    if (cbPoints) cbPoints.disabled = !ready;
    if (cbCameras) cbCameras.disabled = !ready;
    if (cbFrustums) cbFrustums.disabled = !ready;
    if (removeBtn) removeBtn.disabled = !ready;

    if (pointsEntity && cbPoints) pointsEntity.enabled = cbPoints.checked;
    if (cameraCentersEntity && cbCameras) {
      cameraCentersEntity.enabled = cbCameras.checked;
    }
    if (frustumsEntity && cbFrustums) {
      frustumsEntity.enabled = cbFrustums.checked;
    }
  }

  function destroyMeshEntity(entity, resource) {
    if (entity?.parent) entity.parent.removeChild(entity);
    entity?.destroy();
    resource?.mesh?.destroy?.();
    resource?.material?.destroy?.();
  }

  function clear() {
    destroyMeshEntity(pointsEntity, pointsResource);
    destroyMeshEntity(cameraCentersEntity, cameraCentersResource);
    destroyMeshEntity(frustumsEntity, frustumsResource);

    pointsEntity = null;
    cameraCentersEntity = null;
    frustumsEntity = null;

    pointsResource = null;
    cameraCentersResource = null;
    frustumsResource = null;

    if (rootEntity?.parent) rootEntity.parent.removeChild(rootEntity);
    rootEntity?.destroy();
    rootEntity = null;

    loaded = null;

    if (cbPoints) cbPoints.checked = false;
    if (cbCameras) cbCameras.checked = false;
    if (cbFrustums) cbFrustums.checked = false;

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

  function buildFrustumLines(images, cameras, sceneRadius) {
    const near = Math.max(sceneRadius * 0.01, 0.1);
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

    async function loadFromFileList(fileList) {
      const tag = "[COLMAP]";
      console.group(`${tag} loadFromFileList`);

      try {
        console.log("1) entrée fonction", {
          disposed,
          hasFileList: Boolean(fileList),
          length: fileList?.length ?? 0,
        });

        if (disposed || !fileList?.length) {
          console.warn("STOP: disposed ou fileList vide");
          return;
        }

        const files = Array.from(fileList);
        console.log("2) fichiers reçus:", files.length);

        console.table(
          files.map((f, i) => ({
            i,
            name: f.name,
            rel: (f.webkitRelativePath || f.name).replace(/\\/g, "/"),
            size: f.size,
          }))
        );

        // Groupement par dossier relatif
        const groups = new Map();
        for (const file of files) {
          const rel = (file.webkitRelativePath || file.name).replace(/\\/g, "/");
          const idx = rel.lastIndexOf("/");
          const dir = idx >= 0 ? rel.slice(0, idx) : "";
          const base = idx >= 0 ? rel.slice(idx + 1) : rel;

          if (!groups.has(dir)) groups.set(dir, {});
          const g = groups.get(dir);

          if (base === "cameras.bin") g.cameras = file;
          if (base === "images.bin") g.images = file;
          if (base === "points3D.bin") g.points = file;
        }

        const candidates = [];
        for (const [dir, g] of groups.entries()) {
          if (g.cameras && g.images && g.points) candidates.push({ dir, ...g });
        }

        console.log("3) dossiers candidats:", candidates.length);
        if (candidates.length) {
          console.table(
            candidates.map((c) => ({
              dir: c.dir || "(racine)",
              cameras: c.cameras.webkitRelativePath || c.cameras.name,
              images: c.images.webkitRelativePath || c.images.name,
              points: c.points.webkitRelativePath || c.points.name,
            }))
          );
        }

        if (!candidates.length) {
          console.error("STOP: aucun triplet cameras/images/points trouvé");
          setStatus(
            "Aucun sous-dossier valide trouvé (cameras.bin + images.bin + points3D.bin).",
            true
          );
          return;
        }

        candidates.sort(
          (a, b) => b.dir.split("/").length - a.dir.split("/").length
        );
        const selected = candidates[0];

        console.log("4) dossier sélectionné:", selected.dir || "(racine)");
        setStatus(`Chargement COLMAP depuis: ${selected.dir || "(racine)"}`);

        console.log("5) lecture arrayBuffer...");
        const [cameraBuffer, imagesBuffer, pointsBuffer] = await Promise.all([
          selected.cameras.arrayBuffer(),
          selected.images.arrayBuffer(),
          selected.points.arrayBuffer(),
        ]);

        console.log("6) buffers lus", {
          cameras: cameraBuffer.byteLength,
          images: imagesBuffer.byteLength,
          points3D: pointsBuffer.byteLength,
        });

        if (disposed) {
          console.warn("STOP: disposed après lecture buffers");
          return;
        }

        console.log("7) parsing cameras.bin...");
        const cameras = parseCamerasBin(cameraBuffer);
        console.log("   cameras:", cameras.size);

        console.log("8) parsing images.bin...");
        const images = parseImagesBin(imagesBuffer);
        console.log("   images:", images.length);

        console.log("9) parsing points3D.bin...");
        const points = parsePoints3DBin(pointsBuffer, 300000);
        console.log("   points:", {
          original: points.originalCount,
          shown: points.shownCount,
        });

        const radius = Math.max(getSceneRadius?.() ?? 1, 1e-3);
        console.log("10) build frustums/centers, radius=", radius);

        const frustums = buildFrustumLines(images, cameras, radius);
        const cameraCenters = buildCameraCenters(images);

        console.log("   frustums:", {
          total: frustums.total,
          shown: frustums.shown,
          vertices: frustums.positions.length / 3,
        });

        console.log("11) clear ancien modèle");
        clear();

        console.log("12) création entités PlayCanvas");
        rootEntity = new pc.Entity("COLMAP sparse");
        app.root.addChild(rootEntity);

        pointsResource = makePointMesh(app, points.positions, points.colors, 2);
        pointsEntity = new pc.Entity("COLMAP points");
        pointsEntity.addComponent("render", {
          type: "asset",
          meshInstances: [pointsResource.meshInstance],
        });
        rootEntity.addChild(pointsEntity);

        const centerColors = new Float32Array(cameraCenters.length);
        for (let i = 0; i < cameraCenters.length / 3; i++) {
          centerColors[i * 3 + 0] = 1;
          centerColors[i * 3 + 1] = 0;
          centerColors[i * 3 + 2] = 0;
        }

        cameraCentersResource = makePointMesh(app, cameraCenters, centerColors, 8);
        cameraCentersEntity = new pc.Entity("COLMAP camera centers");
        cameraCentersEntity.addComponent("render", {
          type: "asset",
          meshInstances: [cameraCentersResource.meshInstance],
        });
        rootEntity.addChild(cameraCentersEntity);

        frustumsResource = makeLineMesh(app, frustums.positions, [1, 0, 0, 1]);
        frustumsEntity = new pc.Entity("COLMAP frustums");
        frustumsEntity.addComponent("render", {
          type: "asset",
          meshInstances: [frustumsResource.meshInstance],
        });
        rootEntity.addChild(frustumsEntity);

        applyFlipUp(Boolean(getFlipUp?.()));

        loaded = {
          images: images.length,
          pointsTotal: points.originalCount,
          pointsShown: points.shownCount,
          frustumsTotal: frustums.total,
          frustumsShown: frustums.shown,
        };

        console.log("13) refresh UI + info", loaded);

        if (cbPoints) cbPoints.checked = true;
        if (cbCameras) cbCameras.checked = true;
        if (cbFrustums) cbFrustums.checked = true;

        refreshUI();
        refreshInfo();

        setStatus(
          `COLMAP chargé : ${loaded.images.toLocaleString("fr-FR")} caméras, ` +
          `${loaded.pointsShown.toLocaleString("fr-FR")} points affichés ` +
          `(sur ${loaded.pointsTotal.toLocaleString("fr-FR")}).`,
          "success"
        );

        console.log("14) DONE");
      } catch (error) {
        console.error("[COLMAP] ERREUR FATALE:", error);
        setStatus(`Import COLMAP impossible : ${error?.message || error}`, true);
      } finally {
        console.groupEnd();
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

  listen(cbPoints, "change", refreshUI);
  listen(cbCameras, "change", refreshUI);
  listen(cbFrustums, "change", refreshUI);

  listen(removeBtn, "click", () => {
    clear();
    setStatus("Modèle COLMAP retiré.");
  });

  refreshUI();
  refreshInfo();

  return {
    loadFromFileList,
    clear,
    refreshUI,

    setFlipUp(flip) {
      applyFlipUp(Boolean(flip));
    },

      dispose() {
        console.trace("[COLMAP] dispose() appelé depuis");

        if (disposed) return;
        disposed = true;

        for (const remove of removers) remove();
        removers.length = 0;

        clear();
      },
  };
}
