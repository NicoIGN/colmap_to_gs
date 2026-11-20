import * as pc from "playcanvas";
import { getOverlayLayer } from "./colmap.js";
import { placementFor, worldPositions } from "./lidar-layer.js";
import { polylinesToSegments, readGpkgLayers } from "./gpkg-geometry.js";

export const SQL_JS_BASE_URL = "https://cdn.jsdelivr.net/npm/sql.js@1.10.3/dist/";
export const MAX_FILE_BYTES = 512 * 1024 ** 2;
export const MAX_VERTICES = 4_000_000;
export const DEFAULT_WIRE_COLOR = "#00e5ff";

let sqlJsPromise = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Chargement de sql.js impossible (CDN injoignable)."));
    document.head.append(script);
  });
}

// Moteur SQLite WASM partagé, nécessaire pour lire les tables GeoPackage.
function loadSqlJs() {
  if (!sqlJsPromise) {
    sqlJsPromise = (async () => {
      if (!window.initSqlJs) await loadScript(`${SQL_JS_BASE_URL}sql-wasm.js`);
      return window.initSqlJs({ locateFile: (file) => `${SQL_JS_BASE_URL}${file}` });
    })();
    sqlJsPromise.catch(() => {
      sqlJsPromise = null;
    });
  }
  return sqlJsPromise;
}

function makeUnlitMaterial(color) {
  const mat = new pc.StandardMaterial();
  mat.useLighting = false;
  mat.emissive.copy(color);
  mat.diffuse.copy(color);
  mat.cull = pc.CULLFACE_NONE;
  mat.depthTest = false;
  mat.depthWrite = false;
  mat.blendType = pc.BLEND_NORMAL;
  mat.update();
  return mat;
}

// Emprise et positions relatives au centre, en float64 puis float32 (comme le LiDAR).
function buildSource(name, epsg, polylines) {
  const segments = polylinesToSegments(polylines);
  const vertexCount = segments.length / 3;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < segments.length; i++) {
    const axis = i % 3;
    if (segments[i] < min[axis]) min[axis] = segments[i];
    if (segments[i] > max[axis]) max[axis] = segments[i];
  }
  const centre = vertexCount ? min.map((v, axis) => (v + max[axis]) / 2) : [0, 0, 0];
  const rel = new Float32Array(segments.length);
  for (let i = 0; i < segments.length; i++) rel[i] = segments[i] - centre[i % 3];
  return { name, epsg, centre, rel, count: vertexCount };
}

export function createGpkgLayer({ app, setStatus, getFlipUp, getGeoref, registerGroup }) {
  let disposed = false;
  let nextId = 1;
  let lastGeoref = null;
  let wireColor = new pc.Color().fromString(DEFAULT_WIRE_COLOR);
  const layers = new Map();

  function destroyRender(child) {
    child.resource?.mesh?.destroy?.();
    child.resource?.material?.destroy?.();
    child.resource = null;
    child.root?.destroy?.();
    child.root = null;
  }

  function render(child, georef) {
    destroyRender(child);
    const placement = placementFor(child.source, georef);
    child.placement = placement;
    if (!child.source.count) return placement;

    const mesh = new pc.Mesh(app.graphicsDevice);
    mesh.setPositions(worldPositions(child.source, placement));
    mesh.update(pc.PRIMITIVE_LINES, true);
    const material = makeUnlitMaterial(wireColor);
    child.resource = { mesh, material };

    const lineEntity = new pc.Entity(`GPKG lignes · ${child.source.name}`);
    lineEntity.addComponent("render", {
      type: "asset",
      meshInstances: [new pc.MeshInstance(mesh, material)],
      layers: [getOverlayLayer(app).id],
    });

    child.root = new pc.Entity(`GPKG · ${child.source.name}`);
    child.root.addChild(lineEntity);
    child.root.setLocalEulerAngles(getFlipUp() ? 180 : 0, 0, 0);
    child.root.enabled = child.visible;
    app.root.addChild(child.root);
    return placement;
  }

  function refreshPlacement() {
    if (disposed) return;
    const georef = getGeoref?.() ?? null;
    if (georef === lastGeoref) return;
    lastGeoref = georef;
    for (const layer of layers.values()) {
      for (const child of layer.children) render(child, georef);
    }
  }

  async function loadFile(file) {
    if (disposed) return;
    if (file.size > MAX_FILE_BYTES) {
      throw new Error("GeoPackage trop volumineux (limite : 512 Mio).");
    }

    setStatus(`Lecture de ${file.name}…`);
    const SQL = await loadSqlJs();
    const db = new SQL.Database(new Uint8Array(await file.arrayBuffer()));
    let tables;
    try {
      tables = readGpkgLayers(db);
    } finally {
      db.close();
    }
    if (disposed) return;

    const drawable = tables.filter((t) => t.polylines.length > 0);
    if (!drawable.length) {
      throw new Error("Aucune géométrie linéaire ou surfacique dans ce GeoPackage.");
    }

    const georef = getGeoref?.() ?? null;
    lastGeoref = georef;
    const id = `gpkg-${nextId++}`;
    const layer = { id, name: file.name, visible: true, children: [] };
    let truncated = false;
    let vertexBudget = MAX_VERTICES;

    for (const table of drawable) {
      const source = buildSource(table.name, table.epsg, table.polylines);
      const kept = Math.min(source.count, vertexBudget);
      if (kept < source.count) {
        truncated = true;
        source.rel = source.rel.subarray(0, kept * 3);
        source.count = kept;
      }
      vertexBudget -= kept;
      const child = {
        name: `${table.name} · ${(source.count / 2).toLocaleString("fr-FR")} segments`,
        source,
        visible: true,
        root: null,
        resource: null,
        placement: null,
      };
      layer.children.push(child);
      render(child, georef);
    }
    layers.set(id, layer);

    const unregister = registerGroup?.({
      id,
      name: file.name,
      visible: true,
      children: layer.children.map((child) => ({
        name: child.name,
        setVisible(visible) {
          child.visible = visible;
          if (child.root) child.root.enabled = visible;
        },
      })),
      onRemove: () => {
        unregister?.();
        for (const child of layer.children) destroyRender(child);
        layers.delete(id);
      },
    }) ?? null;

    const note = layer.children[0].placement.note;
    const cut = truncated ? ` Affichage limité à ${MAX_VERTICES.toLocaleString("fr-FR")} sommets.` : "";
    setStatus(`${file.name} chargé : ${drawable.length} couche(s) filaire(s). ${note}${cut}`);
  }

  function clear() {
    for (const layer of layers.values()) {
      for (const child of layer.children) destroyRender(child);
    }
    layers.clear();
  }

  return {
    loadFile,
    clear,
    refreshPlacement,

    setFlipUp(flip) {
      for (const layer of layers.values()) {
        for (const child of layer.children) child.root?.setLocalEulerAngles(flip ? 180 : 0, 0, 0);
      }
    },

    setColor(hex) {
      wireColor = new pc.Color().fromString(hex);
      for (const layer of layers.values()) {
        for (const child of layer.children) {
          const material = child.resource?.material;
          if (!material) continue;
          material.emissive.copy(wireColor);
          material.diffuse.copy(wireColor);
          material.update();
        }
      }
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      clear();
    },
  };
}
