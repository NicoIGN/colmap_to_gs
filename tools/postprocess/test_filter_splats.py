import contextlib
import io
import json
import struct
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial.transform import Rotation

from tools.postprocess import export_ply as export
from tools.postprocess.filter_splats import (
    SPLAT_DTYPE, contextual_mask, load_sparse_points, sparse_bbox_mask,
)


ROOT = Path(__file__).resolve().parent


def scene():
    x, y = np.meshgrid(np.linspace(-10, 10, 41), np.linspace(-5, 5, 21))
    road = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
    means = np.vstack((road, [9, 0, 0], [12, 0, 3], [0, 0, 1]))
    scales = np.tile([0.04, 0.04, 0.005], (len(means), 1))
    scales[-3:] = [2, 1, 0.005]
    return {
        "means": means, "scales": np.log(scales),
        "quats": np.tile([1.0, 0, 0, 0], (len(means), 1)),
        "opacity": np.zeros(len(means)), "dc": np.zeros((len(means), 3)),
    }


def write_points3d(path, points):
    with open(path, "wb") as stream:
        stream.write(struct.pack("<Q", len(points)))
        for index, xyz in enumerate(points):
            stream.write(struct.pack("<QdddBBBdQ", index, *xyz, 1, 2, 3, 0.5, 2))
            stream.write(struct.pack("<iiii", 0, 0, 1, 0))


def sparse_cloud():
    rng = np.random.default_rng(1)
    return rng.uniform([-10, -5, -0.5], [10, 5, 3], size=(500, 3))


