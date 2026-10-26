import * as pc from "playcanvas";

export function createGpsLayer({ app }) {
  const checkbox = document.getElementById("gpsTrackVisible");

  let entity = null;
  let mesh = null;
  let material = null;
  let currentPoints = null;
  let disposed = false;

  function refreshUI(blocked = false) {
    if (checkbox) {
      checkbox.disabled = blocked || !entity;
    }

    if (entity) {
      entity.enabled = checkbox ? checkbox.checked : true;
    }
  }

  function clear() {
    entity?.destroy();
    mesh?.destroy();
    material?.destroy();

    entity = null;
    mesh = null;
    material = null;
    currentPoints = null;

    refreshUI();
  }

  function update(track) {
    if (disposed) return;

    const points = track?.pointsWorld ?? null;

    // Aucun travail GPU tant que la trajectoire n’a pas changé.
    if (points === currentPoints) return;

    clear();

    if (!points || points.length < 2) return;

    const positions = new Float32Array((points.length - 1) * 6);
    let offset = 0;

    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i];
      const b = points[i + 1];

      positions[offset++] = a.x;
      positions[offset++] = a.y;
      positions[offset++] = a.z;
      positions[offset++] = b.x;
      positions[offset++] = b.y;
      positions[offset++] = b.z;
    }

    material = new pc.ShaderMaterial({
      uniqueName: "GpsTrackRedLine",

      attributes: {
        vertex_position: pc.SEMANTIC_POSITION,
      },

      vertexGLSL: `
        attribute vec3 vertex_position;

        uniform mat4 matrix_model;
        uniform mat4 matrix_viewProjection;

        void main(void) {
          gl_Position =
            matrix_viewProjection *
            matrix_model *
            vec4(vertex_position, 1.0);
        }
      `,

      fragmentGLSL: `
        precision highp float;

        void main(void) {
          gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0);
        }
      `,
    });

    material.cull = pc.CULLFACE_NONE;
    material.depthTest = true;
    material.depthWrite = false;
    material.blendType = pc.BLEND_NONE;
    material.update();

    mesh = new pc.Mesh(app.graphicsDevice);
    mesh.setPositions(positions);
    mesh.update(pc.PRIMITIVE_LINES, true);

    const meshInstance = new pc.MeshInstance(mesh, material);
    meshInstance.cull = false;

    entity = new pc.Entity("Trajectoire GPS");
    entity.addComponent("render", {
      type: "asset",
      meshInstances: [meshInstance],
    });

    // Les points sont déjà dans le monde affiché.
    // Ne pas les placer sous l’entité COLMAP ou sous les splats.
    app.root.addChild(entity);

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
