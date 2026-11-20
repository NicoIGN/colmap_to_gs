import * as pc from "playcanvas";
import { colmapBounds, makePointMesh, setMeshHighlight } from "./colmap.js";
import { classColor, decodePointRecord, parseLasHeader } from "./las-header.js";

export const LAZ_PERF_URL = "https://cdn.jsdelivr.net/npm/laz-perf@0.0.7/lib/web/laz-perf.js";
export const MAX_DISPLAY_POINTS = 2_000_000;
export const MAX_FILE_BYTES = 1024 ** 3;
const POINT_SIZE = 2;
const YIELD_EVERY = 500_000;

let lazPerfPromise = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error("Chargement de laz-perf impossible (CDN injoignable)."));
    document.head.append(script);
  });
}

// Un seul module WASM partagé entre les fichiers déposés.
function loadLazPerf() {
  if (!lazPerfPromise) {
    lazPerfPromise = (async () => {
      if (!window.createLazPerf) await loadScript(LAZ_PERF_URL);
      return window.createLazPerf();
    })();
    lazPerfPromise.catch(() => {
      lazPerfPromise = null;
    });
  }
  return lazPerfPromise;
}

const nextTick = () => new Promise((resolve) => setTimeout(resolve, 0));

// Fichier d'accompagnement <nom>.laz.json : CRS (EPSG) et datum d'altitude.
async function readSidecar(file) {
  const meta = JSON.parse(await file.text());
  const match = /^EPSG:(\d+)$/i.exec(meta.crs ?? "");
  const urls = (meta.copc_urls ?? []).join(" ");
  return {
    epsg: match ? Number(match[1]) : null,
    verticalDatum: /IGN69/i.test(urls) ? "IGN69" : null,
  };
}

// Appelle visit(view, base, index) pour chaque enregistrement, dans l'ordre.
async function readRecords(laz, buffer, header, visit, onProgress) {
  if (!header.compressed) {
    const view = new DataView(buffer);
    for (let i = 0; i < header.pointCount; i++) {
      visit(view, header.offsetToPointData + i * header.pointLength, i);
      if (i > 0 && i % YIELD_EVERY === 0) {
        onProgress(i / header.pointCount);
        await nextTick();
      }
    }
    return header.pointCount;
  }

  const input = laz._malloc(buffer.byteLength);
  let zip = null;
  let scratch = 0;
  try {
    laz.HEAPU8.set(new Uint8Array(buffer), input);
    zip = new laz.LASZip();
    zip.open(input, buffer.byteLength);
    const count = zip.getCount();
    const length = zip.getPointLength();
    scratch = laz._malloc(length);
    // La mémoire WASM peut grossir (tampon détaché) : la vue est recréée si besoin.
    let heap = laz.HEAPU8.buffer;
    let view = new DataView(heap, scratch, length);
    for (let i = 0; i < count; i++) {
      zip.getPoint(scratch);
      if (laz.HEAPU8.buffer !== heap) {
        heap = laz.HEAPU8.buffer;
        view = new DataView(heap, scratch, length);
      }
      visit(view, 0, i);
      if (i > 0 && i % YIELD_EVERY === 0) {
        onProgress(i / count);
        await nextTick();
      }
    }
    return count;
  } finally {
    if (scratch) laz._free(scratch);
    zip?.delete();
    laz._free(input);
  }
}

// Sous-échantillonnage régulier : au plus MAX_DISPLAY_POINTS points conservés.
async function decodePoints(laz, buffer, header, onProgress) {
  const stride = Math.max(1, Math.ceil(header.pointCount / MAX_DISPLAY_POINTS));
  const capacity = Math.ceil(header.pointCount / stride);
  const abs = new Float64Array(capacity * 3);
  const classes = new Uint8Array(capacity);
  const rgb = header.hasRgb ? new Uint16Array(capacity * 3) : null;
  let kept = 0;
  let maxRgb = 0;

  const visit = (view, base, index) => {
    if (index % stride !== 0 || kept >= capacity) return;
    const p = decodePointRecord(view, base, header);
    abs[kept * 3] = p.x;
    abs[kept * 3 + 1] = p.y;
    abs[kept * 3 + 2] = p.z;
    classes[kept] = p.classification;
    if (rgb) {
      for (let c = 0; c < 3; c++) {
        rgb[kept * 3 + c] = p.rgb[c];
        if (p.rgb[c] > maxRgb) maxRgb = p.rgb[c];
      }
    }
    kept++;
  };

  await readRecords(laz, buffer, header, visit, onProgress);
  return {
    abs: abs.subarray(0, kept * 3),
    classes: classes.subarray(0, kept),
    rgb: rgb ? rgb.subarray(0, kept * 3) : null,
    maxRgb,
    count: kept,
  };
}

