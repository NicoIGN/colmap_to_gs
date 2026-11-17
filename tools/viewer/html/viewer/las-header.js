const LAS_HEADER_MIN_SIZE = 227;
const VLR_HEADER_SIZE = 54;
const GEOKEY_DIRECTORY_RECORD = 34735;
const OGC_WKT_RECORD = 2112;
const GEOKEY_GEOGRAPHIC_TYPE = 2048;
const GEOKEY_PROJECTED_CS_TYPE = 3072;
const GEOKEY_UNDEFINED = 32767;

// Taille minimale de l'enregistrement, offset RGB (ou null) et offset/masque
// de la classification selon le format de point LAS 1.2 à 1.4.
const POINT_LAYOUTS = {
  0: { length: 20, rgbOffset: null, classOffset: 15, classMask: 0x1f },
  1: { length: 28, rgbOffset: null, classOffset: 15, classMask: 0x1f },
  2: { length: 26, rgbOffset: 20, classOffset: 15, classMask: 0x1f },
  3: { length: 34, rgbOffset: 28, classOffset: 15, classMask: 0x1f },
  4: { length: 57, rgbOffset: null, classOffset: 15, classMask: 0x1f },
  5: { length: 63, rgbOffset: 28, classOffset: 15, classMask: 0x1f },
  6: { length: 30, rgbOffset: null, classOffset: 16, classMask: 0xff },
  7: { length: 36, rgbOffset: 30, classOffset: 16, classMask: 0xff },
  8: { length: 38, rgbOffset: 30, classOffset: 16, classMask: 0xff },
  9: { length: 59, rgbOffset: null, classOffset: 16, classMask: 0xff },
  10: { length: 67, rgbOffset: 30, classOffset: 16, classMask: 0xff },
};

function readAscii(view, offset, length) {
  const bytes = new Uint8Array(view.buffer, view.byteOffset + offset, length);
  return String.fromCharCode(...bytes).replace(/\0+$/, "");
}

function readVlrs(view, start, count) {
  const records = [];
  let offset = start;
  for (let i = 0; i < count; i++) {
    if (offset + VLR_HEADER_SIZE > view.byteLength) break;
    const userId = readAscii(view, offset + 2, 16);
    const recordId = view.getUint16(offset + 18, true);
    const length = view.getUint16(offset + 20, true);
    const dataStart = offset + VLR_HEADER_SIZE;
    if (dataStart + length > view.byteLength) break;
    records.push({
      userId,
      recordId,
      data: new DataView(view.buffer, view.byteOffset + dataStart, length),
    });
    offset = dataStart + length;
  }
  return records;
}

// Lit les clés GeoTIFF : ProjectedCSTypeGeoKey prime sur GeographicTypeGeoKey.
function epsgFromGeoKeys(data) {
  if (data.byteLength < 8) return null;
  const keyCount = data.getUint16(6, true);
  let geographic = null;
  for (let i = 0; i < keyCount; i++) {
    const offset = 8 + i * 8;
    if (offset + 8 > data.byteLength) break;
    const keyId = data.getUint16(offset, true);
    const location = data.getUint16(offset + 2, true);
    const value = data.getUint16(offset + 6, true);
    if (location !== 0 || value === 0 || value === GEOKEY_UNDEFINED) continue;
    if (keyId === GEOKEY_PROJECTED_CS_TYPE) return value;
    if (keyId === GEOKEY_GEOGRAPHIC_TYPE) geographic = value;
  }
  return geographic;
}

// Repli : dernier AUTHORITY["EPSG","..."] du WKT (le CRS racine est en fin).
function epsgFromWkt(data) {
  const text = new TextDecoder().decode(data);
  const matches = [...text.matchAll(/AUTHORITY\["EPSG","(\d+)"\]/g)];
  return matches.length ? Number(matches.at(-1)[1]) : null;
}

