import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const imagesSource = await readFile(new URL("./html/viewer/colmap-images.js", import.meta.url), "utf8");
const imagesModuleUrl = `data:text/javascript;base64,${Buffer.from(imagesSource).toString("base64")}`;
const overlaySource = await readFile(new URL("./html/viewer/overlay-depth.js", import.meta.url), "utf8");
const overlayModuleUrl = `data:text/javascript;base64,${Buffer.from(overlaySource).toString("base64")}`;
const source = (await readFile(new URL("./html/viewer/colmap.js", import.meta.url), "utf8"))
  .replace('import * as pc from "playcanvas";', "const pc = globalThis.testPlayCanvas;")
  .replace('from "./colmap-images.js"', `from "${imagesModuleUrl}"`)
  .replace('from "./overlay-depth.js"', `from "${overlayModuleUrl}"`);
class TestEntity {
  addChild(child) { child.parent = this; }
  removeChild(child) { child.parent = null; }
  addComponent() {}
  setLocalEulerAngles() {}
  findComponents() { return []; }
  destroy() {}
}
globalThis.testPlayCanvas = {
  Entity: TestEntity,
  Layer: class { constructor({ name }) { this.name = name; this.id = 1; } },
  ShaderMaterial: class { setParameter() {} update() {} destroy() {} },
  Mesh: class { setPositions() {} setColors() {} update() {} destroy() {} },
  MeshInstance: class {},
};
const { cameraFrustumDepth, colmapPointPixels, thickenLineSegments, colmapBounds, createColmapLayer, equirectToCubeAtlas, coversViewCenter, rayFrustumEntry, rayHitsFarFace, rayBoxHit, alignCubeToView, panoramaSphere } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);
delete globalThis.testPlayCanvas;

test("a click ray picks the single pinhole frustum it crosses, and the far face resets it", () => {
  const image = { qvec: [1, 0, 0, 0], tvec: [0, 0, 0] };
  const camera = { modelName: "PINHOLE", width: 100, height: 100, params: [100, 100, 50, 50] };
  const depth = 10;
  // Rayon axial : entre dans la pyramide à t = 5 (apex à z = 0 et plan lointain à z = depth).
  assert.equal(rayFrustumEntry(image, camera, depth, [0, 0, -5], [0, 0, 1]), 5);
  // Rayon parallèle à l'axe mais hors du cadre latéral.
  assert.equal(rayFrustumEntry(image, camera, depth, [100, 0, -5], [0, 0, 1]), null);
  // Rayon qui s'éloigne de la pyramide.
  assert.equal(rayFrustumEntry(image, camera, depth, [0, 0, -5], [0, 0, -1]), null);
  assert.equal(rayHitsFarFace(image, camera, depth, [0, 0, 0], [0, 0, 1]), true);
  assert.equal(rayHitsFarFace(image, camera, depth, [0, 0, 0], [0, 0.8, 1]), false);
});

test("sphere panoramas keep their radius around the camera centre when oriented like the cube", () => {
  const image = { qvec: [1, 0, 0, 0], tvec: [0, 0, 0] };
  const forward = [0.3, -0.5, -0.8];
  const orient = alignCubeToView(forward);
  const { positions, uvs } = panoramaSphere(image, 2, false, orient);
  const center = [0, 0, 0];
  for (let k = 0; k < positions.length; k += 3) {
    const d = Math.hypot(positions[k] - center[0], positions[k + 1] - center[1], positions[k + 2] - center[2]);
    assert.ok(Math.abs(d - 2) < 1e-5);
  }
  assert.ok([...uvs].every((v) => v >= -1e-6 && v <= 1 + 1e-6));
});

test("cube panoramas stay flat on the ground and only yaw with the view", () => {
  const forward = [0.3, -0.5, -0.8];
  const m = alignCubeToView(forward);
  // Verticale du cube = verticale monde : aucune composante de tangage ni de roulis.
  assert.deepEqual([m[1], m[4], m[7]].map((v) => Math.round(v * 1e9) / 1e9), [0, 1, 0]);
  // Image centre (-z) sur la projection horizontale du regard.
  const h = [forward[0], 0, forward[2]];
  const hl = Math.hypot(...h);
  assert.ok([-m[2], -m[5], -m[8]].every((v, k) => Math.abs(v - h[k] / hl) < 1e-9));
  // Rotation propre (déterminant 1).
  const det = m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6]);
  assert.ok(Math.abs(det - 1) < 1e-9);
  // Le regard varie en hauteur seulement : le cube ne bouge pas.
  assert.ok(alignCubeToView([0.3, 0.9, -0.8]).every((v, k) => Math.abs(v - m[k]) < 1e-9));
});

