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

test("selectNearestImages keeps cameras looking at the viewer when no direction is given, nearest first", () => {
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
  // Deux caméras sur l'axe de vue (+X), regardant dans le sens de la vue ; la 0 est la mieux alignée.
  const centers = [5, 0, 0, 5, 0.3, 0];
  const axes = [1, 0, 0, 1, 0, 0];
  const args = { centers, axes, viewer: [0, 0, 0], direction: [1, 0, 0], maxAlong: 10, count: 1, maxAngleDeg: 35 };
  assert.deepEqual(selectNearestImages({ ...args, keep: -1 }), [0]);
  // Caméra 1 (3,4°) déjà affichée : elle reste choisie tant qu'elle est dans l'angle de centrage.
  assert.deepEqual(selectNearestImages({ ...args, keep: 1, keepAngleDeg: 5 }), [1]);
  // Caméra 1 hors de l'angle de centrage : la caméra 0, plus centrée, prend sa place.
  assert.deepEqual(selectNearestImages({ ...args, keep: 1, keepAngleDeg: 2 }), [0]);
  // Caméra 0 sortie du cône : on bascule sur la meilleure valide.
  const moved = [5, 10, 0, 5, 0.3, 0];
  assert.deepEqual(selectNearestImages({ ...args, centers: moved, keep: 0 }), [1]);
});

test("candidates whose axis differs from the displayed camera are rejected", () => {
  // Référence : caméra affichée regardant en diagonale (+X+Y). Cam 0 la plus centrée mais regardant
  // en diagonale opposée (+X-Y, à 90°) ; cam 1 idem ; cam 2 un peu moins centrée, même axe que la référence.
  const s = Math.SQRT1_2;
  const centers = [5, 0, 0, 5, 1, 0, 5, 1.2, 0];
  const axes = [s, -s, 0, s, -s, 0, s, s, 0];
  const args = { centers, axes, viewer: [0, 0, 0], direction: [1, 0, 0], maxAlong: 10, count: 1, maxAngleDeg: 35, refAxis: [s, s, 0] };
  assert.deepEqual(selectNearestImages({ ...args, maxRefAngleDeg: 30 }), [2]);
  // Sans limite d'angle, le classement pénalise quand même l'écart d'axe : la caméra 2 reste préférée.
  assert.deepEqual(selectNearestImages({ ...args, maxRefAngleDeg: 180 }), [2]);
});

test("the displayed image is replaced when a more centred camera passes the view centre", () => {
  // Caméra 1 affichée à ~15° du centre de vue ; la caméra 0 passe au centre.
  const centers = [5, 0, 0, 5, 1.34, 0];
  const axes = [1, 0, 0, 1, 0, 0];
  const args = { centers, axes, viewer: [0, 0, 0], direction: [1, 0, 0], maxAlong: 10, count: 1, maxAngleDeg: 35, keepAngleDeg: 10 };
  assert.deepEqual(selectNearestImages({ ...args, keep: 1 }), [0]);
});

test("cameras beyond the scene centre are never selected, even when kept", () => {
  // Centre de scène à 10 unités ; caméra alignée à 12 unités (au-delà du centre).
  const centers = [12, 0, 0, 4, 0, 0];
  const axes = [1, 0, 0, 1, 0, 0];
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

test("cameras facing away from the view direction are rejected (no 180° mirror)", () => {
  // Caméra située devant le point de vue mais regardant vers le point de vue : elle montre l'arrière.
  const centers = [5, 0, 0];
  const axes = [-1, 0, 0];
  const args = { centers, axes, viewer: [0, 0, 0], direction: [1, 0, 0], maxAlong: 10, count: 1, maxAngleDeg: 35 };
  assert.deepEqual(selectNearestImages(args), []);
});
