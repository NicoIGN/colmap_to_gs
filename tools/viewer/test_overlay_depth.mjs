import { test } from "node:test";
import assert from "node:assert/strict";
import { registerOverlayMaterial, setOverlayOcclusion } from "./html/viewer/overlay-depth.js";

test("overlay materials follow the splat occlusion toggle", () => {
  setOverlayOcclusion(false);
  const before = registerOverlayMaterial({ depthTest: true });
  assert.equal(before.depthTest, false);

  setOverlayOcclusion(true);
  assert.equal(before.depthTest, true);

  const after = registerOverlayMaterial({ depthTest: false });
  assert.equal(after.depthTest, true);

  setOverlayOcclusion(false);
  assert.equal(before.depthTest, false);
  assert.equal(after.depthTest, false);
});
