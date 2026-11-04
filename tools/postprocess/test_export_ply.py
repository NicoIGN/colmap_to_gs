import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location(
    "export_ply", Path(__file__).with_name("export_ply.py")
)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def make_splats(count=100):
    return {
        "means": np.zeros((count, 3)),
        "scales": np.full((count, 3), np.log(0.1)),
        "quats": np.tile([1.0, 0, 0, 0], (count, 1)),
        "opacity": np.zeros(count),
        "dc": np.zeros((count, 3)),
    }


class CleanupTest(unittest.TestCase):
    def clean(self, splats, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = export.clean_gaussians(
                splats, np.array([[-1.0] * 3, [1.0] * 3]),
                0.1, 7, world_scale=1.0, **kwargs
            )
        return result, output.getvalue()

    def test_exact_thresholds(self):
        for removed, kept, warning, skipped in [
            (10, 90, False, False),
            (11, 89, True, False),
            (20, 80, True, False),
            (21, 100, True, True),
        ]:
            with self.subTest(removed=removed):
                splats = make_splats()
                splats["means"][:removed] = 100
                result, logs = self.clean(splats)
                self.assertEqual(len(result["means"]), kept)
                self.assertEqual("AVERTISSEMENT" in logs, warning)
                self.assertEqual("ignoré" in logs, skipped)

    def test_denominator_is_remaining_and_filters_continue(self):
        splats = make_splats()
        splats["means"][:20] = 100
        splats["scales"][20:37] = np.log(100)
        splats["opacity"][37:49] = -100
        result, logs = self.clean(splats, min_opacity=0.02)
        self.assertIn("17/80 suppressions", logs)
        self.assertIn("filtre trop grands ignoré", logs)
        self.assertIn("12/80 suppressions", logs)
        self.assertEqual(len(result["means"]), 68)
        self.assertEqual(np.count_nonzero(result["scales"][:, 0] > 0), 17)

    def test_similarity_scale_invariance(self):
        original = make_splats()
        original["scales"][:15] = np.log(100)
        original["means"][:5] = 100
        for factor in (0.001, 1, 1000):
            splats = {key: value.copy() for key, value in original.items()}
            splats["means"] *= factor
            splats["scales"] += np.log(factor)
            colmap = np.array([[-1.0] * 3, [1.0] * 3]) * factor
            with contextlib.redirect_stdout(io.StringIO()):
                distance = export.estimate_radius(colmap, 3, 0)
                result = export.clean_gaussians(
                    splats, colmap, distance, 7, world_scale=factor
                )
            self.assertEqual(len(result["means"]), 85)

    def test_skipped_filters_export_all_records(self):
        splats = make_splats()
        splats["means"][:] = 100
        splats["scales"][:21] = np.log(100)
        splats["opacity"][:21] = -100
        result, logs = self.clean(splats, min_opacity=0.02)
        self.assertEqual(len(result["means"]), 100)
        self.assertEqual(logs.count("ignoré"), 3)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "test.splat"
            export.write_world_splat(result, output, np.eye(4), np.eye(3), 1, 7)
            self.assertEqual(output.stat().st_size, 100 * 32)

    def test_invalid_attributes_do_not_bypass_safety(self):
        splats = make_splats()
        splats["means"][:21] = np.nan
        with self.assertRaisesRegex(ValueError, "export impossible"):
            self.clean(splats)
        splats = make_splats()
        splats["quats"][0] = 0
        result, _ = self.clean(splats)
        self.assertEqual(len(result["means"]), 99)


if __name__ == "__main__":
    unittest.main()
