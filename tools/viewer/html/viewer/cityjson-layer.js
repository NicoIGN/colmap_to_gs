import * as pc from "playcanvas";
import { colmapBounds, getOverlayLayer } from "./colmap.js";
import { placementFor, worldPositions, frameFor } from "./lidar-layer.js";
import { readCityJson, splitRoofFacade, buildNearSplatGrid, dropNearTriangles } from "./cityjson-geometry.js";

export const MAX_FILE_BYTES = 512 * 1024 ** 2;
export const MAX_TRIANGLES = 1_500_000;
export const DEFAULT_VOLUME_COLOR = "#a9b8c8";
export const DEFAULT_VOLUME_OPACITY = 100;

// Normales transformées comme les positions (rotation seule, sans translation).
function worldNormals(normals, placement) {
  if (placement.mode === "local") return normals;
  const [[a, b, c], [d, e, f], [g, h, i]] = placement.linear;
  const out = new Float32Array(normals.length);
  for (let v = 0; v < normals.length / 3; v++) {
    const x = normals[v * 3];
    const y = normals[v * 3 + 1];
    const z = normals[v * 3 + 2];
    out[v * 3] = a * x + b * y + c * z;
    out[v * 3 + 1] = d * x + e * y + f * z;
    out[v * 3 + 2] = g * x + h * y + i * z;
  }
  return out;
}

// Ombrage lambert à deux faces (les normales des faces ne sont pas toujours orientées vers l'extérieur).
function makeVolumeMaterial(color, opacity) {
  const material = new pc.ShaderMaterial({
    uniqueName: "CityJsonVolumeMaterial",
    attributes: {
      vertex_position: pc.SEMANTIC_POSITION,
      vertex_normal: pc.SEMANTIC_NORMAL,
    },
    vertexGLSL: `
      attribute vec3 vertex_position;
      attribute vec3 vertex_normal;
      uniform mat4 matrix_model;
      uniform mat4 matrix_viewProjection;
      varying vec3 vNormal;
      void main(void) {
        vNormal = mat3(matrix_model) * vertex_normal;
        gl_Position = matrix_viewProjection * matrix_model * vec4(vertex_position, 1.0);
      }
    `,
    fragmentGLSL: `
      precision highp float;
      uniform vec3 uColor;
      uniform float uOpacity;
      varying vec3 vNormal;
      void main(void) {
        vec3 light = normalize(vec3(0.4, 0.8, 0.6));
        float lambert = abs(dot(normalize(vNormal), light));
        gl_FragColor = vec4(uColor * (0.35 + 0.65 * lambert), uOpacity);
      }
    `,
  });
  material.setParameter("uColor", [color.r, color.g, color.b]);
  material.cull = pc.CULLFACE_NONE;
  material.depthTest = true;
  applyOpacity(material, opacity);
  return material;
}

// Les volumes opaques écrivent la profondeur ; les volumes translucides ne l'écrivent pas.
function applyOpacity(material, opacity) {
  material.setParameter("uOpacity", opacity);
  material.depthWrite = opacity >= 1;
  material.blendType = opacity >= 1 ? pc.BLEND_NONE : pc.BLEND_NORMAL;
  material.update();
}

