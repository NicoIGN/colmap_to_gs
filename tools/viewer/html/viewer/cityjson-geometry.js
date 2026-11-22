// Lecture d'un CityJSON : surfaces triangulées avec une normale par triangle, pour un rendu ombré.

// Les CityJSON déclarent leur CRS dans metadata.referenceSystem (ex. "urn:ogc:def:crs:EPSG::2154").
export function epsgOfCityJson(json) {
  const reference = String(json.metadata?.referenceSystem ?? "");
  const match = /EPSG[^]*?(\d+)\s*$/i.exec(reference);
  return match ? Number(match[1]) : null;
}

export function isCityJsonText(text) {
  return /"type"\s*:\s*"CityJSON"/.test(text);
}

// Un triangle est un toit si sa normale (repère source, Z vers le haut) pointe assez vers le ciel.
// Les autres (façades, surfaces au sol) vont dans la catégorie façade.
export const ROOF_NORMAL_Z = 0.5;

export function splitRoofFacade(source, threshold = ROOF_NORMAL_Z) {
  const roofTriangles = [];
  const facadeTriangles = [];
  for (let t = 0; t < source.triangleCount; t++) {
    (source.normals[t * 9 + 2] >= threshold ? roofTriangles : facadeTriangles).push(t);
  }
  const pick = (indices) => {
    const rel = new Float32Array(indices.length * 9);
    const normals = new Float32Array(indices.length * 9);
    indices.forEach((t, i) => {
      rel.set(source.rel.subarray(t * 9, t * 9 + 9), i * 9);
      normals.set(source.normals.subarray(t * 9, t * 9 + 9), i * 9);
    });
    return { rel, normals, triangleCount: indices.length, vertexCount: indices.length * 3 };
  };
  return { roof: pick(roofTriangles), facade: pick(facadeTriangles) };
}

// Les sommets sont quantifiés (entiers) quand un transform est présent : x = v * scale + translate.
function decodeVertices(json) {
  const { scale, translate } = json.transform ?? {};
  return (json.vertices ?? []).map(([x, y, z]) =>
    scale && translate
      ? [x * scale[0] + translate[0], y * scale[1] + translate[1], z * scale[2] + translate[2]]
      : [x, y, z]
  );
}

const SUPPORTED_TYPES = new Set(["MultiSurface", "CompositeSurface", "Solid", "MultiSolid", "CompositeSolid"]);

// Liste les surfaces (anneaux de sommets) selon le type de géométrie CityJSON.
function surfacesOf(geometry) {
  const { type, boundaries } = geometry;
  const surfaces = [];
  if (type === "MultiSurface" || type === "CompositeSurface") {
    surfaces.push(...boundaries);
  } else if (type === "Solid") {
    for (const shell of boundaries) surfaces.push(...shell);
  } else {
    for (const solid of boundaries) for (const shell of solid) surfaces.push(...shell);
  }
  return surfaces;
}

// Un CityObject peut porter plusieurs LoD : on garde le plus détaillé pour éviter les doublons. Le LoD0 (empreinte) est ignoré.
function bestGeometry(object) {
  let best = null;
  for (const geometry of object.geometry ?? []) {
    if (!SUPPORTED_TYPES.has(geometry.type)) continue;
    if (Number(geometry.lod) === 0) continue;
    if (!best || Number(geometry.lod) > Number(best.lod)) best = geometry;
  }
  return best;
}

function sub(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

// Normale de Newell : robuste à l'ordre des sommets et aux polygones non convexes.
function newellNormal(ring) {
  const n = [0, 0, 0];
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i];
    const b = ring[(i + 1) % ring.length];
    n[0] += (a[1] - b[1]) * (a[2] + b[2]);
    n[1] += (a[2] - b[2]) * (a[0] + b[0]);
    n[2] += (a[0] - b[0]) * (a[1] + b[1]);
  }
  return n;
}

// Projection 2D qui abandonne l'axe dominant de la normale (triangulation stable).
function projectionOf(normal) {
  const [x, y, z] = normal.map(Math.abs);
  if (x >= y && x >= z) return (p) => [p[1], p[2]];
  if (y >= z) return (p) => [p[2], p[0]];
  return (p) => [p[0], p[1]];
}

