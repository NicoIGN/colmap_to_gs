import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (await readFile(new URL("./html/viewer/colmap.js", import.meta.url), "utf8"))
  .replace('import * as pc from "playcanvas";', "");
const { cameraFrustumDepth, colmapPointPixels, thickenLineSegments } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

test("frustum strokes become triangles with scale-relative thickness", () => {
  const line = [0, 0, 0, 0, 0, 2];
  const thick = thickenLineSegments(line, 0.02);
  assert.equal(thick.length, 18);
  const points = Array.from({ length: 6 }, (_, index) =>
    Array.from(thick.slice(index * 3, index * 3 + 3)));
  assert.equal(points[0][2], 0);
  assert.equal(points[2][2], 2);
  assert.equal(points[0][0], -points[1][0]);
  assert.ok(Math.abs(Math.abs(points[0][0]) - 0.01) < 1e-8);
  assert.equal(thickenLineSegments([0, 0, 0, 0, 0, 0], 1).length, 0);
});

test("point sizes are screen pixels, with DPI and hardware limits", () => {
  assert.equal(colmapPointPixels(2, 1), 2);
  assert.equal(colmapPointPixels(4, 2), 8);
  assert.equal(colmapPointPixels(4, 3), 8);
  assert.equal(colmapPointPixels(10, 2, 12), 12);
  assert.equal(colmapPointPixels(0.5, 1), 1);
});
test("frustums follow SfM scale without an absolute minimum", () => {
  const centers = [0, 0, 0, 1, 2, 3, 2, 4, 6];
  const depth = cameraFrustumDepth(centers, 1000);
  for (const scale of [1e-6, 1, 1000]) {
    const actual = cameraFrustumDepth(centers.map((x) => x * scale), 1000);
    assert.ok(Math.abs(actual / (depth * scale) - 1) < 1e-12);
  }
  assert.ok(cameraFrustumDepth(centers.map((x) => x * 1e-6), 1000) < 0.1);
});

test("distant outliers and splat scene radius do not inflate camera extent", () => {
  const centers = Array.from({ length: 100 }, (_, i) => [i / 100, 0, 0]).flat();
  const depth = cameraFrustumDepth(centers, 1e9);
  centers.push(1e9, 1e9, 1e9);
  assert.ok(cameraFrustumDepth(centers, 1e9) < depth * 1.1);
});

test("degenerate camera path uses scene radius or reports invalid scale", () => {
  assert.equal(cameraFrustumDepth([0, 0, 0], 0.001), 0.00001);
  assert.throws(() => cameraFrustumDepth([0, 0, 0], 0), /Échelle COLMAP/);
});
