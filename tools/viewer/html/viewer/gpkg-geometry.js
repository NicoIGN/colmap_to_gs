// Lecture d'un GeoPackage : tables d'entités → polylignes (géométries WKB).

const WKB_POINT = 1;
const WKB_LINESTRING = 2;
const WKB_POLYGON = 3;
const WKB_MULTIPOINT = 4;
const WKB_MULTILINESTRING = 5;
const WKB_MULTIPOLYGON = 6;
const WKB_COLLECTION = 7;
const ENVELOPE_BYTES = [0, 32, 48, 48, 64];

// Chaque géométrie d'un GeoPackage commence par un en-tête GP, suivi de WKB.
function wkbOffset(bytes) {
  if (bytes[0] !== 0x47 || bytes[1] !== 0x50) {
    throw new Error("Géométrie GeoPackage invalide.");
  }
  const envelope = ENVELOPE_BYTES[(bytes[3] >> 1) & 0x07];
  if (envelope === undefined) throw new Error("Enveloppe GeoPackage inconnue.");
  return 8 + envelope;
}

// Lit une géométrie WKB (ou EWKB / ISO Z, M, ZM) et ajoute ses polylignes à `out`.
function readWkb(view, offset, out, points) {
  const little = view.getUint8(offset) === 1;
  let code = view.getUint32(offset + 1, little);
  offset += 5;

  let hasZ = (code & 0x80000000) !== 0;
  let hasM = (code & 0x40000000) !== 0;
  if (code & 0x20000000) offset += 4;
  code &= 0x0fffffff;
  const dimension = Math.floor(code / 1000);
  if (dimension === 1 || dimension === 3) hasZ = true;
  if (dimension === 2 || dimension === 3) hasM = true;
  const type = code % 1000;

  const coordSize = 16 + (hasZ ? 8 : 0) + (hasM ? 8 : 0);
  const readLine = (count) => {
    const coords = new Array(count * 3);
    for (let i = 0; i < count; i++) {
      const base = offset + i * coordSize;
      coords[i * 3] = view.getFloat64(base, little);
      coords[i * 3 + 1] = view.getFloat64(base + 8, little);
      coords[i * 3 + 2] = hasZ ? view.getFloat64(base + 16, little) : 0;
    }
    offset += count * coordSize;
    return coords;
  };
  const readCount = () => {
    const count = view.getUint32(offset, little);
    offset += 4;
    return count;
  };

  if (type === WKB_POINT) {
    points.push(...readLine(1));
  } else if (type === WKB_LINESTRING) {
    out.push(readLine(readCount()));
  } else if (type === WKB_POLYGON) {
    const rings = readCount();
    for (let r = 0; r < rings; r++) out.push(readLine(readCount()));
  } else if (
    type === WKB_MULTIPOINT ||
    type === WKB_MULTILINESTRING ||
    type === WKB_MULTIPOLYGON ||
    type === WKB_COLLECTION
  ) {
    const parts = readCount();
    for (let p = 0; p < parts; p++) offset = readWkb(view, offset, out, points);
  } else {
    throw new Error(`Type de géométrie WKB non pris en charge : ${type}`);
  }
  return offset;
}

// Polylignes (tableaux plats x, y, z) d'une géométrie GeoPackage ; les points sont ajoutés à `points` (x, y, z à plat).
export function readGpkgGeometry(bytes, points = []) {
  const polylines = [];
  if (!bytes || bytes.length < 8) return polylines;
  const flags = bytes[3];
  if (flags & 0x10) return polylines;
  const offset = wkbOffset(bytes);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  readWkb(view, offset, polylines, points);
  return polylines;
}

// Segments [ax, ay, az, bx, by, bz] (un tableau par polyligne, enchaînés).
export function polylinesToSegments(polylines) {
  let segmentCount = 0;
  for (const line of polylines) segmentCount += Math.max(0, line.length / 3 - 1);
  const out = new Float64Array(segmentCount * 6);
  let o = 0;
  for (const line of polylines) {
    for (let i = 0; i + 3 < line.length; i += 3) {
      out.set(line.slice(i, i + 6), o);
      o += 6;
    }
  }
  return out;
}

function rowsOf(db, sql) {
  const [result] = db.exec(sql);
  if (!result) return [];
  return result.values.map((values) =>
    Object.fromEntries(result.columns.map((column, i) => [column, values[i]]))
  );
}

// Tables d'entités : nom, colonne géométrique, EPSG (si défini).
export function listGpkgFeatureTables(db) {
  return rowsOf(
    db,
    `SELECT c.table_name AS table_name, g.column_name AS column_name,
            s.organization AS organization, s.organization_coordsys_id AS coordsys
     FROM gpkg_contents c
     JOIN gpkg_geometry_columns g ON g.table_name = c.table_name
     LEFT JOIN gpkg_spatial_ref_sys s ON s.srs_id = g.srs_id
     WHERE c.data_type = 'features'
     ORDER BY c.table_name`
  ).map((row) => ({
    name: row.table_name,
    column: row.column_name,
    epsg: /^EPSG$/i.test(row.organization ?? "") ? Number(row.coordsys) : null,
  }));
}

const quote = (identifier) => `"${String(identifier).replaceAll('"', '""')}"`;

// Lit toutes les tables d'entités d'une base sql.js (Database.exec).
export function readGpkgLayers(db) {
  return listGpkgFeatureTables(db).map((table) => {
    const polylines = [];
    const points = [];
    const [result] = db.exec(`SELECT ${quote(table.column)} FROM ${quote(table.name)}`);
    for (const [blob] of result?.values ?? []) {
      if (blob) polylines.push(...readGpkgGeometry(blob, points));
    }
    return { ...table, polylines, points };
  });
}
