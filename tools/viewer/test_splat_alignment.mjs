import assert from "node:assert/strict";
import { test } from "node:test";

import { createProjectedFrame, layerPlacement, parseGeoref } from "./html/viewer/georef.js";
import { frameAlignment, splatPlacement } from "./html/viewer/splat-alignment.js";

const SCENE_ORIGIN = [651652.2542795899, 6864123.4116957905, 78.5];

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
        origin_x: SCENE_ORIGIN[0],
        origin_y: SCENE_ORIGIN[1],
        origin_height: SCENE_ORIGIN[2],
      },
    },
  };
}

const frame = parseGeoref(metadataAt(48.8753176, 2.3408312, 34.35));

test("une origine identique donne une translation nulle ; la rotation suit la convergence des méridiens", () => {
  const { rotation, translation } = frameAlignment(frame, parseGeoref(metadataAt(48.8753176, 2.3408312, 34.35)));

  translation.forEach((value) => assert.ok(Math.abs(value) < 1e-3));
  // Convergence des méridiens à Paris : ≈ 0,48°, soit 0,0083 rad.
  assert.ok(Math.abs(rotation[0][1] - 0.0083) < 1e-3);
  assert.ok(Math.abs(rotation[2][2] - 1) < 1e-6);
});

test("une origine 1 km au nord se recale sur le nord du repère commun, sans écart vertical", () => {
  const dLat = 1000 / 111_195;
  const local = parseGeoref(metadataAt(48.8753176 + dLat, 2.3408312, 34.35));
  const { translation } = frameAlignment(frame, local);

  assert.ok(Math.abs(translation[1] - 1000) < 0.1);
  // Convergence des méridiens : à 1 km au nord, le décalage Est est ≈ 8,35 m.
  assert.ok(Math.abs(translation[0] - 8.35) < 0.1);
  assert.ok(Math.abs(translation[2]) < 1e-3);
});

test("un splat ENU et une couche projetée de même origine se superposent, quelle que soit la vue", () => {
  const splat = parseGeoref(metadataAt(48.8753176, 2.3408312, 34.35));
  const lidar = createProjectedFrame(2154, SCENE_ORIGIN);

  for (const view of [splat, lidar]) {
    const fromSplat = layerPlacement(view, splat).origin;
    const fromLidar = layerPlacement(view, lidar).origin;
    assert.ok(Math.hypot(fromSplat[0] - fromLidar[0], fromSplat[1] - fromLidar[1]) < 0.01);
  }
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
