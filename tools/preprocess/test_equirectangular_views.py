import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from tools.preprocess import equirectangular_views as views


class ProjectionTest(unittest.TestCase):
    def test_face_centers_and_longitude_seam(self):
        centers = {
            "front": (400, 200), "right": (600, 200),
            "back": (0, 200), "left": (200, 200),
            "up": (400, 0), "down": (400, 399),
        }
        for face, (x, y) in centers.items():
            u, v = views.view_map(800, 400, 3, face)
            # At the poles longitude is undefined.
            if face not in ("up", "down"):
                self.assertAlmostEqual(float((u[1, 1] + .5) % 800), x, places=4)
            self.assertAlmostEqual(float(v[1, 1]), y if face == "down" else max(0, y - .5), places=4)
        u, _ = views.view_map(800, 400, 5, "back")
        self.assertTrue(np.any(u < 100) and np.any(u > 700))

    def test_manifest_detects_changed_sources_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, target, manifest = root / "source", root / "views", root / "manifest.json"
            source.mkdir()
            Image.new("RGB", (40, 20), (10, 20, 30)).save(source / "one.png")
            views.prepare(source, target, manifest, 8)
            self.assertEqual(len(json.loads(manifest.read_text())["inputs"]["views"]), 6)
            views.prepare(source, target, manifest, 8, check=True)
            (target / "front/one.png.jpg").write_bytes(b"corrupted")
            with self.assertRaisesRegex(ValueError, "modifiées"):
                views.prepare(source, target, manifest, 8, check=True)

    def test_invalid_panorama_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            Image.new("RGB", (20, 20)).save(source / "photo.png")
            with self.assertRaisesRegex(ValueError, "2:1"):
                views.prepare(source, root / "views", root / "manifest.json", 8)
            self.assertFalse((root / "views").exists())

    def test_local_input_selection_projects_and_checks_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "source", root / "workspace"
            source.mkdir()
            Image.new("RGB", (40, 20)).save(source / "one.jpg")
            env = dict(os.environ, SFM_IMAGES_DIR=str(source), OUTPUT_DIR=str(output),
                       SFM_IMAGES_RECURSIVE="true", SFM_EQUIRECTANGULAR_VIEWS="true",
                       SFM_EQUIRECTANGULAR_VIEW_SIZE="8", SFM_MASKS="off", TRAIN_MASKS="off",
                       COLMAP_METHOD="hloc", SFM_COLMAP_MATCHING="exhaustive", SFM_COLMAP_USE_GPU="false",
                       HLOC_FEATURES="sift", HLOC_MATCHER="NN-mutual", HLOC_PAIRS="exhaustive",
                       HLOC_RETRIEVAL="netvlad", HLOC_NUM_NEIGHBORS="20")
            script = Path(__file__).resolve().parents[1] / "images_to_splat/prepare_inputs.py"
            def execute(*args):
                return subprocess.run([sys.executable, str(script), *args], env=env,
                                      capture_output=True, text=True)
            result = execute()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "panoramas/one.jpg").is_symlink())
            self.assertEqual(len(list((output / "images").rglob("*.jpg"))), 6)
            (output / ".prepare_done").touch()
            result = execute("--check")
            self.assertEqual(result.returncode, 0, result.stderr)
            Image.new("RGB", (40, 20), (255, 0, 0)).save(source / "one.jpg")
            result = execute("--check")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("paramètres", result.stderr)
