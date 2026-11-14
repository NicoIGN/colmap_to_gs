import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import pipeline_options as options
from fetch_ign_lidar import CRS, file_signature
from merge_splats import SPLAT_DTYPE


class PipelineOptionTests(unittest.TestCase):
    def test_configuration_validation(self):
        config = dict(options.DEFAULTS)
        options.validate(config)
        for key, value in (("LIDAR_COLMAP_INJECT", "true"),
                           ("LIDAR_CLEAN_MIN_OPACITY", "0"),
                           ("LIDAR_COLMAP_MIN_VIEWS", "1"),
                           ("LIDAR_COLMAP_SCALE_STEPS", "1.5"),
                           ("LIDAR_CLEAN_MODE", "silent"),
                           ("LIDAR_COLMAP_MAX_TILT", "90")):
            changed = dict(config, **{key: value})
            with self.assertRaises(ValueError, msg=key):
                options.validate(changed)

    def test_aligned_cleanup_never_runs_registration_and_reuses_result(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            shared = workspace/"lidar/lidar.laz"
            shared.parent.mkdir()
            shared.write_bytes(b"shared")
            shared.with_suffix(".laz.json").write_text(json.dumps({
                "crs": CRS, "bbox": [649900, 6859900, 650100, 6860100],
                "file_signature": file_signature(shared)}))
            source = workspace/"scene.splat"
            raw = np.zeros(2, dtype=SPLAT_DTYPE)
            raw["position"] = [[0, 0, 0], [1, 1, 1]]
            raw["scale"] = 0.01
            raw["rotation"] = [255, 128, 128, 128]
            raw.tofile(source)
            initial = source.read_bytes()
            config = dict(options.DEFAULTS, LIDAR_COLMAP_ALIGN="true",
                          LIDAR_CLEAN="true", LIDAR_CLEAN_MODE="apply",
                          LIDAR_COLMAP_ORIGIN="650000,6860000,0")
            def filter_only(command, check):
                self.assertTrue(command[1].endswith("filter_splats_by_lidar.py"))
                self.assertIn(str(shared), command)
                output = Path(command[command.index("--output")+1])
                raw[:1].tofile(output)
            with patch.object(options.subprocess, "run", side_effect=filter_only) as run:
                options.clean(config, workspace, source)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(source.stat().st_size, 32)
                options.clean(config, workspace, source)
                source.write_bytes(initial)
                options.clean(config, workspace, source)
                self.assertEqual(source.stat().st_size, 32)
                self.assertEqual(run.call_count, 1)
            self.assertEqual(source.with_suffix(".before_lidar.splat").read_bytes(), initial)

    def test_disabled_hooks_do_not_modify_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options.prepare(options.DEFAULTS, root, root/"model", root/"images")
            options.clean(options.DEFAULTS, root, root/"missing.splat")
            self.assertEqual(list(root.iterdir()), [])

    def test_prepare_signatures_reuse_and_reject_changed_settings(self):
        from test_colmap_lidar import reconstruction

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, workspace, images = root/"source", root/"workspace", root/"images"
            source.mkdir()
            images.mkdir()
            (images/"1.png").write_bytes(b"image")
            model, _ = reconstruction()
            model.write(source)
            config = dict(options.DEFAULTS, LIDAR_COLMAP_ALIGN="true",
                          LIDAR_COLMAP_INJECT="true", LIDAR_COLMAP_ALREADY_PROJECTED="true")
            def master(command, check):
                self.assertIn("--inject", command)
                self.assertIn("--already-projected", command)
                workspace.mkdir()
                shutil.copytree(source, workspace/"colmap_lidar")
                shared = workspace/"lidar/lidar.laz"
                shared.parent.mkdir()
                shared.write_bytes(b"laz")
                shared.with_suffix(".laz.json").write_text(json.dumps({
                    "crs": CRS, "bbox": [1, 2, 3, 4],
                    "file_signature": file_signature(shared)}))
            with patch.object(options.subprocess, "run", side_effect=master) as run:
                options.prepare(config, workspace, source, images)
                options.prepare(config, workspace, source, images, check=True)
                self.assertEqual(run.call_count, 1)
                changed = dict(config, LIDAR_COLMAP_INJECT_VOXEL="0.4")
                with self.assertRaisesRegex(ValueError, "modifiés"):
                    options.prepare(changed, workspace, source, images, check=True)
                (images/"1.png").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "modifiés"):
                    options.prepare(config, workspace, source, images, check=True)


if __name__ == "__main__":
    unittest.main()