export function createCityJsonLayer({ app, setStatus, getFlipUp, getGeoref, getSplatCenters, adoptFrame, registerGroup, onBoundsChanged }) {
  let disposed = false;
  let nextId = 1;
  const color = new pc.Color().fromString(DEFAULT_VOLUME_COLOR);
  const partOpacity = { roof: DEFAULT_VOLUME_OPACITY / 100, facade: DEFAULT_VOLUME_OPACITY / 100 };
  // Visibilité de chaque partie, décidée par les cases « Toits » et « Façades » du panneau de couches.
  const partShown = { roof: true, facade: true };
  // Option « Masquer façades près des splats » : grilles en plan calculées à la demande, par paire d'axes.
  let hideNearSplats = false;
  const nearGrids = new Map();
  const layers = new Map();

  function emitBounds(frame) {
    const boxes = [...layers.values()]
      .flatMap((layer) => layer.children.map((child) => child.bounds))
      .filter(Boolean);
    const bounds = boxes.length
      ? colmapBounds(boxes.flatMap((box) => [box.min, box.max]), Boolean(getFlipUp?.()))
      : null;
    onBoundsChanged?.(bounds, frame);
  }

  function destroyRender(child) {
    for (const part of child.parts ?? []) {
      part.mesh?.destroy?.();
      part.material?.destroy?.();
      part.root?.destroy?.();
    }
    child.parts = [];
  }

  // Axes du plan horizontal : tous sauf l'axe qui porte la verticale source (Z).
  function planAxesFor(placement) {
    if (placement.mode === "local") return [0, 1];
    const linear = placement.linear;
    let up = 0;
    for (let i = 1; i < 3; i++) if (Math.abs(linear[i][2]) > Math.abs(linear[up][2])) up = i;
    return [0, 1, 2].filter((i) => i !== up);
  }

  function nearGridFor(placement) {
    if (!hideNearSplats) return null;
    const [a, b] = planAxesFor(placement);
    const key = `${a}${b}`;
    if (!nearGrids.has(key)) nearGrids.set(key, buildNearSplatGrid(getSplatCenters?.() ?? [], a, b));
    return nearGrids.get(key);
  }

  function render(child) {
    destroyRender(child);
    const view = frameFor(child.source, getGeoref?.() ?? null, adoptFrame);
    child.frame = view;
    const placement = placementFor(child.source, view);
    child.bounds = null;
    if (!child.source.triangleCount) return;

    const allPositions = [];
    const split = splitRoofFacade(child.source);
    const nearGrid = nearGridFor(placement);
    for (const kind of ["roof", "facade"]) {
      const part = split[kind];
      if (!part.triangleCount) continue;
      let positions = worldPositions({ rel: part.rel, count: part.vertexCount }, placement);
      let normals = worldNormals(part.normals, placement);
      allPositions.push(positions);
      if (kind === "facade" && nearGrid) {
        ({ positions, normals } = dropNearTriangles(positions, normals, nearGrid));
        if (!positions.length) continue;
      }
      const mesh = new pc.Mesh(app.graphicsDevice);
      mesh.setPositions(positions);
      mesh.setNormals(normals);
      mesh.update(pc.PRIMITIVE_TRIANGLES, true);

      const material = makeVolumeMaterial(color, partOpacity[kind]);
      const root = new pc.Entity(`CityJSON · ${child.source.name} · ${kind === "roof" ? "toit" : "façade"}`);
      root.addComponent("render", {
        type: "asset",
        meshInstances: [new pc.MeshInstance(mesh, material)],
        layers: [getOverlayLayer(app).id],
      });
      root.setLocalEulerAngles(getFlipUp() ? 180 : 0, 0, 0);
      root.enabled = partShown[kind];
      app.root.addChild(root);
      child.parts.push({ kind, mesh, material, root, positions });
    }
    child.bounds = colmapBounds(allPositions, false);
    child.placement = placement;
  }

  function applyPartVisibility(kind) {
    for (const layer of layers.values()) {
      for (const child of layer.children) {
        for (const part of child.parts) {
          if (part.kind === kind) part.root.enabled = partShown[kind];
        }
      }
    }
  }

  function refreshPlacement() {
    if (disposed) return;
    const view = getGeoref?.() ?? null;
    let changed = false;
    for (const layer of layers.values()) {
      for (const child of layer.children) {
        if (child.frame === view) continue;
        render(child);
        changed = true;
      }
    }
    if (changed) emitBounds(false);
  }

  async function loadText(text, name) {
    if (disposed) return;
    if (text.length > MAX_FILE_BYTES) {
      throw new Error("Fichier trop volumineux (limite : 512 Mio).");
    }
    setStatus(`Lecture de ${name}…`);
    const source = readCityJson(text, name, { maxTriangles: MAX_TRIANGLES });
    if (disposed) return;
    if (!source.triangleCount) {
      throw new Error("Aucune surface exploitable dans ce CityJSON (géométries Solid ou MultiSurface attendues).");
    }

    const id = `cityjson-${nextId++}`;
    const child = {
      name: `${name} · ${source.objectCount.toLocaleString("fr-FR")} objets · ${source.triangleCount.toLocaleString("fr-FR")} triangles`,
      source,
      bounds: null,
      frame: null,
      parts: [],
    };
    const layer = { id, name, visible: true, children: [child] };
    layers.set(id, layer);
    render(child);
    emitBounds(true);

    const unregister = registerGroup?.({
      id,
      name,
      visible: true,
      children: [
        {
          name: "Toits",
          visible: true,
          setVisible(visible) {
            partShown.roof = visible;
            applyPartVisibility("roof");
          },
        },
        {
          name: "Façades",
          visible: true,
          setVisible(visible) {
            partShown.facade = visible;
            applyPartVisibility("facade");
          },
        },
      ],
      onRemove: () => {
        unregister?.();
        destroyRender(child);
        layers.delete(id);
        emitBounds(false);
      },
    }) ?? null;

    const notes = [];
    if (source.holeCount) notes.push(`${source.holeCount} trou(s) ignoré(s)`);
    if (source.unsupportedCount) notes.push(`${source.unsupportedCount} objet(s) sans géométrie surfacique`);
    if (source.truncated) notes.push(`affichage limité à ${MAX_TRIANGLES.toLocaleString("fr-FR")} triangles`);
    const tail = notes.length ? ` Remarques : ${notes.join(", ")}.` : "";
    const placementNote = child.placement.note;
    setStatus(`${name} chargé. ${placementNote}${tail}`);
  }

  async function loadFile(file) {
    if (file.size > MAX_FILE_BYTES) {
      throw new Error("Fichier trop volumineux (limite : 512 Mio).");
    }
    await loadText(await file.text(), file.name);
  }

  function clear() {
    for (const layer of layers.values()) {
      for (const child of layer.children) destroyRender(child);
    }
    layers.clear();
    emitBounds(false);
  }

  return {
    loadFile,
    loadText,
    clear,
    refreshPlacement,

    getPivotSources() {
      const sources = [];
      for (const layer of layers.values()) {
        for (const child of layer.children) {
          for (const part of child.parts) {
            if (part.root.enabled) sources.push({ label: "vecteur", positions: part.positions, entity: part.root });
          }
        }
      }
      return sources;
    },

    setFlipUp(flip) {
      for (const layer of layers.values()) {
        for (const child of layer.children) {
          for (const part of child.parts) part.root.setLocalEulerAngles(flip ? 180 : 0, 0, 0);
        }
      }
    },

    // `percent` : 100 = opaque, 0 = invisible. `kind` : "roof" ou "facade".
    setOpacity(kind, percent) {
      partOpacity[kind] = percent / 100;
      for (const layer of layers.values()) {
        for (const child of layer.children) {
          for (const part of child.parts) {
            if (part.kind === kind) applyOpacity(part.material, partOpacity[kind]);
          }
        }
      }
    },

    setFacadeNearSplats(enabled) {
      hideNearSplats = enabled;
      nearGrids.clear();
      for (const layer of layers.values()) {
        for (const child of layer.children) render(child);
      }
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      clear();
    },
  };
}
