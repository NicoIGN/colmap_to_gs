import argparse
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from plyfile import PlyData, PlyElement

from tools.postprocess import filter_splat_by_bbox as tool
from tools.postprocess.filter_splats import SPLAT_DTYPE


ORIGIN = [48.8753176, 2.3408312, 34.35]
ORIGIN_XY = (651652.2542795899, 6864123.4116957905)
REFERENCE = {
    "type": "local_ENU",
    "units": "m",
    "axes": ["East", "North", "Up"],
    "origin_lat_lon_height": ORIGIN,
    "origin_projected": {"epsg": 2154},
}


def splat_records(points):
    records = np.zeros(len(points), dtype=SPLAT_DTYPE)
    records["position"] = [p[0] for p in points]
    records["scale"] = [p[1] for p in points]
    records["rgba"] = 255
    records["rotation"] = [255, 128, 128, 128]
    return records


def args_for(**overrides):
    values = dict(input=None, output=None, format="splat", georef=None, epsg=None,
                  bbox=None, zmin=None, zmax=None, sigma=3.0, batch_size=2, overwrite=False)
    values.update(overrides)
    return argparse.Namespace(**values)


class FilterSplatByBboxTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.georef = self.dir / "metadata.json"
        self.georef.write_text(json.dumps({"output_reference": REFERENCE}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_splat(self, points, **overrides):
        source = self.dir / "in.splat"
        splat_records(points).tofile(source)
        output = self.dir / "out.splat"
        report = tool.filter_file(args_for(input=source, output=output, georef=self.georef, **overrides))
        kept = np.fromfile(output, dtype=SPLAT_DTYPE)
        return report, kept

    def test_enu_origin_maps_to_projected_origin(self):
        convert = tool.enu_to_projected_function(REFERENCE, 2154)
        x, y, z = convert(np.zeros((1, 3)))[0]
        self.assertAlmostEqual(x, ORIGIN_XY[0], places=3)
        self.assertAlmostEqual(y, ORIGIN_XY[1], places=3)
        self.assertAlmostEqual(z, ORIGIN[2], places=3)

    def test_bbox_keeps_inside_and_removes_straddling_support(self):
        x0, y0 = ORIGIN_XY
        bbox = f"{x0 - 2},{y0 - 2},{x0 + 3.4},{y0 + 2}"
        points = [
            ([0.0, 0.0, 0.0], [0.01, 0.01, 0.01]),
            ([2.0, 0.0, 0.0], [0.5, 0.5, 0.5]),
            ([2.0, 0.0, 0.0], [0.1, 0.1, 0.1]),
        ]
        report, kept = self.run_splat(points, bbox=bbox)
        self.assertEqual(report["removed"], 1)
        self.assertEqual(len(kept), 2)

    def test_z_range_alone_removes_splats_above_zmax(self):
        report, kept = self.run_splat(
            [([0.0, 0.0, 0.0], [0.01] * 3), ([0.0, 0.0, 10.0], [0.01] * 3)],
            zmax=ORIGIN[2] + 5,
        )
        self.assertEqual(report["kept"], 1)
        self.assertAlmostEqual(kept["position"][0][2], 0.0)

    def test_ply_roundtrip_keeps_selected_vertices(self):
        vertex = np.zeros(2, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4"),
                                    ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
                                    ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
                                    ("opacity", "f4")])
        vertex["x"] = [0.0, 50.0]
        vertex["scale_0"] = vertex["scale_1"] = vertex["scale_2"] = np.log(0.01)
        vertex["rot_0"] = 1.0
        source = self.dir / "in.ply"
        PlyData([PlyElement.describe(vertex, "vertex")]).write(str(source))
        output = self.dir / "out.ply"
        x0 = ORIGIN_XY[0]
        report = tool.filter_file(args_for(
            input=source, output=output, format="ply", georef=self.georef,
            bbox=f"{x0 - 1},{ORIGIN_XY[1] - 1},{x0 + 1},{ORIGIN_XY[1] + 1}",
        ))
        kept = PlyData.read(str(output))["vertex"].data
        self.assertEqual(report["kept"], 1)
        self.assertEqual(len(kept), 1)
        self.assertAlmostEqual(float(kept["x"][0]), 0.0)

    def test_writes_metadata_json_with_output_reference(self):
        x0, y0 = ORIGIN_XY
        bbox = f"{x0 - 2},{y0 - 2},{x0 + 2},{y0 + 2}"
        self.run_splat([([0.0, 0.0, 0.0], [0.01] * 3), ([0.0, 0.0, 10.0], [0.01] * 3)],
                      bbox=bbox, zmax=ORIGIN[2] + 5)
        metadata = json.loads((self.dir / "out.metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["output_reference"], REFERENCE)
        self.assertEqual(metadata["splat"], "out.splat")
        self.assertEqual(metadata["crs"], "EPSG:2154")
        self.assertEqual(metadata["filter"]["kept"], 1)
        self.assertEqual(metadata["filter"]["removed"], 1)
        self.assertEqual(metadata["filter"]["source"], "in.splat")

    def test_requires_bbox_or_z_range(self):
        with self.assertRaisesRegex(ValueError, "--bbox, --zmin ou --zmax"):
            self.run_splat([([0.0, 0.0, 0.0], [0.01] * 3)])

    def test_rejects_inverted_bbox(self):
        with self.assertRaisesRegex(ValueError, "xmin < xmax"):
            self.run_splat([([0.0, 0.0, 0.0], [0.01] * 3)], bbox="10,0,0,10")


if __name__ == "__main__":
    unittest.main()
