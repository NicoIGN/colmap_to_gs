import assert from "node:assert/strict";
import test from "node:test";
import { findPivotAtPixel, findVertexAtPixel } from "./html/viewer/pivot.js";

const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const CAMERA = { matrix: IDENTITY, fov: 90, horizontalFov: false };
const SIZE = { width: 800, height: 600 };

function near(actual, expected, epsilon = 1e-6) {
  actual.forEach((value, i) => assert.ok(Math.abs(value - expected[i]) < epsilon, `${actual} ≠ ${expected}`));
}

test("pivot au centre de vue : médiane des profondeurs proches du clic", () => {
  const positions = new Float32Array([0, 0, -10, 1, 0, -10, 0, 0, -12]);
  const pivot = findPivotAtPixel({
    sources: [{ label: "LiDAR", positions, matrix: IDENTITY }],
    camera: CAMERA, px: 400, py: 300, ...SIZE,
  });
  assert.equal(pivot.label, "LiDAR");
  near(pivot.point, [0, 0, -10]);
});

test("la matrice de l'entité déplace les points avant le calcul", () => {
  const translated = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 0, 1];
  // Point (5, 0, -10) : projeté à x = 550 px pour un FOV de 90° et un format 4:3.
  const pivot = findPivotAtPixel({
    sources: [{ label: "COLMAP", positions: new Float32Array([0, 0, -10]), matrix: translated }],
    camera: CAMERA, px: 550, py: 300, ...SIZE,
  });
  near(pivot.point, [5, 0, -10]);
});

test("un point hors du rayon de 40 px n'est pas retenu, la source suivante l'est", () => {
  const far = findPivotAtPixel({
    sources: [{ label: "LiDAR", positions: new Float32Array([3, 0, -10]), matrix: IDENTITY }],
    camera: CAMERA, px: 400, py: 300, ...SIZE,
  });
  assert.equal(far, null);

  const fallback = findPivotAtPixel({
    sources: [
      { label: "LiDAR", positions: new Float32Array([3, 0, -10]), matrix: IDENTITY },
      { label: "vecteur", positions: new Float32Array([0, 0, -4]), matrix: IDENTITY },
    ],
    camera: CAMERA, px: 400, py: 300, ...SIZE,
  });
  assert.equal(fallback.label, "vecteur");
  near(fallback.point, [0, 0, -4]);
});

test("les points derrière la caméra sont ignorés", () => {
  const pivot = findPivotAtPixel({
    sources: [{ label: "LiDAR", positions: new Float32Array([0, 0, 5]), matrix: IDENTITY }],
    camera: CAMERA, px: 400, py: 300, ...SIZE,
  });
  assert.equal(pivot, null);
});

test("mesure : le sommet le plus proche du clic est retenu avec son label", () => {
  
  const hit = findVertexAtPixel({
    sources: [
      { label: "LiDAR", positions: new Float32Array([0, 0, -10, 3, 0, -10]), matrix: IDENTITY },
      { label: "Vecteurs", positions: new Float32Array([0.5, 0, -10]), matrix: IDENTITY },
    ],
    camera: CAMERA, px: 400, py: 300, ...SIZE,
  });
  assert.equal(hit.label, "LiDAR");
  near(hit.point, [0, 0, -10]);
  const none = findVertexAtPixel({
    sources: [{ label: "LiDAR", positions: new Float32Array([3, 0, -10]), matrix: IDENTITY }],
    camera: CAMERA, px: 400, py: 300, ...SIZE,
  });
  assert.equal(none, null);
});
