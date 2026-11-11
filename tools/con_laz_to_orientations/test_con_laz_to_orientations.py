import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
from plyfile import PlyData
from scipy.spatial.transform import Rotation

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from con_laz_to_orientations import convert, parse_con  # noqa: E402

W, H, F = 400, 300, 350.0
NADIR = np.diag([1.0, -1.0, -1.0])


def write_con(path, center, world_to_camera, image2ground=False, extra="", size=(W, H)):
    matrix = world_to_camera.T if image2ground else world_to_camera
    rows = "".join(
        f"<l{i + 1}><pt3d><x>{matrix[i, 0]:.15f}</x><y>{matrix[i, 1]:.15f}</y>"
        f"<z>{matrix[i, 2]:.15f}</z></pt3d></l{i + 1}>" for i in range(3))
    path.write_text(f"""<?xml version="1.0"?><orientation><geometry><extrinseque>
<systeme><euclidien><x>650000</x><y>6860000</y></euclidien></systeme>
<sommet><easting>{center[0] - 650000:.6f}</easting><northing>{center[1] - 6860000:.6f}</northing>
<altitude>{center[2]:.6f}</altitude></sommet>
<rotation><Image2Ground>{str(image2ground).lower()}</Image2Ground><mat3d>{rows}</mat3d></rotation>
</extrinseque><intrinseque><sensor><image_size><width>{size[0]}</width><height>{size[1]}</height></image_size>
<ppa><c>200.5</c><l>149.5</l><focale>{F}</focale></ppa>{extra}</sensor></intrinseque></geometry></orientation>""")


class ConLazTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.images = self.root / "images"
        (self.images / "sub").mkdir(parents=True)
        self.poses = {}
        for index in range(3):
            rotation = Rotation.from_euler("xyz", [0.02 * index, -0.03, 0.05 * index]).as_matrix() @ NADIR
            center = np.array([650100.0 + 10 * index, 6860100.0, 200.0])
            folder = self.images / ("sub" if index == 2 else "")
            write_con(folder / f"img{index}.CON", center, rotation, image2ground=index % 2 == 0)
            Image.new("RGB", (W, H)).save(folder / f"img{index}.jpg")
            self.poses[f"img{index}"] = (center, rotation)
        Image.new("RGB", (W, H)).save(self.images / "orphan.jpg")
        self.laz = self.root / "points.laz"
        self.write_laz()

    def write_laz(self):
        import laspy
        rng = np.random.default_rng(0)
        count = 5000
        header = laspy.LasHeader(point_format=3, version="1.2")
        header.scales, header.offsets = [0.001] * 3, [650000, 6860000, 0]
        las = laspy.LasData(header)
        las.x = 650100 + rng.uniform(-60, 80, count)
        las.y = 6860100 + rng.uniform(-60, 60, count)
        las.z = rng.uniform(0, 5, count)
        for channel in ("red", "green", "blue"):
            setattr(las, channel, rng.integers(0, 65535, count))
        las.write(self.laz)
        self.source = np.column_stack((las.x, las.y, las.z))

    def test_conversion_matches_con_geometry_and_feeds_colmap(self):
        output = self.root / "out"
        orientations, points, count, cameras, origin = convert(
            self.images, self.laz, output, stride=2, crs="EPSG:2154")
        self.assertEqual((count, cameras), (3, 1))
        data = json.loads(orientations.read_text())
        self.assertEqual(data["reference"]["crs"], "EPSG:2154")
        self.assertEqual(data["cameras"][0]["params"], [F, F, 200.5, 149.5])
        for image in data["images"]:
            center, rotation = self.poses[Path(image["name"]).stem]
            quat = np.asarray(image["qvec"])[[1, 2, 3, 0]]
            np.testing.assert_allclose(Rotation.from_quat(quat).as_matrix(), rotation, atol=1e-9)
            np.testing.assert_allclose(image["tvec"], -rotation @ (center - origin), atol=1e-6)
        self.assertIn("sub/img2.jpg", [image["name"] for image in data["images"]])
        vertex = PlyData.read(points)["vertex"]
        xyz = np.column_stack([vertex[key] for key in "xyz"])
        np.testing.assert_allclose(xyz, self.source[::2] - origin, atol=1e-6)
        subprocess.run([sys.executable, str(HERE.parent / "images_to_colmap/orientations_to_colmap.py"),
                        "--orientations", str(orientations), "--points", str(points),
                        "--images", str(self.images), "--output", str(output / "sparse")],
                       check=True, capture_output=True)
        self.assertTrue((output / "sparse/points3D.bin").stat().st_size > 8)

    def test_bbox_and_explicit_origin(self):
        output = self.root / "out"
        _, points, *_ = convert(self.images, self.laz, output, origin="650000,6860000,0",
                                bbox=[650080, 6860080, 650120, 6860120])
        vertex = PlyData.read(points)["vertex"]
        x, y = np.asarray(vertex["x"]), np.asarray(vertex["y"])
        self.assertTrue(len(x) and (x >= 80).all() and (x <= 120).all() and (y >= 80).all())

    def test_unsupported_calibration_is_rejected(self):
        center, rotation = self.poses["img0"]
        cases = {
            "distorsion": '<distortion><pps><c>200</c><l>150</l></pps><r3>1e-8</r3></distortion>',
            "systématisme": '<transfo2d><tr2delem Type="systematismeCylindriqueTopAero" '
                            'C0="200" L0="150" S1="0.01" S2="0"/></transfo2d>',
        }
        for message, extra in cases.items():
            with self.subTest(message):
                write_con(self.images / "img0.CON", center, rotation, extra=extra)
                with self.assertRaisesRegex(ValueError, message):
                    convert(self.images, self.laz, self.root / message)
        write_con(self.images / "img0.CON", center, rotation, size=(800, 600))
        with self.assertRaisesRegex(ValueError, "taille"):
            convert(self.images, self.laz, self.root / "size")

    def test_image2ground_flag_transposes_matrix(self):
        center, rotation = self.poses["img1"]
        write_con(self.root / "a.CON", center, rotation, image2ground=True)
        write_con(self.root / "b.CON", center, rotation, image2ground=False)
        np.testing.assert_allclose(parse_con(self.root / "a.CON")["rotation"],
                                   parse_con(self.root / "b.CON")["rotation"], atol=1e-12)


if __name__ == "__main__":
    unittest.main()
