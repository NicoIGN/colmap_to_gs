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
