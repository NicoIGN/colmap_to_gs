// Lecture d'un GeoJSON : points, lignes et surfaces → points et polylignes, comme pour le GeoPackage.

// RFC 7946 : sans membre "crs", les coordonnées sont en WGS84.
const DEFAULT_EPSG = 4326;

// Code EPSG du membre "crs" (ex. "urn:ogc:def:crs:EPSG::2154" ou "EPSG:2154").
function epsgOf(geojson) {
  const name = geojson.crs?.properties?.name ?? "";
  const match = /EPSG:{1,2}(\d+)$/i.exec(name);
  return match ? Number(match[1]) : DEFAULT_EPSG;
}

// Ajoute à `out` les polylignes (tableaux plats x, y, z) et à `points` les points (x, y, z à plat).
function collectGeometry(geometry, out, points) {
  if (!geometry) return;
  const { type, coordinates } = geometry;
  const line = (positions) => {
    const flat = [];
    for (const [x, y, z = 0] of positions) flat.push(x, y, z);
    if (flat.length >= 6) out.push(flat);
  };
  if (type === "Point") {
    const [x, y, z = 0] = coordinates;
    points.push(x, y, z);
  } else if (type === "MultiPoint") {
    for (const [x, y, z = 0] of coordinates) points.push(x, y, z);
  } else if (type === "LineString") {
    line(coordinates);
  } else if (type === "MultiLineString" || type === "Polygon") {
    for (const part of coordinates) line(part);
  } else if (type === "MultiPolygon") {
    for (const polygon of coordinates) for (const ring of polygon) line(ring);
  } else if (type === "GeometryCollection") {
    for (const part of geometry.geometries) collectGeometry(part, out, points);
  }
}

// Placement sans géoréférencement : chaque couche est décalée de `centre - reference`, pour garder
// les écarts relatifs entre fichiers (sinon chaque couche serait recentrée à l'origine).
export function sharedLocalPlacement(centre, reference, note) {
  return {
    mode: "shared",
    note,
    origin: centre.map((value, axis) => value - reference[axis]),
    linear: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  };
}

// Tables équivalentes à celles du GeoPackage : une seule couche nommée d'après le fichier.
export function readGeojsonLayers(text, name) {
  const geojson = JSON.parse(text);
  const epsg = epsgOf(geojson);
  const polylines = [];
  const points = [];
  if (geojson.type === "FeatureCollection") {
    for (const feature of geojson.features ?? []) collectGeometry(feature.geometry, polylines, points);
  } else if (geojson.type === "Feature") {
    collectGeometry(geojson.geometry, polylines, points);
  } else {
    collectGeometry(geojson, polylines, points);
  }
  return [{ name, epsg, polylines, points }];
}
