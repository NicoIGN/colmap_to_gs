import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from tools.preprocess.extract_sharp_frames import select_indices

SCRIPT = Path(__file__).with_name("extract_sharp_frames.py")


def write_video(path, frames=30, fps=10, size=(64, 48), seed=0):
    rng = np.random.default_rng(seed)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    for _ in range(frames):
        writer.write(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))
    writer.release()


class SelectIndicesTest(unittest.TestCase):
    def test_blurry_candidates_are_dropped(self):
        sharp = [1, 2, 3, 10, 11, 12, 13, 14, 15, 16]
        thumbs = [np.full((2, 2), 10 * i, np.uint8) for i in range(10)]
        positions = [i / 10 for i in range(10)]
        selected = select_indices(sharp, thumbs, positions, num_frames=10)
        self.assertFalse({0, 1, 2} & set(selected))
        self.assertEqual(len(selected), 7)

    def test_one_sharpest_frame_per_time_slice(self):
        sharp = [5, 9, 1, 7, 2, 8, 3, 6]
        thumbs = [np.full((2, 2), 20 * i, np.uint8) for i in range(8)]
        positions = [i / 8 for i in range(8)]
        selected = select_indices(sharp, thumbs, positions, num_frames=4)
        self.assertEqual(selected, [1, 3, 5, 7])

    def test_near_duplicates_are_dropped(self):
        sharp = [5, 5, 5, 5, 9]
        values = [0, 1, 1.01, 50, 100]
        thumbs = [np.full((2, 2), v, np.float32) for v in values]
        positions = [0.0, 0.25, 0.5, 0.75, 0.99]
        selected = select_indices(sharp, thumbs, positions, num_frames=4)
        self.assertEqual(selected, [0, 4])

    def test_empty_input(self):
        self.assertEqual(select_indices([], [], [], num_frames=3), [])


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


if __name__ == "__main__":
    unittest.main()