function buildColors(decoded) {
  const { classes, rgb, maxRgb, count } = decoded;
  const colors = new Float32Array(count * 3);
  if (rgb) {
    // Les RGB LAS sont sur 16 bits ; des valeurs ≤ 255 indiquent un 8 bits.
    const scale = maxRgb > 255 ? 1 / 65535 : 1 / 255;
    for (let i = 0; i < count * 3; i++) colors[i] = rgb[i] * scale;
    return colors;
  }
  for (let i = 0; i < count; i++) {
    const color = classColor(classes[i]);
    colors[i * 3] = color[0];
    colors[i * 3 + 1] = color[1];
    colors[i * 3 + 2] = color[2];
  }
  return colors;
}

// Centre local (en mètres du CRS du LAZ) et positions relatives en float32.
function buildSource(name, header, decoded) {
  const { abs, count } = decoded;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < count; i++) {
    for (let axis = 0; axis < 3; axis++) {
      const v = abs[i * 3 + axis];
      if (v < min[axis]) min[axis] = v;
      if (v > max[axis]) max[axis] = v;
    }
  }
  const centre = min.map((v, axis) => (v + max[axis]) / 2);
  const rel = new Float32Array(count * 3);
  for (let i = 0; i < count * 3; i++) {
    rel[i] = abs[i] - centre[i % 3];
  }
  return {
    name,
    count,
    epsg: header.epsg,
    centre,
    rel,
    colors: buildColors(decoded),
    totalPoints: header.pointCount,
  };
}

// Transformation affine LAZ (CRS projeté) → repère monde de la scène.
export function placementFor(source, georef) {
  if (!georef) {
    return { mode: "local", note: "Aucun géoréférencement : nuage centré à l’origine." };
  }
  if (!source.epsg) {
    return { mode: "local", note: "CRS absent du LAZ : nuage centré à l’origine." };
  }
  if (georef.epsg !== source.epsg) {
    return {
      mode: "local",
      note: `CRS du LAZ (EPSG:${source.epsg}) ≠ scène (EPSG:${georef.epsg}) : nuage centré à l’origine.`,
    };
  }

  const [cx, cy, cz] = source.centre;
  const origin = georef.projectedToWorld(cx, cy, cz);
  // linear[ligne monde][axe projeté], par différences finies de 1 m.
  const linear = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let axis = 0; axis < 3; axis++) {
    const d = [0, 0, 0];
    d[axis] = 1;
    const w = georef.projectedToWorld(cx + d[0], cy + d[1], cz + d[2]);
    const delta = [w.x - origin.x, w.y - origin.y, w.h - origin.h];
    for (let row = 0; row < 3; row++) linear[row][axis] = delta[row];
  }
  return {
    mode: "georef",
    note: `Géoréférencé (EPSG:${source.epsg}).`,
    origin: [origin.x, origin.y, origin.h],
    linear,
  };
}

export function worldPositions(source, placement) {
  const { rel, count } = source;
  if (placement.mode === "local") return rel;

  const [ox, oy, oz] = placement.origin;
  const [[a, b, c], [d, e, f], [g, h, i]] = placement.linear;
  const out = new Float32Array(count * 3);
  for (let p = 0; p < count; p++) {
    const x = rel[p * 3];
    const y = rel[p * 3 + 1];
    const z = rel[p * 3 + 2];
    out[p * 3] = ox + a * x + b * y + c * z;
    out[p * 3 + 1] = oy + d * x + e * y + f * z;
    out[p * 3 + 2] = oz + g * x + h * y + i * z;
  }
  return out;
}