class SparseBBoxTest(unittest.TestCase):
    def test_outside_centres_rejected_and_similarity_invariant(self):
        points = sparse_cloud()
        means = np.array([[0, 0, 0], [9.9, 4.9, 0], [25, 0, 0], [0, 0, 30], [10.5, 0, 0]])
        expected, report = sparse_bbox_mask(means, points)
        self.assertEqual(expected.tolist(), [False, False, True, True, False])
        self.assertEqual(report["outside_sparse_bbox"], 2)
        rotation = Rotation.from_euler("xyz", [0.3, 1.2, -0.7])
        for scale in (1e-5, 1e5):
            with self.subTest(scale=scale):
                transform = lambda x: rotation.apply(x) * scale + np.array([4, -1, 7]) * scale
                mask, _ = sparse_bbox_mask(transform(means), transform(points))
                np.testing.assert_array_equal(mask, expected)

    def test_margin_and_invalid_inputs(self):
        points = sparse_cloud()
        mask, _ = sparse_bbox_mask(np.array([[12, 0, 0]]), points, margin=0.2)
        self.assertFalse(mask[0])
        with self.assertRaises(ValueError):
            sparse_bbox_mask(np.zeros((1, 3)), points[:5])
        with self.assertRaises(ValueError):
            sparse_bbox_mask(np.zeros((1, 3)), points, margin=-1)
        with self.assertRaises(ValueError):
            sparse_bbox_mask(np.zeros((1, 3)), np.zeros((20, 3)))

    def test_points3d_reader_skips_tracks(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "points3D.bin"
            write_points3d(path, sparse_cloud()[:7])
            np.testing.assert_allclose(load_sparse_points(path), sparse_cloud()[:7])
            path.write_bytes(path.read_bytes()[:-3])
            with self.assertRaisesRegex(ValueError, "tronqué"):
                load_sparse_points(path)


class ContextualFilterTest(unittest.TestCase):
    def test_large_road_supported_and_interior_large_kept_parasite_rejected(self):
        s = scene()
        mask, report = contextual_mask(s["means"], s["scales"], s["quats"])
        self.assertEqual(np.flatnonzero(mask).tolist(), [len(mask) - 2])
        self.assertEqual(report["large"], 3)
        self.assertEqual(report["peripheral_large"], 2)
        self.assertEqual(report["supported_large"], 1)

    def test_similarity_invariance_and_batch_independence(self):
        s = scene()
        rotation = Rotation.from_euler("xyz", [0.4, -0.8, 1.1])
        expected, _ = contextual_mask(s["means"], s["scales"], s["quats"])
        for scale in (1e-6, 1, 1e6):
            with self.subTest(scale=scale):
                means = rotation.apply(s["means"]) * scale + np.array([3, -2, 5]) * scale
                q = (rotation * Rotation.from_quat(s["quats"][:, [1, 2, 3, 0]])).as_quat()
                mask, _ = contextual_mask(
                    means, s["scales"] + np.log(scale), q[:, [3, 0, 1, 2]],
                    batch_size=13,
                )
                np.testing.assert_array_equal(mask, expected)

    def test_subsampled_reference_is_deterministic(self):
        s = scene()
        first, _ = contextual_mask(s["means"], s["scales"], s["quats"],
                                  reference_limit=200, batch_size=17)
        second, _ = contextual_mask(s["means"], s["scales"], s["quats"],
                                   reference_limit=200, batch_size=100)
        np.testing.assert_array_equal(first, second)
        self.assertFalse(first[-3])
        self.assertTrue(first[-2])

    def test_size_alone_does_not_remove_boundary_surface(self):
        s = scene()
        s["means"][-2] = [9.5, 0, 0]
        mask, _ = contextual_mask(s["means"], s["scales"], s["quats"])
        self.assertFalse(mask.any())
        # A large flat splat normal to the road is not supported by it.
        s["quats"][-2] = Rotation.from_euler("x", 90, degrees=True).as_quat()[[3, 0, 1, 2]]
        mask, _ = contextual_mask(s["means"], s["scales"], s["quats"])
        self.assertTrue(mask[-2])

    def test_local_size_reference_not_global(self):
        s = scene()
        s["scales"][s["means"][:, 0] > 4] = np.log([0.5, 0.5, 0.005])
        s["scales"][-2] = np.log([2, 1, 0.005])
        mask, _ = contextual_mask(s["means"], s["scales"], s["quats"])
        self.assertFalse(mask[-2])  # Four times local size, not "large".

    def test_degenerate_scenes_and_invalid_inputs(self):
        for count in (1, 30):
            s = {k: v[:count].copy() for k, v in scene().items()}
            s["means"][:] = 0
            mask, report = contextual_mask(s["means"], s["scales"], s["quats"])
            self.assertFalse(mask.any())
            self.assertIn("warning", report)
        s = scene()
        s["means"][:, 1:] = 0
        mask, report = contextual_mask(s["means"], s["scales"], s["quats"])
        self.assertFalse(mask.any())
        self.assertIn("warning", report)
        s = scene()
        for factor in (0, 1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                contextual_mask(s["means"], s["scales"], s["quats"], size_factor=factor)
        s["quats"][0] = 0
        with self.assertRaisesRegex(ValueError, "Quaternion nul"):
            contextual_mask(s["means"], s["scales"], s["quats"])

    def test_exporter_default_diagnoses_without_bbox_or_size_removal(self):
        s = scene()
        with contextlib.redirect_stdout(io.StringIO()):
            result, rejected, report = export.clean_gaussians(
                s, np.array([[0, 0, 0], [0.1, 0.1, 0.1]]), 0.01, 100,
                world_scale=1, return_rejected=True,
            )
        self.assertEqual(len(result["means"]), len(s["means"]))
        self.assertEqual(len(rejected["means"]), 1)
        self.assertEqual(report["mode"], "diagnostic")
        # Invalid attributes are still removed, not silently retained.
        s["means"][0] = np.nan
        with contextlib.redirect_stdout(io.StringIO()):
            result = export.clean_gaussians(s, None, None, 100, world_scale=1, filter_mode="off")
        self.assertEqual(len(result["means"]), len(s["means"]) - 1)

    def test_binary_cli_diagnostic_apply_and_guard_preserve_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "raw.splat", root / "result.splat"
            s = scene()
            export.write_world_splat(s, source, np.eye(4), np.eye(3), 1, 100)
            original = source.read_bytes()
            command = [sys.executable, str(ROOT / "filter_splats.py"),
                       "--input", str(source), "--output", str(output)]
            diagnostic = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(diagnostic.returncode, 0, diagnostic.stderr)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(output.with_suffix(".rejected.splat").stat().st_size, 32)
            report = json.loads(output.with_suffix(".filter.json").read_text())
            self.assertEqual(report["applied_rejections"], 0)
            applied = subprocess.run(command + ["--mode", "apply", "--overwrite"],
                                     capture_output=True, text=True, timeout=30)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(output.stat().st_size, len(original) - 32)
            records = np.fromfile(output, dtype=SPLAT_DTYPE)
            self.assertFalse(np.any(np.all(records["position"] == [12, 0, 3], axis=1)))
            before = {p: p.read_bytes() for p in (output, output.with_suffix(".rejected.splat"),
                                                  output.with_suffix(".filter.json"))}
            guarded = subprocess.run(command + ["--mode", "apply", "--max-removal-fraction", "0",
                                               "--overwrite"],
                                     capture_output=True, text=True, timeout=30)
            self.assertNotEqual(guarded.returncode, 0)
            for path, data in before.items():
                self.assertEqual(path.read_bytes(), data)
            self.assertEqual(source.read_bytes(), original)
            same_path = subprocess.run([*command[:command.index("--output")], "--output", str(source),
                                        "--overwrite"], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(same_path.returncode, 0)
            self.assertEqual(source.read_bytes(), original)

    def test_binary_cli_sparse_bbox_applies_independently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output, points = root / "raw.splat", root / "result.splat", root / "p.bin"
            s = scene()
            s["means"] = np.vstack((s["means"], [[200, 0, 0]]))
            for key in ("scales", "quats", "opacity", "dc"):
                s[key] = np.concatenate((s[key], s[key][:1]))
            export.write_world_splat(s, source, np.eye(4), np.eye(3), 1, 100)
            cloud = np.random.default_rng(2).uniform([-13, -6, -1], [13, 6, 4], (500, 3))
            write_points3d(points, cloud)
            command = [sys.executable, str(ROOT / "filter_splats.py"), "--input", str(source),
                       "--output", str(output), "--sparse-points", str(points)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            records = np.fromfile(output, dtype=SPLAT_DTYPE)
            self.assertEqual(len(records), len(s["means"]) - 1)
            self.assertFalse(np.any(records["position"][:, 0] == 200))
            report = json.loads(output.with_suffix(".filter.json").read_text())
            self.assertEqual(report["sparse_bbox"]["outside_sparse_bbox"], 1)
            self.assertEqual(report["applied_rejections"], 1)
            self.assertEqual(report["contextual_input"], len(s["means"]) - 1)
            self.assertGreaterEqual(report["proposed_rejections"], 2)
            write_points3d(points, sparse_cloud() + 1000)
            failed = subprocess.run(command + ["--overwrite"], capture_output=True, text=True,
                                    timeout=30)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("repère", failed.stderr)

    def test_new_exporter_cli_writes_diagnostics_without_retraining(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            s = scene()
            fields = [
                ("means", ["x", "y", "z"]), ("scales", [f"scale_{i}" for i in range(3)]),
                ("quats", [f"rot_{i}" for i in range(4)]),
                ("dc", [f"f_dc_{i}" for i in range(3)]),
            ]
            vertex = np.zeros(len(s["means"]), dtype=[
                (name, "<f4") for _, names in fields for name in names
            ] + [("opacity", "<f4")])
            for key, names in fields:
                for i, name in enumerate(names):
                    vertex[name] = s[key][:, i]
            source = root / "scene.ply"
            PlyData([PlyElement.describe(vertex, "vertex")]).write(source)
            points = root / "points.bin"
            points.write_bytes(b"unused sparse reference")
            transform = root / "transform.json"
            transform.write_text(json.dumps({"transform": np.eye(4).tolist(), "scale": 1}))
            output = root / "scene.splat"
            result = subprocess.run([
                sys.executable, str(ROOT / "export_ply.py"), "--in-ply", str(source),
                "--points", str(points), "--transform", str(transform), "--out-ply", str(output),
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(output.stat().st_size, len(s["means"]) * 32)
            self.assertEqual(output.with_suffix(".rejected.splat").stat().st_size, 32)


if __name__ == "__main__":
    unittest.main()