export function readLasEpsg(view, headerSize, vlrCount) {
  const records = readVlrs(view, headerSize, vlrCount);
  const geoKeys = records.find(
    (record) =>
      record.userId === "LASF_Projection" && record.recordId === GEOKEY_DIRECTORY_RECORD
  );
  const fromKeys = geoKeys ? epsgFromGeoKeys(geoKeys.data) : null;
  if (fromKeys) return fromKeys;

  const wkt = records.find(
    (record) => record.userId === "LASF_Projection" && record.recordId === OGC_WKT_RECORD
  );
  return wkt ? epsgFromWkt(wkt.data) : null;
}

export function parseLasHeader(buffer) {
  if (buffer.byteLength < LAS_HEADER_MIN_SIZE) {
    throw new Error("Fichier LAS/LAZ trop court pour contenir un en-tête.");
  }
  const view = new DataView(buffer);
  if (readAscii(view, 0, 4) !== "LASF") {
    throw new Error("Signature LASF absente : ce n’est pas un fichier LAS/LAZ.");
  }

  const versionMajor = view.getUint8(24);
  const versionMinor = view.getUint8(25);
  if (versionMajor !== 1) {
    throw new Error(`Version LAS ${versionMajor}.${versionMinor} non supportée.`);
  }

  const headerSize = view.getUint16(94, true);
  const offsetToPointData = view.getUint32(96, true);
  const vlrCount = view.getUint32(100, true);
  const formatByte = view.getUint8(104);
  const pointFormat = formatByte & 0x3f;
  const compressed = (formatByte & 0x80) !== 0;
  const pointLength = view.getUint16(105, true);

  const layout = POINT_LAYOUTS[pointFormat];
  if (!layout) throw new Error(`Format de point LAS ${pointFormat} non supporté.`);
  if (pointLength < layout.length) {
    throw new Error(`Taille d’enregistrement invalide (${pointLength} octets).`);
  }

  let pointCount = view.getUint32(107, true);
  if (versionMinor >= 4 && buffer.byteLength >= 255) {
    const extendedCount = Number(view.getBigUint64(247, true));
    if (extendedCount > 0) pointCount = extendedCount;
  }

  if (offsetToPointData > buffer.byteLength) {
    throw new Error("Données de points absentes du fichier.");
  }

  return {
    versionMajor,
    versionMinor,
    pointFormat,
    compressed,
    pointLength,
    pointCount,
    offsetToPointData,
    scale: [view.getFloat64(131, true), view.getFloat64(139, true), view.getFloat64(147, true)],
    offset: [view.getFloat64(155, true), view.getFloat64(163, true), view.getFloat64(171, true)],
    epsg: readLasEpsg(view, headerSize, vlrCount),
    hasRgb: layout.rgbOffset !== null,
    rgbOffset: layout.rgbOffset,
    classOffset: layout.classOffset,
    classMask: layout.classMask,
  };
}

// Lit un enregistrement brut LAS non compressé (ou décompressé par laz-perf).
export function decodePointRecord(view, base, header) {
  const [sx, sy, sz] = header.scale;
  const [ox, oy, oz] = header.offset;
  const point = {
    x: view.getInt32(base, true) * sx + ox,
    y: view.getInt32(base + 4, true) * sy + oy,
    z: view.getInt32(base + 8, true) * sz + oz,
    classification: view.getUint8(base + header.classOffset) & header.classMask,
    rgb: null,
  };
  if (header.hasRgb) {
    const o = base + header.rgbOffset;
    point.rgb = [view.getUint16(o, true), view.getUint16(o + 2, true), view.getUint16(o + 4, true)];
  }
  return point;
}

// Palette ASPRS usuelle ; les classes utilisateur reçoivent un gris bleuté.
const CLASS_COLORS = {
  0: [0.6, 0.6, 0.6],
  1: [0.75, 0.75, 0.75],
  2: [0.55, 0.38, 0.2],
  3: [0.7, 0.9, 0.45],
  4: [0.45, 0.75, 0.25],
  5: [0.15, 0.5, 0.15],
  6: [0.9, 0.2, 0.2],
  7: [1, 0, 1],
  9: [0.2, 0.4, 0.9],
  17: [0.6, 0.6, 0.9],
};
const DEFAULT_CLASS_COLOR = [0.5, 0.5, 0.8];

export function classColor(classification) {
  return CLASS_COLORS[classification] ?? DEFAULT_CLASS_COLOR;
}
