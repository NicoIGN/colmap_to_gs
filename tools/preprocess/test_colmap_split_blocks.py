import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

from tools.preprocess import colmap_split_blocks as splitter


def graph(n, edges):
    rows, cols, weights = [], [], []
    for a, b, weight in edges:
        rows.extend((a, b))
        cols.extend((b, a))
        weights.extend((weight, weight))
    return sp.csr_matrix((weights, (rows, cols)), shape=(n, n))


class SoftBalanceTest(unittest.TestCase):
    def balance(self, labels, W, k):
        with contextlib.redirect_stdout(io.StringIO()):
            result = splitter.balance_primary_blocks(labels, W, k)
        self.assertEqual(len(result), len(labels))
        self.assertEqual(set(result), set(range(k)))
        for label in range(k):
            nodes = np.flatnonzero(result == label)
            self.assertEqual(connected_components(
                W[nodes][:, nodes], directed=False, return_labels=False,
            ), 1)
        return result

    def test_sizes_within_tolerance_are_not_forced_to_equality(self):
        W = graph(20, [(i, i + 1, 1) for i in range(19)])
        labels = np.array([0] * 12 + [1] * 8)
        result = self.balance(labels, W, 2)
        np.testing.assert_array_equal(result, labels)

    def test_path_moves_boundary_only_until_sizes_are_close(self):
        W = graph(20, [(i, i + 1, 1) for i in range(19)])
        labels = np.array([0] * 18 + [1] * 2)
        original = labels.copy()
        result = self.balance(labels, W, 2)
        self.assertEqual(np.bincount(result).tolist(), [12, 8])
        np.testing.assert_array_equal(labels, original)

    def test_least_cut_weight_boundary_is_preferred(self):
        edges = [(i, j, 1 if 11 in (i, j) else 20)
                 for i in range(13) for j in range(i + 1, 13)]
        edges += [(i, j, 20) for i in range(13, 20) for j in range(i + 1, 20)]
        edges += [(11, 13, 100), (12, 13, 1)]
        result = self.balance(np.array([0] * 13 + [1] * 7), graph(20, edges), 2)
        self.assertEqual(np.bincount(result).tolist(), [12, 8])
        self.assertEqual(result[11], 1)
        self.assertEqual(result[12], 0)

    def test_four_blocks_can_relay_images_through_intermediate_blocks(self):
        W = graph(1421, [(i, i + 1, 1) for i in range(1420)])
        labels = np.repeat(np.arange(4), [800, 300, 200, 121])
        result = self.balance(labels, W, 4)
        counts = np.bincount(result)
        self.assertTrue(np.all(counts >= 284), counts)
        self.assertTrue(np.all(counts <= 427), counts)

    def test_partition_keeps_four_strong_groups_separated_at_weak_links(self):
        edges = [(i, j, 10) for start in range(0, 40, 10)
                 for i in range(start, start + 10) for j in range(i + 1, start + 10)]
        edges += [(9, 10, 0.01), (19, 20, 0.01), (29, 30, 0.01)]
        W = graph(40, edges)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            labels = splitter.spectral_partition(W, 4)
            labels = splitter.enforce_connectivity(labels, W)
        result = self.balance(labels, W, 4)
        for start in range(0, 40, 10):
            self.assertEqual(len(np.unique(result[start:start + 10])), 1)
        self.assertEqual(np.bincount(result).tolist(), [10, 10, 10, 10])

    def test_articulation_and_disconnected_graphs_warn_instead_of_failing(self):
        cases = [
            graph(10, [(0, i, 1) for i in range(1, 10)]),
            graph(10, [(i, i + 1, 1) for i in range(8)]),
        ]
        for W in cases:
            with self.subTest(edges=W.nnz), mock.patch.object(splitter, "warn") as warning:
                labels = np.array([0] * 9 + [1])
                result = self.balance(labels, W, 2)
                np.testing.assert_array_equal(result, labels)
                warning.assert_called_once()
                self.assertIn("découpage est conservé", warning.call_args.args[0])

    def test_small_and_singleton_blocks(self):
        for n, k, labels in ((1, 1, [0]), (3, 2, [0, 0, 1]), (3, 3, [0, 1, 2])):
            with self.subTest(n=n, k=k):
                W = graph(n, [(i, i + 1, 1) for i in range(n - 1)])
                result = self.balance(np.array(labels), W, k)
                np.testing.assert_array_equal(result, labels)

    def test_export_continues_when_connected_blocks_cannot_be_balanced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "input", root / "blocks"
            source.mkdir()
            (source / "cameras.txt").write_text("1 PINHOLE 100 100 50 50 50 50\n")
            # A star of seven images, linked to a three-image triangle.
            edges = [(0, i) for i in range(1, 8)] + [(7, 8), (8, 9), (7, 9)]
            observations = [[] for _ in range(10)]
            point_lines = []
            for a, b in edges:
                for _ in range(3):
                    pid = len(point_lines) + 1
                    point_lines.append(
                        f"{pid} {pid * 0.125} {-pid * 0.25} {1 + pid * 0.0625} 255 255 255 0 "
                        f"{a + 1} {len(observations[a])} {b + 1} {len(observations[b])}\n"
                    )
                    observations[a].append(pid)
                    observations[b].append(pid)
            (source / "points3D.txt").write_text("".join(point_lines))
            (source / "images.txt").write_text("".join(
                f"{i + 1} 0.9238795325112867 0 0 0.3826834323650898 {i * 0.125} {-i * 0.25} 2 1 image-{i}.jpg\n"
                + " ".join(f"0 0 {pid}" for pid in pids) + "\n"
                for i, pids in enumerate(observations)
            ))
            argv = ["split", "--input", str(source), "--output", str(output),
                    "--num-blocks", "2", "--overlap-max-fraction", "0"]
            # Reproduce the post-spectral partition that used to raise.
            with (mock.patch.object(sys, "argv", argv),
                  mock.patch.object(splitter, "spectral_partition",
                                    return_value=np.array([0] * 7 + [1] * 3)),
                  mock.patch.object(splitter, "warn") as warning,
                  contextlib.redirect_stdout(io.StringIO()),
                  contextlib.redirect_stderr(io.StringIO())):
                splitter.main()
            self.assertTrue(any("Équilibrage souple arrêté" in call.args[0]
                                for call in warning.call_args_list))
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual([block["num_primary_images"] for block in manifest["blocks"]], [7, 3])
            memberships = []
            with contextlib.redirect_stderr(io.StringIO()):
                source_cameras, source_images, source_points, _ = splitter.load_colmap_model(source)
            for block in range(2):
                folder = output / f"block_{block:03d}"
                for name in ("cameras.bin", "images.bin", "points3D.bin"):
                    self.assertGreater((folder / name).stat().st_size, 0)
                with contextlib.redirect_stderr(io.StringIO()):
                    cameras, images, points, _ = splitter.load_colmap_model(folder)
                self.assertEqual(len(images), (7, 3)[block])
                for iid, image in images.items():
                    np.testing.assert_array_equal(image.qvec, source_images[iid].qvec)
                    np.testing.assert_array_equal(image.tvec, source_images[iid].tvec)
                    np.testing.assert_allclose(splitter.image_center_world(image),
                                               splitter.image_center_world(source_images[iid]))
                for pid, point in points.items():
                    np.testing.assert_array_equal(point.xyz, source_points[pid].xyz)
                for cid, camera in cameras.items():
                    np.testing.assert_array_equal(camera.params, source_cameras[cid].params)
                memberships.extend(json.loads((folder / "image_membership.json").read_text())["primary_image_ids"])
            self.assertEqual(sorted(memberships), list(range(1, 11)))


if __name__ == "__main__":
    unittest.main()
