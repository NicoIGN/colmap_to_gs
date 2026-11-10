import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (await readFile(new URL("./html/viewer/colmap.js", import.meta.url), "utf8"))
  .replace('import * as pc from "playcanvas";', "");
const { cameraFrustumDepth } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

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
