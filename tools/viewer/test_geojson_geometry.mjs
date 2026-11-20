import assert from "node:assert/strict";
import test from "node:test";
import { readGeojsonLayers } from "./html/viewer/geojson-geometry.js";

test("une FeatureCollection donne une couche WGS84 avec lignes et contours", () => {
  const text = JSON.stringify({
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[0, 0], [1, 2, 3]] } },
      { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] } },
      { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [5, 5] } },
    ],
  });
  const [layer] = readGeojsonLayers(text, "bati.geojson");
  assert.equal(layer.name, "bati.geojson");
  assert.equal(layer.epsg, 4326);
  assert.deepEqual(layer.polylines, [
    [0, 0, 0, 1, 2, 3],
    [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0],
  ]);
});

test("le membre crs fixe le code EPSG", () => {
  const text = JSON.stringify({
    type: "Feature",
    crs: { type: "name", properties: { name: "urn:ogc:def:crs:EPSG::2154" } },
    geometry: { type: "MultiLineString", coordinates: [[[10, 20], [30, 40]], [[1, 1], [2, 2]]] },
  });
  const [layer] = readGeojsonLayers(text, "x.geojson");
  assert.equal(layer.epsg, 2154);
  assert.equal(layer.polylines.length, 2);
});

test("un JSON invalide lève une erreur", () => {
  assert.throws(() => readGeojsonLayers("{not json", "x.geojson"));
});
