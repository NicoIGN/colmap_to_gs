import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from tools.preprocess.filter_sparse_density import (
    POINT_HEADER,
    density_votes,
    filter_sparse_model,
)


def image_view(point_count, center=(0, 0, 0)):
    return {
        "indices": np.arange(point_count),
        "center": np.asarray(center, dtype=np.float64),
    }


def write_model(folder, positions, image_count=2):
    folder.mkdir()
    (folder / "rigs.bin").write_bytes(b"rig metadata")
    (folder / "frames.bin").write_bytes(b"frame metadata")
    (folder / "cameras.bin").write_bytes(
        struct.pack("<QIiQQ3d", 1, 1, 0, 640, 480, 500, 320, 240)
    )
    with (folder / "images.bin").open("wb") as stream:
        stream.write(struct.pack("<Q", image_count))
        for image_id in range(1, image_count + 1):
            stream.write(struct.pack(
                "<I7dI", image_id, 1, 0, 0, 0, 0, 0, 0, 1
            ))
            stream.write(f"image_{image_id}.jpg".encode() + b"\0")
            stream.write(struct.pack("<Q", len(positions)))
            for point_index in range(len(positions)):
                stream.write(struct.pack(
                    "<ddq", float(point_index), 0.0, point_index + 1
                ))
    with (folder / "points3D.bin").open("wb") as stream:
        stream.write(struct.pack("<Q", len(positions)))
        for point_index, xyz in enumerate(positions):
            stream.write(POINT_HEADER.pack(
                point_index + 1, *xyz, 120, 130, 140, 0.1, image_count
            ))
            for image_id in range(1, image_count + 1):
                stream.write(struct.pack("<II", image_id, point_index))


def read_output_point_ids(path):
    ids = []
    with path.open("rb") as stream:
        count = struct.unpack("<Q", stream.read(8))[0]
        for _ in range(count):
            header = stream.read(POINT_HEADER.size)
            fields = POINT_HEADER.unpack(header)
            ids.append(fields[0])
            stream.seek(fields[-1] * 8, 1)
    return ids


def read_image_point_ids(path):
    images = []
    with path.open("rb") as stream:
        count = struct.unpack("<Q", stream.read(8))[0]
        for _ in range(count):
            header = stream.read(struct.calcsize("<I7dI"))
            image_id, = struct.unpack_from("<I", header)
            while stream.read(1) != b"\0":
                pass
            observation_count = struct.unpack("<Q", stream.read(8))[0]
            row = []
            for _ in range(observation_count):
                observation = stream.read(24)
                row.append(struct.unpack("<q", observation[16:])[0])
            images.append((image_id, row))
    return images


