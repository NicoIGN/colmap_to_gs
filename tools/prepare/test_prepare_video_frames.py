import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from tools.prepare.prepare_video_frames import scan_video, select_indices

SCRIPT = Path(__file__).with_name("prepare_video_frames.py")


def write_video(path, frames=30, fps=10, size=(64, 48), seed=0):
    rng = np.random.default_rng(seed)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    for _ in range(frames):
        writer.write(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))
    writer.release()


class SelectIndicesTest(unittest.TestCase):
    def test_low_sharpness_segment_does_not_create_temporal_gap(self):
        sharp = [1, 2, 3, 10, 11, 12, 13, 14, 15, 16]
        positions = [i / 10 for i in range(10)]
        selected = select_indices(sharp, positions, num_frames=10)
        self.assertEqual(selected, list(range(10)))

    def test_prefers_sharper_frame_within_each_time_slice(self):
        sharp = [5, 9, 1, 7, 2, 8, 3, 6]
        positions = [i / 8 for i in range(8)]
        selected = select_indices(sharp, positions, num_frames=4)
        self.assertEqual(selected, [1, 3, 5, 7])

    def test_weight_balances_sharpness_against_temporal_center(self):
        sharp = [100, 90, 100, 90]
        positions = [0.0, 0.25, 0.5, 0.75]
        self.assertEqual(select_indices(sharp, positions, 2, sharpness_weight=0.7), [1, 3])
        self.assertEqual(select_indices(sharp, positions, 2, sharpness_weight=1.0), [0, 2])
        self.assertEqual(select_indices(sharp, positions, 2, sharpness_weight=0.0), [1, 3])

    def test_results_follow_time_not_candidate_array_order(self):
        selected = select_indices([10, 20, 30, 40], [0.75, 0.25, 0.0, 0.5], num_frames=4)
        self.assertEqual(selected, [2, 1, 3, 0])

    def test_empty_input(self):
        self.assertEqual(select_indices([], [], num_frames=3), [])

    def test_scan_rejects_truncated_video_decoding(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        with patch("tools.prepare.prepare_video_frames.read_video_info", return_value=(30, 10)), \
                patch("tools.prepare.prepare_video_frames.iter_frames", return_value=iter([(0, frame)])):
            with self.assertRaisesRegex(RuntimeError, "Lecture incomplète"):
                scan_video(Path("test.avi"), 4)


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True)

    def test_single_video_writes_flat_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_video(tmp / "a.avi")
            result = self.run_cli("--video", str(tmp / "a.avi"), "--out", str(tmp / "out"),
                                  "--num-frames", "4")
            self.assertEqual(result.returncode, 0, result.stderr)
            frames = sorted((tmp / "out").glob("frame_*.jpg"))
            self.assertEqual(len(frames), 4)
            self.assertEqual(frames[0].name, "frame_00000.jpg")

    def test_multiple_videos_get_subfolders_and_reset_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_video(tmp / "a.avi", seed=1)
            write_video(tmp / "b.avi", seed=2)
            args = ("--video", str(tmp / "a.avi"), str(tmp / "b.avi"),
                    "--out", str(tmp / "out"), "--num-frames", "3")
            self.assertEqual(self.run_cli(*args).returncode, 0)
            for name in ("a", "b"):
                self.assertEqual(len(list((tmp / "out" / name).glob("frame_*.jpg"))), 3)
            again = self.run_cli(*args)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(self.run_cli(*args, "--reset").returncode, 0)

    def test_index_csv_matches_selected_video_frames_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            video = tmp / "ordered.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
            for i in range(30):
                writer.write(np.full((48, 64, 3), i * 7, np.uint8))
            writer.release()
            index = tmp / "frames.csv"
            result = self.run_cli("--video", str(video), "--out", str(tmp / "out"),
                                  "--num-frames", "10", "--index-csv", str(index))
            self.assertEqual(result.returncode, 0, result.stderr)
            with index.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 10)
            indices = [int(row["video_frame_index"]) for row in rows]
            self.assertEqual(indices, sorted(indices))
            self.assertEqual(len(set(indices)), 10)
            self.assertLessEqual(max(b - a for a, b in zip(indices, indices[1:])), 5)
            for row, source_index in zip(rows, indices):
                frame = cv2.imread(str(tmp / "out" / row["image"]))
                self.assertIsNotNone(frame)
                self.assertLess(abs(float(frame.mean()) - source_index * 7), 3)

    def test_invalid_sharpness_weight_is_rejected(self):
        result = self.run_cli("--video", "missing.avi", "--out", "out", "--num-frames", "4",
                              "--sharpness-weight", "1.1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--sharpness-weight doit", result.stderr)


if __name__ == "__main__":
    unittest.main()