function orient2d(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function insideTriangle(p, a, b, c) {
  const d1 = orient2d(a, b, p);
  const d2 = orient2d(b, c, p);
  const d3 = orient2d(c, a, p);
  return d1 >= 0 && d2 >= 0 && d3 >= 0;
}

// Écrêtage d'oreilles (O(n²) par anneau, suffisant pour les faces de bâtiments).
// Les trous des surfaces ne sont pas pris en compte.
function earClip(points2d) {
  const n = points2d.length;
  const order = [...Array(n).keys()];
  let area = 0;
  for (let i = 0; i < n; i++) {
    const a = points2d[i];
    const b = points2d[(i + 1) % n];
    area += a[0] * b[1] - b[0] * a[1];
  }
  if (area < 0) order.reverse();

  const triangles = [];
  let guard = n * n;
  while (order.length > 3 && guard-- > 0) {
    let clipped = false;
    for (let i = 0; i < order.length; i++) {
      const ia = order[(i - 1 + order.length) % order.length];
      const ib = order[i];
      const ic = order[(i + 1) % order.length];
      const a = points2d[ia];
      const b = points2d[ib];
      const c = points2d[ic];
      if (orient2d(a, b, c) <= 0) continue;
      const blocked = order.some(
        (p) => p !== ia && p !== ib && p !== ic && insideTriangle(points2d[p], a, b, c)
      );
      if (blocked) continue;
      triangles.push([ia, ib, ic]);
      order.splice(i, 1);
      clipped = true;
      break;
    }
    if (!clipped) break;
  }
  if (order.length === 3) triangles.push([order[0], order[1], order[2]]);
  return triangles;
}

// Triangles d'une surface : [[a, b, c], ...] en coordonnées 3D.
function triangulateSurface(ring) {
  const normal = newellNormal(ring);
  const project = projectionOf(normal);
  return earClip(ring.map(project)).map((tri) => tri.map((i) => ring[i]));
}

// `out` stocke 6 valeurs par sommet (x, y, z, nx, ny, nz) : 18 par triangle.
const FLOATS_PER_TRIANGLE = 18;

// Parcourt les surfaces d'un objet et ajoute leurs triangles à `out`. Retourne le nombre de trous ignorés.
function appendObjectTriangles(geometry, vertices, out, limit) {
  let holes = 0;
  for (const surface of surfacesOf(geometry)) {
    if (out.length / FLOATS_PER_TRIANGLE >= limit) break;
    const outer = surface[0] ?? [];
    holes += Math.max(0, surface.length - 1);
    let ring = outer.map((index) => vertices[index]).filter(Boolean);
    if (ring.length > 3) {
      const first = ring[0];
      const last = ring[ring.length - 1];
      if (first[0] === last[0] && first[1] === last[1] && first[2] === last[2]) ring = ring.slice(0, -1);
    }
    if (ring.length < 3) continue;
    // La triangulation travaille en 2D : on réoriente chaque triangle selon la normale 3D de la surface.
    const surfaceNormal = newellNormal(ring);
    for (const triangle of triangulateSurface(ring)) {
      let [a, b, c] = triangle;
      if (dot(cross(sub(b, a), sub(c, a)), surfaceNormal) < 0) [b, c] = [c, b];
      const normal = cross(sub(b, a), sub(c, a));
      const length = Math.hypot(...normal) || 1;
      const unit = normal.map((v) => v / length);
      for (const p of [a, b, c]) out.push(p[0], p[1], p[2], unit[0], unit[1], unit[2]);
    }
  }
  return holes;
}

// Résultat : triangles en coordonnées réelles (x, y, z, nx, ny, nz) centrés puis convertis en float32.
export function readCityJson(text, name, { maxTriangles = Infinity } = {}) {
  let json;
  try {
    json = JSON.parse(text);
  } catch (error) {
    throw new Error(`JSON invalide : ${error.message}`);
  }
  if (json.type !== "CityJSON") throw new Error("Ce fichier n’est pas un CityJSON (champ « type » absent).");

  const vertices = decodeVertices(json);
  const flat = [];
  let objects = 0;
  let holes = 0;
  let unsupported = 0;
  for (const object of Object.values(json.CityObjects ?? {})) {
    const geometry = bestGeometry(object);
    if (!geometry) {
      unsupported++;
      continue;
    }
    objects++;
    holes += appendObjectTriangles(geometry, vertices, flat, maxTriangles);
    if (flat.length / FLOATS_PER_TRIANGLE >= maxTriangles) break;
  }

  const triangleCount = flat.length / FLOATS_PER_TRIANGLE;
  const vertexCount = triangleCount * 3;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let v = 0; v < vertexCount; v++) {
    for (let k = 0; k < 3; k++) {
      min[k] = Math.min(min[k], flat[v * 6 + k]);
      max[k] = Math.max(max[k], flat[v * 6 + k]);
    }
  }
  const centre = vertexCount ? min.map((value, k) => (value + max[k]) / 2) : [0, 0, 0];
  const rel = new Float32Array(vertexCount * 3);
  const normals = new Float32Array(vertexCount * 3);
  for (let v = 0; v < vertexCount; v++) {
    for (let k = 0; k < 3; k++) {
      rel[v * 3 + k] = flat[v * 6 + k] - centre[k];
      normals[v * 3 + k] = flat[v * 6 + 3 + k];
    }
  }

  return {
    name,
    epsg: epsgOfCityJson(json),
    centre,
    rel,
    normals,
    vertexCount,
    triangleCount,
    objectCount: objects,
    unsupportedCount: unsupported,
    holeCount: holes,
    truncated: triangleCount >= maxTriangles,
  };
}
