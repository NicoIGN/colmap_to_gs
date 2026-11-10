import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (await readFile(new URL("./html/viewer/colmap.js", import.meta.url), "utf8"))
  .replace('import * as pc from "playcanvas";', "const pc = globalThis.testPlayCanvas;");
class TestEntity {
  addChild(child) { child.parent = this; }
  removeChild(child) { child.parent = null; }
  addComponent() {}
  setLocalEulerAngles() {}
  destroy() {}
}
globalThis.testPlayCanvas = {
  Entity: TestEntity,
  ShaderMaterial: class { setParameter() {} update() {} destroy() {} },
  Mesh: class { setPositions() {} setColors() {} update() {} destroy() {} },
  MeshInstance: class {},
};
const { cameraFrustumDepth, colmapPointPixels, thickenLineSegments, colmapBounds, createColmapLayer } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);
delete globalThis.testPlayCanvas;

test("COLMAP loading, flipping and removal notify the navigation bounds", async () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const input = { addEventListener() {}, removeEventListener() {} };
  globalThis.document = { getElementById: (id) => id === "colmapFolderInput" ? input : null };
  globalThis.window = { devicePixelRatio: 1 };
  const updates = [];
  const statuses = [];
  try {
    const layer = createColmapLayer({
      app: { root: new TestEntity(), graphicsDevice: {} },
      getSceneRadius: () => 1,
      getFlipUp: () => false,
      setStatus: (...args) => statuses.push(args),
      onBoundsChanged: (bounds, frame) => updates.push({ bounds, frame }),
    });
    const binary = (...fields) => {
      const buffer = Buffer.alloc(fields.reduce((size, [type]) =>
        size + ({ u64: 8, f64: 8, u32: 4, u8: 1 })[type], 0));
      let offset = 0;
      for (const [type, value] of fields) {
        if (type === "u64") { buffer.writeBigUInt64LE(BigInt(value), offset); offset += 8; }
        if (type === "f64") { buffer.writeDoubleLE(value, offset); offset += 8; }
        if (type === "u32") { buffer.writeUInt32LE(value, offset); offset += 4; }
        if (type === "u8") { buffer.writeUInt8(value, offset); offset++; }
      }
      return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
    };
    const cameras = binary(["u64", 1], ["u32", 1], ["u32", 0], ["u64", 800], ["u64", 600],
      ["f64", 600], ["f64", 400], ["f64", 300]);
    const images = binary(["u64", 1], ["u32", 1],
      ...[1, 0, 0, 0, -10, -20, -30].map((x) => ["f64", x]),
      ["u32", 1], ["u8", 0], ["u64", 0]);
    const points = binary(["u64", 1], ["u64", 1],
      ...[12, 24, 36].map((x) => ["f64", x]),
      ["u8", 255], ["u8", 255], ["u8", 255], ["f64", 0], ["u64", 0]);
    await layer.loadFromFileList([
      { name: "cameras.bin", arrayBuffer: async () => cameras },
      { name: "images.bin", arrayBuffer: async () => images },
      { name: "points3D.bin", arrayBuffer: async () => points },
    ]);
    assert.equal(statuses.at(-1)[1], "success", JSON.stringify(statuses));
    assert.deepEqual(updates.at(-1), {
      bounds: { min: [10, 20, 30], max: [12, 24, 36] }, frame: true,
    });
    layer.setFlipUp(true);
    assert.deepEqual(updates.at(-1), {
      bounds: { min: [10, -24, -36], max: [12, -20, -30] }, frame: false,
    });
    layer.clear();
    assert.equal(updates.at(-1).bounds, null);
  } finally {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  }
});

test("COLMAP bounds include sparse points and camera centers without splats", () => {
  const positions = [[10, 20, 30, 12, 24, 36], [8, 18, 28]];
  assert.deepEqual(colmapBounds(positions), {
    min: [8, 18, 28], max: [12, 24, 36],
  });
  assert.deepEqual(colmapBounds(positions, true), {
    min: [8, -24, -36], max: [12, -18, -28],
  });
  assert.deepEqual(colmapBounds([[1, 2, 3], [NaN, 5, 6]]), {
    min: [1, 2, 3], max: [1, 2, 3],
  });
  assert.equal(colmapBounds([[], [Infinity, 0, 0]]), null);
});

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
