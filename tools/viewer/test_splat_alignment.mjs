import assert from "node:assert/strict";
import { test } from "node:test";

import { parseGeoref } from "./html/viewer/georef.js";
import { frameAlignment, splatPlacement } from "./html/viewer/splat-alignment.js";

function metadataAt(lat, lon, h) {
  return {
    type: "FeatureCollection",
    crs: "EPSG:2154",
    georeferenced: true,
    output_reference: {
      type: "local_ENU",
      units: "meters",
      axes: ["East", "North", "Up"],
      origin_lat_lon_height: [lat, lon, h],
      origin_geographic_crs: "EPSG:4979 (WGS84 supposé pour le GPX)",
      origin_projected: {
        epsg: 2154,
        origin_x: 651652.2542795899,
        origin_y: 6864123.4116957905,
        origin_height: 78.5,
      },
    },
  };
}

const frame = parseGeoref(metadataAt(48.8753176, 2.3408312, 34.35));

test("une origine identique donne une pose identité", () => {
  const local = parseGeoref(metadataAt(48.8753176, 2.3408312, 34.35));
  const { rotation, translation } = frameAlignment(frame, local);

  rotation.forEach((row, r) => row.forEach((value, c) => {
    assert.ok(Math.abs(value - (r === c ? 1 : 0)) < 1e-6);
  }));
  translation.forEach((value) => assert.ok(Math.abs(value) < 1e-3));
});

test("une origine 1 km au nord se recale sur le nord du repère commun", () => {
  const dLat = 1000 / 111_195;
  const local = parseGeoref(metadataAt(48.8753176 + dLat, 2.3408312, 34.35));
  const { rotation, translation } = frameAlignment(frame, local);

  assert.ok(Math.abs(translation[0]) < 1);
  assert.ok(Math.abs(translation[1] - 1000) < 1);
  // Courbure terrestre : à 1 km, le point est à environ s²/2R ≈ 8 cm sous le plan tangent.
  assert.ok(Math.abs(translation[2] + 1000 ** 2 / (2 * 6_371_000)) < 0.01);
  assert.ok(Math.abs(rotation[0][0] - 1) < 1e-5);
});

test("la pose avec flipUp inverse Y et Z, et la rotation reste un quaternion unitaire", () => {
  const { position, quaternion } = splatPlacement(
    { rotation: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], translation: [5, 6, 7] },
    true
  );

  assert.deepEqual(position.map((v) => Math.round(v * 1e6) / 1e6), [5, -6, -7]);
  assert.ok(Math.abs(quaternion[0] - 1) < 1e-9);
  assert.ok(Math.abs(Math.hypot(...quaternion) - 1) < 1e-9);
});
