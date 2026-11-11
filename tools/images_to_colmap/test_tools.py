import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from plyfile import PlyData, PlyElement

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[1]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(ROOT / "tools/libs/python"))
from orientations_to_colmap import convert
from transforms_to_orientations import convert as convert_transforms
from las_to_ply import convert as convert_las
from nerfstudio_runtime import check_runtime
from nerfstudio_block import prepare_dataset, main as block_main
from colmap_split_blocks import read_images_binary, read_points3d_binary


class ConversionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.images = self.root / "images"
        self.images.mkdir()
        for name in ("a.jpg", "b.jpg"):
            Image.new("RGB", (100, 100)).save(self.images / name)
        self.orientations = self.root / "poses.json"
        self.data = {
            "version": 1, "pose_convention": "opencv_world_to_camera",
            "reference": {"type": "local_ENU", "origin_lat_lon_height": [48, 2, 40]},
            "cameras": [{"id": 1, "model": "PINHOLE", "width": 100, "height": 100,
                         "params": [50, 50, 50, 50]}],
            "images": [
                {"name": name, "camera_id": 1, "qvec": [1, 0, 0, 0], "tvec": [offset, 0, 0]}
                for name, offset in (("a.jpg", 0), ("b.jpg", -1))
            ],
        }
        self.orientations.write_text(json.dumps(self.data))
        points = np.array([
            (0., 0., 10., 255, 0, 0), (1., 1., 10., 0, 255, 0),
            (-1., -1., 10., 0, 0, 255),
        ], dtype=[("x", "f8"), ("y", "f8"), ("z", "f8"),
                  ("red", "u1"), ("green", "u1"), ("blue", "u1")])
        self.points = self.root / "points.ply"
        PlyData([PlyElement.describe(points, "vertex")]).write(self.points)
        self.sparse = self.root / "sparse"
        convert(self.orientations, self.points, self.images, self.sparse)

    def test_projected_tracks_are_consistent_and_coordinates_preserved(self):
        sys.path.insert(0, str(ROOT / "tools/images_to_splat"))
        from resume import validate_sparse
        validate_sparse(self.sparse)
        points = read_points3d_binary(self.sparse / "points3D.bin")
        self.assertEqual(len(points), 3)
        np.testing.assert_array_equal(points[1].xyz, [0, 0, 10])
        self.assertEqual(points[1].track, [(1, 1), (2, 1)])

    def test_transforms_round_trip_keeps_world_frame(self):
        transforms = self.root / "transforms.json"
        subprocess.run([
            sys.executable, str(ROOT / "tools/libs/python/colmap_to_transforms.py"),
            "--colmap-model", str(self.sparse), "--images-dir", str(self.images),
            "--output", str(transforms),
        ], check=True, capture_output=True)
        output = self.root / "converted.json"
        convert_transforms(transforms, output)
        converted = json.loads(output.read_text())
        np.testing.assert_allclose(converted["images"][1]["tvec"], [-1, 0, 0])
        np.testing.assert_allclose(converted["images"][1]["qvec"], [1, 0, 0, 0])

    def test_nerfstudio_dataset_initialization_and_mask_paths(self):
        masks = self.root / "masks"
        masks.mkdir()
        for name in ("a.jpg", "b.jpg"):
            Image.new("L", (100, 100), 255).save(masks / (name + ".png"))
        dataset = self.root / "dataset"
        prepare_dataset(self.sparse, self.images, masks, dataset)
        frames = json.loads((dataset / "transforms.json").read_text())["frames"]
        self.assertEqual(frames[0]["mask_path"], "masks/a.jpg.png")
        self.assertEqual(len(PlyData.read(dataset / "sparse_pc.ply")["vertex"]), 3)
        Image.new("L", (2, 2)).save(masks / "a.jpg.png")
        with self.assertRaisesRegex(ValueError, "Masque incompatible"):
            prepare_dataset(self.sparse, self.images, masks, dataset)

    def test_invalid_convention_and_unobserved_cloud_are_rejected(self):
        self.data["pose_convention"] = "unknown"
        self.orientations.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError, "pose_convention"):
            convert(self.orientations, self.points, self.images, self.root / "bad")

    def test_las_conversion_preserves_coordinates_and_decimates(self):
        import laspy
        cloud = laspy.LasData(laspy.LasHeader(point_format=3, version="1.2"))
        cloud.x = [650001, 650002, 650003]
        cloud.y = [6800001, 6800002, 6800003]
        cloud.z = [50, 51, 52]
        cloud.red, cloud.green, cloud.blue = [65535, 0, 257], [0, 0, 0], [0, 0, 0]
        source, output = self.root / "cloud.las", self.root / "cloud.ply"
        cloud.write(source)
        convert_las(source, output, 2)
        vertex = PlyData.read(output)["vertex"]
        np.testing.assert_array_equal(vertex["x"], [650001, 650003])
        np.testing.assert_array_equal(vertex["red"], [255, 1])
        self.assertFalse(output.with_name(output.name + ".partial").exists())

    def test_standalone_conversion_then_global_preparation_reuses_same_model(self):
        output = self.root / "workspace"
        config = self.root / "config.sh"
        config.write_text(
            f'SFM_IMAGES_DIR="{self.images}"\nOUTPUT_DIR="{output}"\n'
            f'PYTHON_BIN="{sys.executable}"\n'
            'COLMAP_METHOD=orientations\nSFM_MASKS=off\nTRAIN_MASKS=off\n'
            f'ORIENTATIONS_JSON="{self.orientations}"\nLIDAR_PLY="{self.points}"\n'
            'SPLIT_NUM_BLOCKS=1\nSPIRULA_BIN=true\n'
        )
        for script, args in (
            (ROOT / "tools/images_to_colmap/run.sh", []),
            (ROOT / "script/images_to_3dgs.sh", ["--phase", "prepare"]),
        ):
            result = subprocess.run(["bash", str(script), str(config), *args],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((output / ".prepare_done").is_file())
        self.assertTrue((output / "blocks/block_000/points3D.bin").is_file())
        dataset = json.loads((output / "colmap_dataset.json").read_text())
        self.assertEqual(dataset["sparse_model"], "sfm/sparse/0")
        self.assertEqual(len(read_images_binary(output / dataset["sparse_model"] / "images.bin")), 2)

    def test_native_colmap_passes_masks_and_normalizes_output(self):
        import reconstruct
        calls = []

        def fake_run(command):
            command = list(map(str, command))
            calls.append(command)
            if command[1] == "mapper":
                sparse = Path(command[command.index("--output_path") + 1])
                shutil.copytree(self.sparse, sparse / "2")

        env = {
            "OUTPUT_DIR": str(self.root / "native"), "COLMAP_METHOD": "colmap",
            "SFM_COLMAP_BIN": "colmap", "SFM_COLMAP_USE_GPU": "false",
            "SFM_COLMAP_MATCHING": "exhaustive", "SFM_CAMERA_MODE": "folder",
            "SFM_CAMERA_MODEL": "opencv", "SFM_MAX_IMAGE_SIZE": "2048",
            "SFM_ALIKED_MAX_FEATURES": "4096", "SFM_THREADS": "4",
            "SFM_MASKS": "on", "SFM_MAX_ERROR": "2", "SFM_MIN_INLIERS": "30",
            "SFM_MIN_TRI_ANGLE": "3",
        }
        with patch.dict(os.environ, env), patch.object(reconstruct, "run", fake_run):
            reconstruct.reconstruct()
        self.assertIn("--ImageReader.mask_path", calls[0])
        self.assertEqual(calls[0][calls[0].index("--SiftExtraction.use_gpu") + 1], "0")
        self.assertTrue((Path(env["OUTPUT_DIR"]) / "sfm/sparse/0/points3D.bin").is_file())

    def test_ns_process_data_preserves_names_and_checks_cuda(self):
        import reconstruct
        calls = []

        def fake_run(command):
            command = list(map(str, command))
            calls.append(command)
            output = Path(command[command.index("--output-dir") + 1])
            shutil.copytree(self.sparse, output / "colmap/sparse/0")

        env = {
            "OUTPUT_DIR": str(self.root / "ns"), "COLMAP_METHOD": "nerfstudio",
            "SFM_COLMAP_MATCHING": "exhaustive", "SFM_COLMAP_BIN": "colmap",
            "SFM_NS_PROCESS_DATA_BIN": "ns-process-data", "SFM_CAMERA_MODEL": "opencv",
        }
        Path(env["OUTPUT_DIR"]).mkdir()
        (Path(env["OUTPUT_DIR"]) / "images").symlink_to(self.images, target_is_directory=True)
        with patch.dict(os.environ, env), patch.object(reconstruct, "run", fake_run), \
                patch("nerfstudio_runtime.check_runtime") as check:
            reconstruct.reconstruct()
        check.assert_called_once()
        self.assertIn("--skip-image-processing", calls[0])
        self.assertTrue((Path(env["OUTPUT_DIR"]) / "sfm/sparse/0/images.bin").is_file())

    def test_nerfstudio_worker_exports_world_frame_and_refuses_changed_signature(self):
        import nerfstudio_block
        calls = []

        def fake_run(command):
            command = list(map(str, command))
            calls.append(command)
            if command[0] == "ns-train":
                self.assertIn("--pipeline.model.camera-optimizer.mode", command)
                run = self.root / "trained/train/block/splatfacto/run"
                run.mkdir(parents=True)
                (run / "config.yml").write_text("test")
                (run / "dataparser_transforms.json").write_text(json.dumps({
                    "transform": [[1, 0, 0, 10], [0, 1, 0, 0], [0, 0, 1, 0]],
                    "scale": 2,
                }))
            elif command[0] == "ns-export":
                target = self.root / "trained/ns-export"
                target.mkdir(exist_ok=True)
                fields = ["x", "y", "z", "scale_0", "scale_1", "scale_2",
                          "rot_0", "rot_1", "rot_2", "rot_3",
                          "opacity", "f_dc_0", "f_dc_1", "f_dc_2"]
                vertex = np.zeros(1, dtype=[(field, "f4") for field in fields])
                vertex["x"], vertex["rot_0"] = 22, 1
                PlyData([PlyElement.describe(vertex, "vertex")]).write(target / "splat.ply")
            else:
                subprocess.run(command, check=True, capture_output=True)

        args = ["worker", "--sparse-dir", str(self.sparse), "--images-dir", str(self.images),
                "--output-dir", str(self.root / "trained"), "--name", "test.splat"]
        with patch.object(nerfstudio_block, "check_runtime"), \
                patch.object(nerfstudio_block, "run", fake_run), patch.object(sys, "argv", args):
            block_main()
            raw = self.root / "trained/splats/test.splat"
            xyz = np.frombuffer(raw.read_bytes()[:12], dtype="<f4")
            np.testing.assert_allclose(xyz, [1, 0, 0])
            count = len(calls)
            block_main()
            self.assertEqual(len(calls), count)
            with patch.dict(os.environ, TRAIN_NUM_ITER="123"):
                with self.assertRaises(SystemExit):
                    block_main()


class RuntimeTest(unittest.TestCase):
    def test_missing_torch_has_explicit_error(self):
        with patch.dict(sys.modules, {"torch": None}):
            with self.assertRaisesRegex(RuntimeError, "PyTorch"):
                check_runtime()

    def test_cpu_torch_has_explicit_nvidia_error(self):
        from types import SimpleNamespace
        torch = SimpleNamespace(version=SimpleNamespace(cuda=None))
        with patch.dict(sys.modules, {"torch": torch}):
            with self.assertRaisesRegex(RuntimeError, "NVIDIA"):
                check_runtime()

    def test_cuda_build_without_available_device_is_rejected(self):
        from types import SimpleNamespace
        torch = SimpleNamespace(
            version=SimpleNamespace(cuda="12.1"),
            cuda=SimpleNamespace(is_available=lambda: False),
        )
        with patch.dict(sys.modules, {"torch": torch}):
            with self.assertRaisesRegex(RuntimeError, "NVIDIA"):
                check_runtime()


if __name__ == "__main__":
    unittest.main()