export function createLidarLayer({ app, setStatus, getFlipUp, getGeoref, registerGroup, onBoundsChanged }) {
  let disposed = false;
  let nextId = 1;
  let lastGeoref = null;
  const layers = new Map();

  function emitBounds(frame) {
    const boxes = [...layers.values()].map((layer) => layer.bounds).filter(Boolean);
    const bounds = boxes.length
      ? colmapBounds(boxes.flatMap((box) => [box.min, box.max]), Boolean(getFlipUp?.()))
      : null;
    onBoundsChanged?.(bounds, frame);
  }

  function destroyRender(layer) {
    layer.resource?.mesh?.destroy?.();
    layer.resource?.material?.destroy?.();
    layer.resource = null;
    layer.root?.destroy?.();
    layer.root = null;
  }

  function render(layer, georef) {
    destroyRender(layer);
    const placement = placementFor(layer.source, georef);
    layer.placement = placement;
    const positions = worldPositions(layer.source, placement);
    layer.bounds = colmapBounds([positions], false);

    layer.resource = makePointMesh(app, positions, layer.source.colors, POINT_SIZE);
    setMeshHighlight(layer.resource, layer.highlighted);
    const pointsEntity = new pc.Entity(`LiDAR points · ${layer.name}`);
    pointsEntity.addComponent("render", {
      type: "asset",
      meshInstances: [layer.resource.meshInstance],
    });

    layer.root = new pc.Entity(`LiDAR · ${layer.name}`);
    layer.root.addChild(pointsEntity);
    // Même retournement que les splats et le COLMAP.
    layer.root.setLocalEulerAngles(getFlipUp() ? 180 : 0, 0, 0);
    layer.root.enabled = layer.visible;
    app.root.addChild(layer.root);
    return placement;
  }

  function refreshPlacement() {
    if (disposed) return;
    const georef = getGeoref?.() ?? null;
    if (georef === lastGeoref) return;
    lastGeoref = georef;
    for (const layer of layers.values()) render(layer, georef);
    emitBounds(false);
  }

  async function loadFile(file, sidecarFile = null) {
    if (disposed) return;
    if (file.size > MAX_FILE_BYTES) {
      throw new Error("Fichier LAZ trop volumineux (limite : 1 Gio).");
    }

    setStatus(`Lecture de ${file.name}…`);
    const buffer = await file.arrayBuffer();
    const header = parseLasHeader(buffer);
    const sidecar = sidecarFile ? await readSidecar(sidecarFile) : null;
    if (sidecar?.epsg && !header.epsg) header.epsg = sidecar.epsg;
    if (sidecar?.verticalDatum) header.verticalDatum = sidecar.verticalDatum;

    setStatus("Chargement du décodeur LAZ…");
    const laz = await loadLazPerf();
    const decoded = await decodePoints(laz, buffer, header, (fraction) => {
      if (!disposed) setStatus(`Décodage de ${file.name} : ${Math.round(fraction * 100)} %`);
    });
    if (disposed) return;

    const source = buildSource(file.name, header, decoded);
    const id = `lidar-${nextId++}`;
    const layer = {
      id,
      name: file.name,
      source,
      visible: true,
      highlighted: false,
      root: null,
      resource: null,
      placement: null,
      unregister: null,
    };
    layers.set(id, layer);

    const georef = getGeoref?.() ?? null;
    lastGeoref = georef;
    const placement = render(layer, georef);

    layer.unregister = registerGroup?.({
      id,
      name: `${file.name} · ${source.count.toLocaleString("fr-FR")} points`,
      visible: true,
      children: [{
        name: "Points",
        setVisible(visible) {
          layer.visible = visible;
          if (layer.root) layer.root.enabled = visible;
        },
        setHighlight(on) {
          layer.highlighted = on;
          setMeshHighlight(layer.resource, on);
        },
      }],
      onRemove: () => {
        layer.unregister?.();
        destroyRender(layer);
        layers.delete(id);
        emitBounds(false);
      },
    }) ?? null;

    emitBounds(true);

    const sampled = source.count < source.totalPoints
      ? ` (sous-échantillonné : ${source.count.toLocaleString("fr-FR")} / ${source.totalPoints.toLocaleString("fr-FR")})`
      : "";
    const datum = header.verticalDatum ? ` Altitudes : ${header.verticalDatum}.` : "";
    setStatus(`${file.name} chargé${sampled}. ${placement.note}${datum}`);
  }

  function clear() {
    for (const layer of layers.values()) {
      layer.unregister?.();
      destroyRender(layer);
    }
    layers.clear();
    emitBounds(false);
  }

  return {
    loadFile,
    clear,
    refreshPlacement,

    setFlipUp(flip) {
      for (const layer of layers.values()) {
        layer.root?.setLocalEulerAngles(flip ? 180 : 0, 0, 0);
      }
      emitBounds(false);
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      clear();
    },
  };
}
