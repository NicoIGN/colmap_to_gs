import contextlib
import io
from pathlib import Path
import struct
import tempfile
import unittest

import diagnose_colmap_models as diagnostic


def model(folder, names):
    folder.mkdir()
    (folder / "cameras.txt").write_text("1 SIMPLE_PINHOLE 100 100 80 50 50\n")
    (folder / "images.txt").write_text("".join(
        f"{i} 1 0 0 0 0 0 0 1 {name}\n1 2 3\n"
        for i, name in enumerate(names, 1)
    ))


def matches(path, names, records):
    data = struct.pack("<4sII", b"VKMT", 4, len(names))
    for name in names:
        raw = name.encode()
        data += struct.pack("<I", len(raw)) + raw + struct.pack("<I", 3)
    data += struct.pack("<I", len(records))
    for a, b, config, count in records:
        data += struct.pack("<IIiI", a, b, config, count) + b"\0" * (count * 8)
    path.write_bytes(data)


class DiagnoseModelsTest(unittest.TestCase):
    def test_overlap_transitions_and_cross_model_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model(root / "0", ["frame_001.jpg", "frame_002.jpg", "frame_003.jpg"])
            model(root / "1", ["frame_003.jpg", "frame_004.jpg"])
            matches(root / "matches.bin",
                    ["frame_001", "frame_002", "frame_003", "frame_004"],
                    [(1, 3, 2, 50), (2, 3, 2, 30)])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                diagnostic.report(root / "0", root / "1", root / "matches.bin")
            text = output.getvalue()
            self.assertIn("Images communes : 1", text)
            self.assertIn("frame_003.jpg : points 3D observés A=1, B=1", text)
            self.assertIn("coordonnées 2D exactes communes=1", text)
            self.assertIn("Paires entre images exclusives de A et B : 1", text)
            self.assertIn("frame_002.jpg <-> frame_004.jpg : 50 correspondances", text)
            self.assertNotIn("frame_003.jpg <-> frame_004.jpg", text)

    def test_disconnected_models_and_no_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model(root / "0", ["frame_001.jpg"])
            model(root / "1", ["frame_002.jpg"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                diagnostic.report(root / "0", root / "1")
            self.assertIn("Transitions A/B dans l'ordre des noms : 1", output.getvalue())
            self.assertIn("Correspondances : non fournies", output.getvalue())

    def test_truncated_vkmt_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "matches.bin"
            matches(path, ["a", "b"], [(0, 1, 2, 2)])
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError, "tronquée"):
                diagnostic.read_matches(path)

    def test_duplicate_feature_stems_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "ambigus"):
            diagnostic.feature_lookup({"a.jpg", "a.png"}, ["a"])


if __name__ == "__main__":
    unittest.main()
