import * as pc from "playcanvas";

const LINE_COLOR = new pc.Color(140 / 255, 230 / 255, 0); // vert pomme
const SPHERE_COLOR = new pc.Color(140 / 255, 230 / 255, 0);
const SPHERE_RADIUS = 0.5; // mètres

export function createGpsLayer({ app }) {
  const checkbox = document.getElementById("gpsTrackVisible");

  let rootEntity = null;
  let lineEntity = null;

  let lineMesh = null;
  let lineMaterial = null;
  let sphereMaterial = null;

  let currentTrack = null;
  let currentPoints = null;
  let disposed = false;

  function refreshUI(blocked = false) {
    if (checkbox) checkbox.disabled = blocked || !rootEntity;
    if (rootEntity) rootEntity.enabled = checkbox ? checkbox.checked : true;
  }

  function destroySafe(obj) {
    try {
      obj?.destroy?.();
    } catch {}
  }

  function clear() {
    destroySafe(rootEntity);
    rootEntity = null;
    lineEntity = null;

    destroySafe(lineMesh);
    lineMesh = null;

    destroySafe(lineMaterial);
    lineMaterial = null;

    destroySafe(sphereMaterial);
    sphereMaterial = null;

    currentTrack = null;
    currentPoints = null;

    refreshUI();
  }

  function makeUnlitMaterial(color) {
    const mat = new pc.StandardMaterial();
    mat.useLighting = false;
    mat.emissive.copy(color);
    mat.diffuse.copy(color);
    mat.cull = pc.CULLFACE_NONE;
    mat.depthTest = true;
    mat.depthWrite = false;
    mat.blendType = pc.BLEND_NORMAL;
    mat.update();
    return mat;
  }

  function update(track) {
    if (disposed) return;

    const points = track?.pointsWorld ?? null;
    if (track === currentTrack && points === currentPoints) return;

    clear();
    if (!points || points.length < 2) return;

    const validPoints = points.filter((p) =>
      [p.x, p.y, p.z].every(Number.isFinite)
    );
    if (validPoints.length < 2) return;

    rootEntity = new pc.Entity("GPS Track Root");
    app.root.addChild(rootEntity);

    // Ligne
    const linePositions = new Float32Array((validPoints.length - 1) * 6);
    let o = 0;
    for (let i = 0; i < validPoints.length - 1; i++) {
      const a = validPoints[i];
      const b = validPoints[i + 1];
      linePositions[o++] = a.x;
      linePositions[o++] = a.y;
      linePositions[o++] = a.z;
      linePositions[o++] = b.x;
      linePositions[o++] = b.y;
      linePositions[o++] = b.z;
    }

    lineMesh = new pc.Mesh(app.graphicsDevice);
    lineMesh.setPositions(linePositions);
    lineMesh.update(pc.PRIMITIVE_LINES, true);

    lineMaterial = makeUnlitMaterial(LINE_COLOR);

    lineEntity = new pc.Entity("GPS Track Line");
    lineEntity.addComponent("render", {
      type: "asset",
      meshInstances: [new pc.MeshInstance(lineMesh, lineMaterial)],
    });
    rootEntity.addChild(lineEntity);

    // Sommets (sphères)
    sphereMaterial = makeUnlitMaterial(SPHERE_COLOR);

    for (let i = 0; i < validPoints.length; i++) {
      const p = validPoints[i];

      const sphere = new pc.Entity(`GPS Vertex ${i}`);
      sphere.addComponent("render", {
        type: "sphere",
        material: sphereMaterial,
      });

      sphere.setLocalPosition(p.x, p.y, p.z);
      sphere.setLocalScale(
        SPHERE_RADIUS * 2,
        SPHERE_RADIUS * 2,
        SPHERE_RADIUS * 2
      );

      for (const instance of sphere.render.meshInstances) {
        instance.material = sphereMaterial;
        instance.cull = false;
      }

      rootEntity.addChild(sphere);
    }

    currentTrack = track;
    currentPoints = points;
    refreshUI();
  }

  function onVisibilityChange() {
    refreshUI();
  }

  checkbox?.addEventListener("change", onVisibilityChange);
  refreshUI();

  return {
    update,
    clear,
    refreshUI,
    dispose() {
      disposed = true;
      checkbox?.removeEventListener("change", onVisibilityChange);
      clear();
    },
  };
}