test("every temporary cube orientation hypothesis gives a proper rotation", () => {
  const rwc = [0.2, -0.9, 0.1, 0.7, 0.3, 0.6, -0.6, -0.2, 0.7];
  for (const hypothesis of ["yaw", "world", "camera", "identity"]) {
    const m = alignCubeToView([0.3, -0.5, -0.8], [0, 1, 0], hypothesis, rwc);
    const det = m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6]);
    assert.ok(Math.abs(det - 1) < 1e-9, hypothesis);
  }
});

test("cube orientation stays orthonormal and defined when the view is vertical", () => {
  const m = alignCubeToView([0, -1, 0]);
  const dot = (i, j) => m[i] * m[j] + m[i + 3] * m[j + 3] + m[i + 6] * m[j + 6];
  assert.ok(Math.abs(dot(0, 0) - 1) < 1e-9 && Math.abs(dot(1, 1) - 1) < 1e-9 && Math.abs(dot(2, 2) - 1) < 1e-9);
  assert.ok(Math.abs(dot(0, 1)) < 1e-9 && Math.abs(dot(1, 2)) < 1e-9 && Math.abs(dot(0, 2)) < 1e-9);
});


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
      app: {
        root: new TestEntity(),
        graphicsDevice: {},
        scene: { layers: { getLayerByName: () => null, push() {} } },
      },
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

test("COLMAP equirectangular cameras (model 17) load as panoramas", async () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const input = { addEventListener() {}, removeEventListener() {} };
  globalThis.document = { getElementById: (id) => id === "colmapFolderInput" ? input : null };
  globalThis.window = { devicePixelRatio: 1 };
  const statuses = [];
  try {
    const layer = createColmapLayer({
      app: {
        root: new TestEntity(),
        graphicsDevice: {},
        scene: { layers: { getLayerByName: () => null, push() {} } },
      },
      getSceneRadius: () => 1,
      getFlipUp: () => false,
      setStatus: (...args) => statuses.push(args),
      onBoundsChanged: () => {},
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
    const cameras = binary(["u64", 1], ["u32", 1], ["u32", 17], ["u64", 4096], ["u64", 2048],
      ["f64", 4096], ["f64", 2048]);
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

test("cube map front face samples the equirectangular centre", () => {
  const width = 8;
  const height = 4;
  const rgba = new Uint8ClampedArray(width * height * 4);
  const centre = (2 * width + 4) * 4;
  rgba.set([255, 0, 0, 255], centre);
  const faceSize = 4;
  const atlas = equirectToCubeAtlas(rgba, width, height, faceSize);
  assert.equal(atlas.width, faceSize * 3);
  assert.equal(atlas.height, faceSize * 2);
  const texel = (2 * atlas.width + 2) * 4;
  assert.deepEqual([...atlas.data.slice(texel, texel + 3)], [255, 0, 0]);
  // Avec l'inversion gauche/droite, le centre avant est échantillonné à la colonne miroir.
  const mirrored = equirectToCubeAtlas(rgba, width, height, faceSize, true);
  const mirroredTexel = (2 * mirrored.width + 2) * 4;
  assert.notDeepEqual([...mirrored.data.slice(mirroredTexel, mirroredTexel + 3)], [255, 0, 0]);
});

test("the view ray must cross the enlarged frustum to count as covering the centre", () => {
  const image = { qvec: [1, 0, 0, 0], tvec: [0, 0, 0] };
  const camera = { width: 100, height: 100, modelName: "PINHOLE", params: [100, 100, 50, 50] };
  // Cadre à 5 unités : demi-largeur 2.5.
  assert.equal(coversViewCenter(image, camera, 5, [0, 0, -10], [0, 0, 1]), true);
  assert.equal(coversViewCenter(image, camera, 5, [10, 0, -10], [0, 0, 1]), false);
  assert.equal(coversViewCenter(image, camera, 5, [0, 0, -10], [0, 0, -1]), false);
  assert.equal(coversViewCenter(image, camera, 5, [0, 0, 6], [0, 0, 1]), false);
  // Point de vue derrière le plan de l'image, regardant vers elle : l'image ne doit pas être choisie.
  assert.equal(coversViewCenter(image, camera, 5, [0, 0, 6], [0, 0, -1]), false);
});

test("a click ray hits a panorama marker box only when it crosses it", () => {
  assert.equal(rayBoxHit([0, 0, 10], 2, [0, 0, 0], [0, 0, 1]), true);
  assert.equal(rayBoxHit([0, 0, 10], 2, [3, 0, 0], [0, 0, 1]), false);
  assert.equal(rayBoxHit([0, 0, 10], 2, [0, 0, 0], [0, 0, -1]), false);
});
