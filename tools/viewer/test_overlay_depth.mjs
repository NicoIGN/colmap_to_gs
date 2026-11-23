import { test } from "node:test";
import assert from "node:assert/strict";
import { registerOverlayMaterial, setOverlayOcclusion } from "./html/viewer/overlay-depth.js";

test("overlay materials follow their group's occlusion toggle", () => {
  setOverlayOcclusion("vectors", false);
  const before = registerOverlayMaterial({ depthTest: true }, "vectors");
  assert.equal(before.depthTest, false);

  setOverlayOcclusion("vectors", true);
  assert.equal(before.depthTest, true);

  const after = registerOverlayMaterial({ depthTest: false }, "vectors");
  assert.equal(after.depthTest, true);

  const colmap = registerOverlayMaterial({ depthTest: true }, "colmap");
  setOverlayOcclusion("colmap", false);
  assert.equal(colmap.depthTest, false);
  assert.equal(before.depthTest, true, "toggling colmap must not affect vectors");

  setOverlayOcclusion("vectors", false);
  assert.equal(before.depthTest, false);
  assert.equal(after.depthTest, false);
});
