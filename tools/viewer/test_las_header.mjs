import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./html/viewer/las-header.js", import.meta.url), "utf8");
const { parseLasHeader, decodePointRecord, classColor } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

// Construit un LAS 1.2 non compressé, format 3, avec un VLR GeoKeys EPSG:2154.
function buildLas({ epsg = 2154, points = [] } = {}) {
  const geoKeys = [1, 1, 0, 1, 3072, 0, 1, epsg];
  const vlrData = new Uint8Array(new Uint16Array(geoKeys).buffer);
  const vlrSize = 54 + vlrData.length;
  const headerSize = 227;
  const pointLength = 34;
  const offsetToPointData = headerSize + vlrSize;
  const buffer = new ArrayBuffer(offsetToPointData + points.length * pointLength);
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);

  bytes.set(new TextEncoder().encode("LASF"), 0);
  view.setUint8(24, 1);
  view.setUint8(25, 2);
  view.setUint16(94, headerSize, true);
  view.setUint32(96, offsetToPointData, true);
  view.setUint32(100, 1, true);
  view.setUint8(104, 3);
  view.setUint16(105, pointLength, true);
  view.setUint32(107, points.length, true);
  view.setFloat64(131, 0.01, true);
  view.setFloat64(139, 0.01, true);
  view.setFloat64(147, 0.001, true);
  view.setFloat64(155, 600000, true);
  view.setFloat64(163, 6700000, true);
  view.setFloat64(171, 0, true);

  const vlr = headerSize;
  bytes.set(new TextEncoder().encode("LASF_Projection"), vlr + 2);
  view.setUint16(vlr + 18, 34735, true);
  view.setUint16(vlr + 20, vlrData.length, true);
  bytes.set(vlrData, vlr + 54);

  points.forEach((p, i) => {
    const base = offsetToPointData + i * pointLength;
    view.setInt32(base, p.x, true);
    view.setInt32(base + 4, p.y, true);
    view.setInt32(base + 8, p.z, true);
    view.setUint8(base + 15, p.classification);
    view.setUint16(base + 28, p.r, true);
    view.setUint16(base + 30, p.g, true);
    view.setUint16(base + 32, p.b, true);
  });
  return buffer;
}

test("parseLasHeader lit format, comptage, échelle et CRS EPSG", () => {
  const header = parseLasHeader(buildLas({ points: [{ x: 0, y: 0, z: 0, classification: 2, r: 1, g: 2, b: 3 }] }));
  assert.equal(header.pointFormat, 3);
  assert.equal(header.pointLength, 34);
  assert.equal(header.pointCount, 1);
  assert.equal(header.compressed, false);
  assert.equal(header.epsg, 2154);
  assert.deepEqual(header.scale, [0.01, 0.01, 0.001]);
  assert.deepEqual(header.offset, [600000, 6700000, 0]);
  assert.equal(header.hasRgb, true);
});

test("decodePointRecord applique échelle, offset, classe et RVB", () => {
  const buffer = buildLas({
    points: [{ x: 12345, y: -200, z: 7, classification: 6, r: 65535, g: 256, b: 0 }],
  });
  const header = parseLasHeader(buffer);
  const point = decodePointRecord(new DataView(buffer), header.offsetToPointData, header);
  assert.equal(point.x, 600000 + 123.45);
  assert.equal(point.y, 6700000 - 2);
  assert.equal(point.z, 0.007);
  assert.equal(point.classification, 6);
  assert.deepEqual(point.rgb, [65535, 256, 0]);
});

test("parseLasHeader rejette une signature invalide", () => {
  const bad = buildLas();
  new Uint8Array(bad)[0] = 0;
  assert.throws(() => parseLasHeader(bad), /LASF/);
});

test("classColor renvoie une couleur ASPRS connue ou un repli", () => {
  assert.deepEqual(classColor(2), [0.55, 0.38, 0.2]);
  assert.deepEqual(classColor(67), [0.5, 0.5, 0.8]);
});