class SparseDensityTest(unittest.TestCase):
    def test_cli_progress_and_verbose_preserve_json_and_filtered_model(self):
        positions = np.vstack((np.tile([0, 0, 10.0], (20, 1)), [[0, 0, 100]]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sparse"
            write_model(source, positions)
            results = []
            for name, options in (("progress", ["--verbose"]),
                                  ("quiet", ["--no-progress"])):
                output = root / name
                result = subprocess.run(
                    [sys.executable, "-m", "tools.preprocess.filter_sparse_density",
                     "--input", str(source), "--output", str(output), *options],
                    capture_output=True, text=True, check=True,
                )
                report = json.loads(result.stdout)
                self.assertEqual(report["points_removed"], 1)
                if name == "progress":
                    for message in ("Lecture points 3D", "Lecture images",
                                    "Analyse densité", "Points vue", "Écriture sparse",
                                    "progress_clean", "progress_rejected",
                                    "[sparse] Terminé"):
                        self.assertIn(message, result.stderr)
                else:
                    self.assertEqual(result.stderr, "")
                results.append(output)
            for suffix in ("_clean", "_rejected"):
                for filename in ("cameras.bin", "images.bin", "points3D.bin"):
                    self.assertEqual(
                        (root / (results[0].name + suffix) / filename).read_bytes(),
                        (root / (results[1].name + suffix) / filename).read_bytes())
            self.assertFalse(list(root.rglob("*.ply")))

    def test_isolated_far_point_rejected_but_several_depth_packets_survive(self):
        rng = np.random.default_rng(42)
        angles = rng.uniform(-0.02, 0.02, (24, 2))
        directions = np.column_stack((angles, np.ones(24)))
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        positions = np.vstack((
            directions[:12] * 10,
            directions[12:] * 20,
            [[0, 0, 100]],
        ))
        votes, evaluated = density_votes(
            positions,
            [image_view(len(positions)), image_view(len(positions), (0.1, 0, 0))],
            neighbors=128,
            min_neighbors=12,
        )
        self.assertTrue(np.all(evaluated == 2))
        self.assertTrue(np.all(votes[:-1] == 0))
        self.assertEqual(votes[-1], 2)

    def test_far_packet_needs_increasing_support_relative_to_near_packet(self):
        rng = np.random.default_rng(42)
        angles = rng.uniform(-0.02, 0.02, (25, 2))
        directions = np.column_stack((angles, np.ones(25)))
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        positions = np.vstack((
            directions[:20] * 10,
            directions[20:] * 40,
        ))
        votes, evaluated = density_votes(
            positions,
            [image_view(len(positions)), image_view(len(positions), (0.1, 0, 0))],
            min_neighbors=12,
            min_cluster_points=5,
            min_cluster_fraction=0.05,
        )
        self.assertTrue(np.all(evaluated == 2))
        self.assertTrue(np.all(votes[:20] == 0))
        self.assertTrue(np.all(votes[20:] == 2))

    def test_parallel_angular_queries_preserve_votes(self):
        rng = np.random.default_rng(23)
        positions = rng.normal(size=(500, 3))
        images = [image_view(len(positions)), image_view(len(positions), (0.2, 0, 0))]
        serial = density_votes(positions, images, workers=1)
        parallel = density_votes(positions, images, workers=2)
        np.testing.assert_array_equal(parallel[0], serial[0])
        np.testing.assert_array_equal(parallel[1], serial[1])

    def test_insufficient_camera_views_do_not_make_a_removal_decision(self):
        positions = np.vstack((np.tile([0, 0, 10.0], (20, 1)), [[0, 0, 100]]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sparse"
            output = root / "filtered"
            write_model(source, positions, image_count=1)
            report = filter_sparse_model(
                source, output, min_neighbors=12, min_cluster_points=5
            )
            self.assertEqual(report["points_removed"], 0)
            self.assertEqual(len(read_output_point_ids(
                root / "filtered_clean/points3D.bin")), 21)
            self.assertEqual(read_output_point_ids(root / "filtered_rejected/points3D.bin"), [])
            self.assertTrue(all(refs == [-1] * 21 for _, refs in
                                read_image_point_ids(root / "filtered_rejected/images.bin")))

    def test_filter_writes_consistent_sparse_and_report(self):
        rng = np.random.default_rng(7)
        angles = rng.uniform(-0.02, 0.02, (20, 2))
        directions = np.column_stack((angles, np.ones(20)))
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        positions = np.vstack((directions * 10, [0, 0, 100]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sparse"
            output = root / "filtered"
            write_model(source, positions)
            report = filter_sparse_model(
                source, output, min_neighbors=12, min_cluster_points=5
            )
            output = root / "filtered_clean"
            rejected = root / "filtered_rejected"
            self.assertEqual(report["points_removed"], 1)
            self.assertEqual(read_output_point_ids(output / "points3D.bin"), list(range(1, 21)))
            image_refs = read_image_point_ids(output / "images.bin")
            self.assertEqual(len(image_refs), 2)
            self.assertTrue(all(refs[-1] == -1 for _, refs in image_refs))
            self.assertTrue(all(refs[:-1] == list(range(1, 21)) for _, refs in image_refs))
            self.assertEqual(
                json.loads((output / "density_report.json").read_text())["points_kept"],
                20,
            )
            self.assertFalse(list(root.rglob("*.ply")))
            self.assertEqual(read_output_point_ids(rejected / "points3D.bin"), [21])
            self.assertTrue(all(refs == [-1] * 20 + [21] for _, refs in
                                read_image_point_ids(rejected / "images.bin")))
            self.assertEqual((rejected / "cameras.bin").read_bytes(),
                             (source / "cameras.bin").read_bytes())
            from tools.images_to_splat.resume import validate_sparse
            validate_sparse(output)
            validate_sparse(rejected)
            self.assertEqual((output / "rigs.bin").read_bytes(), b"rig metadata")
            self.assertEqual((output / "frames.bin").read_bytes(), b"frame metadata")
            self.assertEqual(
                (source / "points3D.bin").stat().st_size,
                (output / "points3D.bin").stat().st_size + POINT_HEADER.size + 16,
            )

    def test_default_output_names_and_input_protection(self):
        positions = np.tile([0, 0, 10.0], (20, 1))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "model"
            write_model(source, positions)
            original = (source / "points3D.bin").read_bytes()
            report = filter_sparse_model(source)
            self.assertEqual(report["output_clean"], str(root / "model_clean"))
            self.assertEqual(report["output_rejected"], str(root / "model_rejected"))
            self.assertEqual((source / "points3D.bin").read_bytes(), original)
            protected = root / "protected_clean"
            write_model(protected, positions)
            with self.assertRaisesRegex(ValueError, "distincts"):
                filter_sparse_model(protected, root / "protected")


if __name__ == "__main__":
    unittest.main()
