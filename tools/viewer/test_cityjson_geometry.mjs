import assert from "node:assert/strict";
import test from "node:test";
import {
  readCityJson, isCityJsonText, epsgOfCityJson, splitRoofFacade, buildNearSplatGrid, dropNearTriangles,
} from "./html/viewer/cityjson-geometry.js";

const CUBE_FACES = [
  [[0, 3, 2, 1]],
  [[4, 5, 6, 7]],
  [[0, 1, 5, 4]],
  [[1, 2, 6, 5]],
  [[2, 3, 7, 6]],
  [[3, 0, 4, 7]],
];
const CUBE_VERTICES = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]];

function cityJson(objects, extra = {}) {
  return JSON.stringify({
    type: "CityJSON",
    version: "2.0",
    transform: { scale: [1, 1, 1], translate: [1000, 2000, 0] },
    metadata: { referenceSystem: "urn:ogc:def:crs:EPSG::2154" },
    CityObjects: objects,
    vertices: extra.vertices ?? CUBE_VERTICES.map((v) => v.map((c, k) => (k < 2 ? c * 10 : c))),
  });
}

function totalArea(source) {
  let area = 0;
  for (let t = 0; t < source.vertexCount; t += 3) {
    const p = (v) => [0, 1, 2].map((k) => source.rel[(t + v) * 3 + k]);
    const [a, b, c] = [p(0), p(1), p(2)];
    const u = a.map((x, k) => b[k] - x);
    const w = a.map((x, k) => c[k] - x);
    const n = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
    area += Math.hypot(...n) / 2;
  }
  return area;
}

test("un Solid donne 12 triangles, sommets décodés par transform et CRS lu depuis les métadonnées", () => {
  const text = cityJson({
    building: { type: "Building", geometry: [{ type: "Solid", lod: "2", boundaries: [CUBE_FACES] }] },
  });
  const source = readCityJson(text, "ville.json");
  assert.equal(source.triangleCount, 12);
  assert.equal(source.objectCount, 1);
  assert.equal(source.epsg, 2154);
  assert.deepEqual(source.centre.map((v) => Number(v.toFixed(3))), [1005, 2005, 0.5]);
  assert.ok(Math.abs(totalArea(source) - 240) < 1e-6);
});

test("seul le LoD le plus détaillé d'un objet est gardé", () => {
  const flat = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]];
  const text = cityJson(
    {
      building: {
        type: "Building",
        geometry: [
          { type: "MultiSurface", lod: "1", boundaries: [[[0, 1, 2, 3]]] },
          { type: "MultiSurface", lod: "2.2", boundaries: [[[0, 1, 2, 3]], [[0, 1, 2, 3]]] },
        ],
      },
    },
    { vertices: flat }
  );
  const source = readCityJson(text, "ville.json");
  assert.equal(source.triangleCount, 4);
});

test("un LoD0 seul est ignoré et compté comme non pris en charge", () => {
  const text = cityJson({
    footprint: { type: "Building", geometry: [{ type: "Solid", lod: "0", boundaries: [CUBE_FACES] }] },
  });
  const source = readCityJson(text, "ville.json");
  assert.equal(source.triangleCount, 0);
  assert.equal(source.unsupportedCount, 1);
});

test("un polygone concave est triangulé sans perte d'aire", () => {
  const lShape = [[0, 0, 0], [2, 0, 0], [2, 1, 0], [1, 1, 0], [1, 2, 0], [0, 2, 0]];
  const text = JSON.stringify({
    type: "CityJSON",
    version: "2.0",
    CityObjects: { plan: { type: "Building", geometry: [{ type: "MultiSurface", lod: "1", boundaries: [[[0, 1, 2, 3, 4, 5]]] }] } },
    vertices: lShape,
  });
  const source = readCityJson(text, "plan.json");
  assert.equal(source.triangleCount, 4);
  assert.ok(Math.abs(totalArea(source) - 3) < 1e-6);
});

test("les géométries non surfaciques sont comptées et la limite de triangles est signalée", () => {
  const text = cityJson({
    building: { type: "Building", geometry: [{ type: "Solid", lod: "2", boundaries: [CUBE_FACES] }] },
    instance: { type: "BuildingInstallation", geometry: [{ type: "GeometryInstance", lod: "2", template: 0 }] },
  });
  const source = readCityJson(text, "ville.json");
  assert.equal(source.unsupportedCount, 1);

  const limited = readCityJson(text, "ville.json", { maxTriangles: 5 });
  assert.equal(limited.truncated, true);
  assert.ok(limited.triangleCount <= 6);
});

test("les triangles orientés vers le ciel sont des toits, les autres des façades", () => {
  const text = cityJson({
    building: { type: "Building", geometry: [{ type: "Solid", lod: "2", boundaries: [CUBE_FACES] }] },
  });
  const source = readCityJson(text, "ville.json");
  const { roof, facade } = splitRoofFacade(source);
  assert.equal(roof.triangleCount, 2);
  assert.equal(facade.triangleCount, 10);
  for (let v = 0; v < roof.vertexCount; v++) assert.equal(roof.normals[v * 3 + 2], 1);
  for (let v = 0; v < facade.vertexCount; v++) assert.ok(facade.normals[v * 3 + 2] < 0.5);
  assert.equal(roof.rel.length + facade.rel.length, source.rel.length);
});

test("détection CityJSON et refus d'un JSON qui n'en est pas un", () => {
  assert.equal(isCityJsonText(cityJson({})), true);
  assert.equal(isCityJsonText('{"type":"FeatureCollection"}'), false);
  assert.throws(() => readCityJson('{"type":"FeatureCollection"}', "x.json"), /pas un CityJSON/);
  assert.equal(epsgOfCityJson({ metadata: { referenceSystem: "https://www.opengis.net/def/crs/EPSG/0/7415" } }), 7415);
  assert.equal(epsgOfCityJson({}), null);
});

test("facade triangles are dropped only when a splat lies within about 1 m of their plan edges", () => {
  // Deux murs verticaux au même endroit en plan : un côté x = 0 (y de 0 à 10), un côté x = 20.
  const positions = new Float32Array([
    0, 0, 0, 0, 10, 0, 0, 0, 5,
    20, 0, 0, 20, 10, 0, 20, 0, 5,
  ]);
  const normals = new Float32Array(18);
  // Un splat à 0,5 m du premier mur, rien près du second.
  const grid = buildNearSplatGrid([new Float32Array([0.5, 5, 2])], 0, 1);
  const kept = dropNearTriangles(positions, normals, grid);
  assert.equal(kept.triangleCount, 1);
  assert.equal(kept.positions[0], 20);

  const farGrid = buildNearSplatGrid([new Float32Array([5, 5, 2])], 0, 1);
  assert.equal(dropNearTriangles(positions, normals, farGrid).triangleCount, 2);
});
