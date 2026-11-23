import * as pc from "playcanvas";
import { colmapBounds, getOverlayLayer } from "./colmap.js";
import { placementFor, worldPositions, resolvePivot, retainPivot, releasePivot } from "./lidar-layer.js";
import { polylinesToSegments, readGpkgLayers } from "./gpkg-geometry.js";
import { registerOverlayMaterial } from "./overlay-depth.js";
import { readGeojsonLayers } from "./geojson-geometry.js";

export const SQL_JS_BASE_URL = "https://cdn.jsdelivr.net/npm/sql.js@1.10.3/dist/";
export const MAX_FILE_BYTES = 512 * 1024 ** 2;
export const MAX_VERTICES = 4_000_000;
export const DEFAULT_WIRE_COLOR = "#00e5ff";
const HIGHLIGHT_COLOR = [1.0, 0.92, 0.0, 1.0];
// Les lignes WebGL ont une épaisseur fixe : la surbrillance superpose des copies décalées de 1 px.
const HIGHLIGHT_OFFSETS = [[0, 0], [-1, 0], [1, 0], [0, -1], [0, 1]];

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

function makeHighlightMaterial([dx, dy]) {
  const pixelRatio = window.devicePixelRatio || 1;
  const material = new pc.ShaderMaterial({
    uniqueName: "GpkgHighlightLinesMaterial",
    attributes: { vertex_position: pc.SEMANTIC_POSITION },
    vertexGLSL: `
      attribute vec3 vertex_position;
      uniform mat4 matrix_model;
      uniform mat4 matrix_viewProjection;
      uniform vec2 uOffsetPx;
      uniform vec2 uViewport;
      void main(void) {
        vec4 clip = matrix_viewProjection * matrix_model * vec4(vertex_position, 1.0);
        clip.xy += uOffsetPx * 2.0 / uViewport * clip.w;
        gl_Position = clip;
      }
    `,
    fragmentGLSL: `
      precision highp float;
      uniform vec4 uColor;
      void main(void) {
        gl_FragColor = uColor;
      }
    `,
  });
  material.setParameter("uOffsetPx", [dx * pixelRatio, dy * pixelRatio]);
  material.setParameter("uViewport", [1, 1]);
  material.setParameter("uColor", HIGHLIGHT_COLOR);
  material.cull = pc.CULLFACE_NONE;
  material.depthTest = false;
  material.depthWrite = false;
  material.blendType = pc.BLEND_NORMAL;
  material.update();
  return material;
}

function makeUnlitMaterial(color) {
  const mat = new pc.StandardMaterial();
  mat.useLighting = false;
  mat.emissive.copy(color);
  mat.diffuse.copy(color);
  mat.cull = pc.CULLFACE_NONE;
  registerOverlayMaterial(mat, "vectors");
  mat.depthWrite = false;
  mat.blendType = pc.BLEND_NORMAL;
  mat.update();
  return mat;
}

