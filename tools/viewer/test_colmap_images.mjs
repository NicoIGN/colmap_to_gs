import assert from "node:assert/strict";
import test from "node:test";
import {
  findImageFile,
  indexImageFiles,
  selectNearestImages,
} from "./html/viewer/colmap-images.js";

const file = (name, webkitRelativePath = "") => ({ name, webkitRelativePath });

test("indexImageFiles keys images by path relative to the dropped folder, with basename fallback", () => {
  const index = indexImageFiles([
    file("a.jpg", "images/sub/a.jpg"),
    file("cameras.bin", "sparse/0/cameras.bin"),
    file("b.PNG", "images/b.PNG"),
  ]);
  assert.equal(index.get("sub/a.jpg").name, "a.jpg");
  assert.equal(index.get("a.jpg").name, "a.jpg");
  assert.equal(index.get("b.PNG").name, "b.PNG");
  assert.equal(index.has("cameras.bin"), false);
});

test("findImageFile matches COLMAP names with or without sub-folders", () => {
  const index = indexImageFiles([file("x.jpg", "images/cam1/x.jpg")]);
  assert.equal(findImageFile(index, "cam1/x.jpg")?.name, "x.jpg");
  assert.equal(findImageFile(index, "x.jpg")?.name, "x.jpg");
  assert.equal(findImageFile(index, "missing.jpg"), null);
});

test("selectNearestImages keeps cameras looking at the viewer, nearest first, up to count", () => {
  // Trois caméras regardant +Y ; la quatrième regarde à l'opposé du point de vue.
  const centers = [0, 0, 0, 0, 2, 0, 0, 10, 0, 0, -1, 0];
  const axes = [0, 1, 0, 0, 1, 0, 0, 1, 0, 0, -1, 0];
  const viewer = [0, 5, 0];
  const picked = selectNearestImages({ centers, axes, viewer, count: 5 });
  assert.deepEqual(picked, [1, 0]);
});

test("selectNearestImages ignores cameras beyond maxDistance", () => {
  const centers = [0, 0, 0, 0, 0, 100];
  const axes = [0, 1, 0, 0, 1, 0];
  assert.deepEqual(selectNearestImages({ centers, axes, viewer: [0, 5, 0], maxDistance: 10 }), [0]);
});

test("selectNearestImages rejects cameras whose axis is outside the cone", () => {
  const centers = [0, 0, 0];
  const axes = [1, 0, 0];
  assert.deepEqual(selectNearestImages({ centers, axes, viewer: [0, 5, 0] }), []);
  assert.deepEqual(selectNearestImages({ centers, axes, viewer: [5, 0, 0] }), [0]);
});

test("hysteresis keeps the current image while it stays valid, then switches", () => {
  // Deux caméras sur l'axe de vue (+X) ; la 0 est la mieux alignée.
  const centers = [5, 0, 0, 5, 0.3, 0];
  const axes = [-1, 0, 0, -1, 0, 0];
  const args = { centers, axes, viewer: [0, 0, 0], direction: [1, 0, 0], maxAlong: 10, count: 1, maxAngleDeg: 35 };
  assert.deepEqual(selectNearestImages({ ...args, keep: -1 }), [0]);
  // Caméra 1 déjà affichée : elle reste choisie malgré la meilleure caméra 0.
  assert.deepEqual(selectNearestImages({ ...args, keep: 1 }), [1]);
  // Caméra 0 sortie du cône : on bascule sur la meilleure valide.
  const moved = [5, 10, 0, 5, 0.3, 0];
  assert.deepEqual(selectNearestImages({ ...args, centers: moved, keep: 0 }), [1]);
});

test("cameras beyond the scene centre are never selected, even when kept", () => {
  // Centre de scène à 10 unités ; caméra alignée à 12 unités (au-delà du centre).
  const centers = [12, 0, 0, 4, 0, 0];
  const axes = [-1, 0, 0, -1, 0, 0];
  const args = { centers, axes, viewer: [0, 0, 0], direction: [1, 0, 0], maxAlong: 10, count: 1, maxAngleDeg: 35 };
  assert.deepEqual(selectNearestImages({ ...args, keep: 0 }), [1]);
  assert.deepEqual(selectNearestImages({ ...args, keep: -1 }), [1]);
});

test("the camera's own image stays selected when the viewer sits at its centre", () => {
  const centers = [0, 0, 0, 5, 0, 0];
  const axes = [1, 0, 0, -1, 0, 0];
  const picked = selectNearestImages({
    centers, axes, viewer: [0, 0, 0], direction: [1, 0, 0], maxAlong: 10, count: 1, maxAngleDeg: 35,
  });
  assert.deepEqual(picked, [0]);
});
