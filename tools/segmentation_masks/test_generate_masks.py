from argparse import Namespace
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_masks as gm

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


class FakeSegmenter:
    """Top quarter of the upright image is 'sky' (id 2), with probability 0.9."""

    id2label = {0: "wall", 1: "person", 2: "sky", 3: "car"}

    def __init__(self):
        self.calls = 0

    def probability(self, image, label_ids):
        self.calls += 1
        probability = np.zeros((image.height, image.width), np.float32)
        if 2 in label_ids:
            probability[: image.height // 4] = 0.9
        if 3 in label_ids:
            probability[-2:] = 0.95
        return probability


class PureFunctionsTest(unittest.TestCase):
    def test_parse_labels_dedupes_and_normalizes(self):
        self.assertEqual(gm.parse_labels(" sky, Person ,sky,traffic   light,"),
                         ["sky", "Person", "traffic light"])
        with self.assertRaises(ValueError):
            gm.parse_labels(" , ")

    def test_resolve_label_ids_case_insensitive_and_numeric(self):
        labels = {0: "Sky", 1: "Person", 2: "Car"}
        self.assertEqual(gm.resolve_label_ids(labels, ["sky", "2"]), [0, 2])
        with self.assertRaisesRegex(ValueError, "nuage"):
            gm.resolve_label_ids(labels, ["nuage"])
        with self.assertRaisesRegex(ValueError, "proches : sky"):
            gm.resolve_label_ids(labels, ["skyy"])

    def test_keep_mask_threshold_and_dilation(self):
        probability = np.zeros((9, 9))
        probability[4, 4] = 0.6
        keep = gm.keep_mask(probability, 0.5, 0)
        self.assertEqual(keep.dtype, np.uint8)
        self.assertEqual(int((keep == 0).sum()), 1)
        self.assertEqual(int((gm.keep_mask(probability, 0.7, 0) == 0).sum()), 0)
        self.assertEqual(int((gm.keep_mask(probability, 0.5, 1) == 0).sum()), 5)
        self.assertEqual(gm.dilation_radius(300, 400, 0.01), 5)


@unittest.skipIf(Image is None, "Pillow requis")
class RunTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.images = root / "images"
        (self.images / "sub").mkdir(parents=True)
        self.model = root / "model"
        self.model.mkdir()
        (self.model / "config.json").write_text("{}")
        self.output = root / "masks"
        Image.new("RGB", (8, 16)).save(self.images / "a.jpg")
        Image.new("RGB", (8, 16)).save(self.images / "sub" / "b.png")
        (self.images / "notes.txt").write_text("x")

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, **overrides):
        values = dict(images=self.images, output=self.output, model=self.model,
                      labels="sky", confidence=0.5, dilate_fraction=0.0,
                      max_masked_fraction=0.8, device="cpu")
        values.update(overrides)
        return Namespace(**values)

    def test_writes_spirula_masks_and_resumes(self):
        segmenter = FakeSegmenter()
        summary = gm.run(self.args(), segmenter)
        self.assertEqual(summary["images"], 2)
        mask = np.asarray(Image.open(self.output / "a.jpg.png"))
        self.assertEqual(mask.shape, (16, 8))
        self.assertTrue((mask[:4] == 0).all() and (mask[4:] == 255).all())
        self.assertTrue((self.output / "sub" / "b.png.png").is_file())
        report = json.loads((self.output / "masks.json").read_text())
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["settings"]["label_names"], ["sky"])

        summary = gm.run(self.args(), segmenter)
        self.assertEqual((summary["generated"], summary["reused"]), (0, 2))
        self.assertEqual(segmenter.calls, 2)

    def test_changed_labels_rebuild_everything(self):
        gm.run(self.args(), FakeSegmenter())
        stale = self.output / "stale.png"
        stale.write_bytes(b"x")
        summary = gm.run(self.args(labels="sky,car"), FakeSegmenter())
        self.assertEqual(summary["generated"], 2)
        self.assertFalse(stale.exists())
        mask = np.asarray(Image.open(self.output / "a.jpg.png"))
        self.assertTrue((mask[-2:] == 0).all())
        self.assertEqual(list(self.output.parent.glob(".masks.tmp-*")), [])

    def test_unknown_label_fails_before_writing(self):
        with self.assertRaisesRegex(ValueError, "Labels absents"):
            gm.run(self.args(labels="cloud"), FakeSegmenter())
        self.assertFalse(self.output.exists())

    def test_overmasked_image_is_kept_whole(self):
        summary = gm.run(self.args(max_masked_fraction=0.1), FakeSegmenter())
        self.assertEqual(summary["overmasked_kept"], 2)
        self.assertTrue((np.asarray(Image.open(self.output / "a.jpg.png")) == 255).all())

    def test_exif_rotation_mask_returns_to_raw_frame(self):
        for path in self.images.rglob("*.*"):
            path.unlink()
        image = Image.new("RGB", (16, 8))
        exif = image.getexif()
        exif[0x0112] = 6
        image.save(self.images / "r.jpg", exif=exif)
        summary = gm.run(self.args(), FakeSegmenter())
        self.assertEqual(summary["exif_rotated"], 1)
        mask = np.asarray(Image.open(self.output / "r.jpg.png"))
        self.assertEqual(mask.shape, (8, 16))
        # Orientation 6 turns the raw image clockwise: its left columns become the top.
        self.assertTrue((mask[:, :4] == 0).all() and (mask[:, 4:] == 255).all())

    def test_refuses_unknown_existing_output(self):
        self.output.mkdir()
        (self.output / "x.png").write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "sans masks.json"):
            gm.run(self.args(), FakeSegmenter())


if __name__ == "__main__":
    unittest.main()
