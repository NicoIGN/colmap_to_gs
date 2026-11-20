import assert from "node:assert/strict";
import test from "node:test";
import {
  polylinesToSegments,
  readGpkgGeometry,
} from "./html/viewer/gpkg-geometry.js";

// En-tête GeoPackage (GP, version 0, flags sans enveloppe, SRID 2154) + WKB little-endian.
function gpkgBlob(wkbType, body) {
  const header = new Uint8Array([0x47, 0x50, 0, 0x01, 0, 0, 0x08, 0x66]);
  const wkb = new Uint8Array(5 + body.length);
  const view = new DataView(wkb.buffer);
  view.setUint8(0, 1);
  view.setUint32(1, wkbType, true);
  wkb.set(body, 5);
  const out = new Uint8Array(header.length + wkb.length);
  out.set(header, 0);
  out.set(wkb, header.length);
  return out;
}

const u32 = (...values) => {
  const buf = new ArrayBuffer(values.length * 4);
  values.forEach((v, i) => new DataView(buf).setUint32(i * 4, v, true));
  return new Uint8Array(buf);
};

function coords(points, stride = 2) {
  const buf = new ArrayBuffer(points.length * stride * 8);
  const view = new DataView(buf);
  points.forEach((p, i) => {
    p.forEach((v, k) => view.setFloat64((i * stride + k) * 8, v, true));
  });
  return new Uint8Array(buf);
}

const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
};

test("readGpkgGeometry keeps LineString and closed Polygon rings as polylines", () => {
  const line = gpkgBlob(2, concat(u32(3), coords([[0, 0], [10, 0], [10, 5]])));
  const polygon = gpkgBlob(3, concat(u32(1, 4), coords([[0, 0], [4, 0], [4, 4], [0, 0]])));
  assert.deepEqual(readGpkgGeometry(line), [[0, 0, 0, 10, 0, 0, 10, 5, 0]]);
  assert.equal(readGpkgGeometry(polygon)[0].length, 12);
});

test("readGpkgGeometry flattens MultiLineString and reads ISO Z coordinates", () => {
  const multi = gpkgBlob(5, concat(
    u32(2),
    gpkgBlob(2, concat(u32(2), coords([[1, 1], [2, 2]]))).slice(8),
    gpkgBlob(1002, concat(u32(2), coords([[3, 3, 7], [4, 4, 8]], 3))).slice(8),
  ));
  const lines = readGpkgGeometry(multi);
  assert.equal(lines.length, 2);
  assert.deepEqual(lines[1], [3, 3, 7, 4, 4, 8]);
});

test("polylinesToSegments yields one 6-value segment per consecutive vertex pair", () => {
  const segments = polylinesToSegments([[0, 0, 0, 1, 0, 0, 1, 1, 0]]);
  assert.deepEqual([...segments], [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
});

test("readGpkgGeometry rejects a blob without the GP magic", () => {
  assert.throws(() => readGpkgGeometry(new Uint8Array([0, 0, 0, 1, 0, 0, 0, 0, 1])), /invalide/);
});

test("readGpkgGeometry collects Point and MultiPoint coordinates as points", () => {
  const points = [];
  assert.deepEqual(readGpkgGeometry(gpkgBlob(1, coords([[3, 4]])), points), []);
  assert.deepEqual(points, [3, 4, 0]);
  const multi = gpkgBlob(4, concat(u32(2), gpkgBlob(1, coords([[5, 6]])).subarray(8), gpkgBlob(1, coords([[7, 8]])).subarray(8)));
  readGpkgGeometry(multi, points);
  assert.deepEqual(points, [3, 4, 0, 5, 6, 0, 7, 8, 0]);
});