// Icosaèdre unitaire : 12 sommets, 20 faces, un petit octaèdre suffirait mais paraît moins rond.
const PHI = (1 + Math.sqrt(5)) / 2;
const ICO_VERTICES = [
  [-1, PHI, 0], [1, PHI, 0], [-1, -PHI, 0], [1, -PHI, 0],
  [0, -1, PHI], [0, 1, PHI], [0, -1, -PHI], [0, 1, -PHI],
  [PHI, 0, -1], [PHI, 0, 1], [-PHI, 0, -1], [-PHI, 0, 1],
].map(([x, y, z]) => {
  const n = Math.hypot(x, y, z);
  return [x / n, y / n, z / n];
});
const ICO_FACES = [
  [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
  [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
  [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
  [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
];
// Une subdivision de l'icosaèdre (42 sommets, 80 faces) pour que les points soient ronds et non facettés.
function subdivideIcosahedron(vertices, faces) {
  const out = vertices.map((v) => [...v]);
  const midpointCache = new Map();
  const midpoint = (a, b) => {
    const key = a < b ? `${a}-${b}` : `${b}-${a}`;
    if (midpointCache.has(key)) return midpointCache.get(key);
    const m = [0, 1, 2].map((k) => (out[a][k] + out[b][k]) / 2);
    const n = Math.hypot(...m);
    out.push(m.map((x) => x / n));
    midpointCache.set(key, out.length - 1);
    return out.length - 1;
  };
  const outFaces = [];
  for (const [a, b, c] of faces) {
    const ab = midpoint(a, b);
    const bc = midpoint(b, c);
    const ca = midpoint(c, a);
    outFaces.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]);
  }
  return { vertices: out, faces: outFaces };
}
const SPHERE = subdivideIcosahedron(ICO_VERTICES, ICO_FACES);
const SPHERE_VERTICES = SPHERE.vertices.length;
// Rayon des sphères en proportion de l'emprise de la couche (unités des données).
const POINT_RADIUS_RATIO = 0.002;
const POINT_RADIUS_DEFAULT = 0.25;
// Valeur par défaut du curseur « Taille des points » ; les sphères grossissent ou rétrécissent proportionnellement.
export const DEFAULT_POINT_SIZE = 2;

// Emprise et positions relatives au centre, en float64 puis float32 (comme le LiDAR).
function buildSource(name, epsg, polylines, points) {
  const segments = polylinesToSegments(polylines);
  const vertexCount = segments.length / 3;
  const pointCount = points.length / 3;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  const extend = (values) => {
    for (let i = 0; i < values.length; i++) {
      const axis = i % 3;
      if (values[i] < min[axis]) min[axis] = values[i];
      if (values[i] > max[axis]) max[axis] = values[i];
    }
  };
  extend(segments);
  extend(points);
  const hasData = vertexCount + pointCount > 0;
  const centre = hasData ? min.map((v, axis) => (v + max[axis]) / 2) : [0, 0, 0];
  const rel = new Float32Array(segments.length);
  for (let i = 0; i < segments.length; i++) rel[i] = segments[i] - centre[i % 3];
  const pointRel = new Float32Array(points.length);
  for (let i = 0; i < points.length; i++) pointRel[i] = points[i] - centre[i % 3];
  const span = hasData ? Math.max(...min.map((v, axis) => max[axis] - v)) : 0;
  const radius = span > 0 ? span * POINT_RADIUS_RATIO : POINT_RADIUS_DEFAULT;
  return { name, epsg, centre, rel, count: vertexCount, pointRel, pointCount, radius };
}

// Une sphère centrée sur chaque point, en coordonnées monde (même transformation que les lignes).
function buildSphereMesh(app, source, placement, radius) {
  const centres = worldPositions({ rel: source.pointRel, count: source.pointCount }, placement);
  const n = source.pointCount;
  const unit = SPHERE.vertices;
  const positions = new Float32Array(n * SPHERE_VERTICES * 3);
  const normals = new Float32Array(n * SPHERE_VERTICES * 3);
  const indices = new Uint32Array(n * SPHERE.faces.length * 3);
  for (let p = 0; p < n; p++) {
    for (let v = 0; v < SPHERE_VERTICES; v++) {
      const o = (p * SPHERE_VERTICES + v) * 3;
      for (let k = 0; k < 3; k++) {
        positions[o + k] = centres[p * 3 + k] + unit[v][k] * radius;
        normals[o + k] = unit[v][k];
      }
    }
    for (let f = 0; f < SPHERE.faces.length; f++) {
      for (let j = 0; j < 3; j++) {
        indices[(p * SPHERE.faces.length + f) * 3 + j] = p * SPHERE_VERTICES + SPHERE.faces[f][j];
      }
    }
  }
  const mesh = new pc.Mesh(app.graphicsDevice);
  mesh.setPositions(positions);
  mesh.setNormals(normals);
  mesh.setIndices(indices);
  mesh.update(pc.PRIMITIVE_TRIANGLES, true);
  return mesh;
}

// Sphères éclairées (lambert simple) : sans ombrage, une sphère unie se lit comme un disque plat.
function makeSphereMaterial(color) {
  const material = new pc.ShaderMaterial({
    uniqueName: "GpkgSpherePointsMaterial",
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
      varying vec3 vNormal;
      void main(void) {
        vec3 light = normalize(vec3(0.4, 0.8, 0.6));
        float lambert = max(dot(normalize(vNormal), light), 0.0);
        gl_FragColor = vec4(uColor * (0.35 + 0.65 * lambert), 1.0);
      }
    `,
  });
  material.setParameter("uColor", [color.r, color.g, color.b]);
  material.cull = pc.CULLFACE_BACK;
  registerOverlayMaterial(material, "vectors");
  material.depthWrite = false;
  material.blendType = pc.BLEND_NONE;
  material.update();
  return material;
}

export function createGpkgLayer({ app, setStatus, getFlipUp, getGeoref, registerGroup, onBoundsChanged }) {
  let disposed = false;
  let nextId = 1;
  let lastGeoref = null;
  let wireColor = new pc.Color().fromString(DEFAULT_WIRE_COLOR);
  let pointSize = DEFAULT_POINT_SIZE;
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
    for (const mesh of child.resource?.meshes ?? []) mesh.destroy?.();
    child.resource?.material?.destroy?.();
    child.resource?.sphereMaterial?.destroy?.();
    for (const material of child.resource?.highlightMaterials ?? []) material.destroy?.();
    child.resource = null;
    child.root?.destroy?.();
    child.root = null;
    child.lineEntity = null;
    child.highlightEntity = null;
  }

  function applyHighlight(child) {
    if (!child.root) return;
    child.lineEntity.enabled = !child.highlighted;
    child.highlightEntity.enabled = child.highlighted;
    if (!child.highlighted) return;
    const { width, height } = app.graphicsDevice;
    for (const material of child.resource.highlightMaterials) {
      material.setParameter("uViewport", [width, height]);
    }
  }

  // Sans géoréférencement de scène, le pivot partagé avec le LiDAR sert de repère (cf. lidar-layer.js).
  function render(child, georef) {
    destroyRender(child);
    const placement = placementFor(child.source, resolvePivot(child.source, georef));
    child.placement = placement;
    child.bounds = null;
    const hasLines = child.source.count > 0;
    const hasPoints = child.source.pointCount > 0;
    if (!hasLines && !hasPoints) return placement;

    const positionSets = [];
    const meshes = [];
    let pointMesh = null;
    if (hasLines) {
      const linePositions = worldPositions(child.source, placement);
      positionSets.push(linePositions);
      const lineMesh = new pc.Mesh(app.graphicsDevice);
      lineMesh.setPositions(linePositions);
      lineMesh.update(pc.PRIMITIVE_LINES, true);
      meshes.push(lineMesh);
    }
    if (hasPoints) {
      positionSets.push(worldPositions({ rel: child.source.pointRel, count: child.source.pointCount }, placement));
      const radius = (child.source.radius * pointSize) / DEFAULT_POINT_SIZE;
      pointMesh = buildSphereMesh(app, child.source, placement, radius);
      meshes.push(pointMesh);
    }
    child.bounds = colmapBounds(positionSets, false);
    child.pivotPositions = positionSets;

    const material = makeUnlitMaterial(wireColor);
    const sphereMaterial = pointMesh ? makeSphereMaterial(wireColor) : null;
    const highlightMaterials = HIGHLIGHT_OFFSETS.map(makeHighlightMaterial);
    child.resource = { meshes, material, sphereMaterial, pointMesh, highlightMaterials };

    child.lineEntity = new pc.Entity(`GPKG lignes · ${child.source.name}`);
    child.lineEntity.addComponent("render", {
      type: "asset",
      meshInstances: meshes.map((mesh) => new pc.MeshInstance(mesh, mesh === pointMesh ? sphereMaterial : material)),
      layers: [getOverlayLayer(app).id],
    });

    child.highlightEntity = new pc.Entity(`GPKG surbrillance · ${child.source.name}`);
    child.highlightEntity.addComponent("render", {
      type: "asset",
      meshInstances: highlightMaterials.flatMap((m) => meshes.map((mesh) => new pc.MeshInstance(mesh, m))),
      layers: [getOverlayLayer(app).id],
    });

    child.root = new pc.Entity(`GPKG · ${child.source.name}`);
    child.root.addChild(child.lineEntity);
    child.root.addChild(child.highlightEntity);
    child.root.setLocalEulerAngles(getFlipUp() ? 180 : 0, 0, 0);
    child.root.enabled = child.visible;
    app.root.addChild(child.root);
    applyHighlight(child);
    return placement;
  }

  function refreshPlacement() {
    if (disposed) return;
    for (const layer of layers.values()) {
      for (const child of layer.children) {
        if (child.highlighted) applyHighlight(child);
      }
    }
    const georef = getGeoref?.() ?? null;
    if (georef === lastGeoref) return;
    lastGeoref = georef;
    for (const layer of layers.values()) {
      for (const child of layer.children) render(child, georef);
    }
    emitBounds(false);
  }

  async function loadFile(file) {
    if (disposed) return;
    if (file.size > MAX_FILE_BYTES) {
      throw new Error("Fichier trop volumineux (limite : 512 Mio).");
    }

    setStatus(`Lecture de ${file.name}…`);
    let tables;
    if (/\.geojson$/i.test(file.name)) {
      try {
        tables = readGeojsonLayers(await file.text(), file.name);
      } catch (error) {
        throw new Error(`GeoJSON invalide : ${error.message}`);
      }
    } else {
      const SQL = await loadSqlJs();
      const db = new SQL.Database(new Uint8Array(await file.arrayBuffer()));
      try {
        tables = readGpkgLayers(db);
      } finally {
        db.close();
      }
    }
    if (disposed) return;

    const drawable = tables.filter((t) => t.polylines.length > 0 || t.points.length > 0);
    if (!drawable.length) {
      throw new Error("Aucune géométrie linéaire, surfacique ou ponctuelle dans ce fichier.");
    }

    const georef = getGeoref?.() ?? null;
    lastGeoref = georef;
    const id = `gpkg-${nextId++}`;
    const layer = { id, name: file.name, visible: true, children: [] };
    let truncated = false;
    let vertexBudget = MAX_VERTICES;

    for (const table of drawable) {
      const source = buildSource(table.name, table.epsg, table.polylines, table.points);
      const keptLines = Math.min(source.count, vertexBudget);
      if (keptLines < source.count) {
        truncated = true;
        source.rel = source.rel.subarray(0, keptLines * 3);
        source.count = keptLines;
      }
      vertexBudget -= keptLines;
      const keptPoints = Math.min(source.pointCount, Math.floor(vertexBudget / SPHERE_VERTICES));
      if (keptPoints < source.pointCount) {
        truncated = true;
        source.pointRel = source.pointRel.subarray(0, keptPoints * 3);
        source.pointCount = keptPoints;
      }
      vertexBudget -= keptPoints * SPHERE_VERTICES;
      const parts = [];
      if (source.count) parts.push(`${(source.count / 2).toLocaleString("fr-FR")} segments`);
      if (source.pointCount) parts.push(`${source.pointCount.toLocaleString("fr-FR")} points`);
      const child = {
        name: `${table.name} · ${parts.join(" · ")}`,
        source,
        visible: true,
        highlighted: false,
        root: null,
        resource: null,
        lineEntity: null,
        highlightEntity: null,
        placement: null,
      };
      layer.children.push(child);
      render(child, georef);
    }
    layers.set(id, layer);
    retainPivot(id);
    emitBounds(true);

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
        setHighlight(on) {
          child.highlighted = on;
          applyHighlight(child);
        },
      })),
      onRemove: () => {
        unregister?.();
        for (const child of layer.children) destroyRender(child);
        layers.delete(id);
        releasePivot(id);
        emitBounds(false);
      },
    }) ?? null;

    const note = layer.children[0].placement.note;
    const cut = truncated ? ` Affichage limité à ${MAX_VERTICES.toLocaleString("fr-FR")} sommets.` : "";
    setStatus(`${file.name} chargé : ${drawable.length} couche(s) filaire(s). ${note}${cut}`);
  }

  function clear() {
    for (const layer of layers.values()) {
      for (const child of layer.children) destroyRender(child);
      releasePivot(layer.id);
    }
    layers.clear();
    emitBounds(false);
  }

  return {
    loadFile,
    clear,
    refreshPlacement,

    getPivotSources() {
      const sources = [];
      for (const layer of layers.values()) {
        for (const child of layer.children) {
          if (!child.root?.enabled || !child.pivotPositions) continue;
          for (const positions of child.pivotPositions) {
            sources.push({ label: "vecteur", positions, entity: child.root });
          }
        }
      }
      return sources;
    },

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
          if (child.resource.sphereMaterial) {
            child.resource.sphereMaterial.setParameter("uColor", [wireColor.r, wireColor.g, wireColor.b]);
            child.resource.sphereMaterial.update();
          }
        }
      }
    },

    setPointSize(size) {
      pointSize = size;
      for (const layer of layers.values()) {
        for (const child of layer.children) {
          if (child.source.pointCount) render(child, lastGeoref);
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
