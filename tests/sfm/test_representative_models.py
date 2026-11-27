import struct
import tempfile
import unittest
from pathlib import Path

from tools.sfm import model_workspaces as mw
from tools.sfm import representative_models as rm


def write_model(root: Path, index: int, registered: int) -> None:
    model = root / str(index)
    model.mkdir(parents=True)
    (model / "images.bin").write_bytes(struct.pack("<Q", registered) + b"\0" * 16)


class RepresentativeModelsTest(unittest.TestCase):
    def test_tile_case_keeps_both_models(self):
        # Cas réel tile_000_001 : 440 et 541 images sur 972.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_model(root, 0, 440)
            write_model(root, 1, 541)
            manifest = rm.select_models(root, 972, 0.05, 30)
        self.assertEqual(manifest["selected"], [0, 1])

    def test_small_or_fragment_models_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_model(root, 0, 900)
            write_model(root, 1, 20)   # sous le minimum absolu
            write_model(root, 2, 40)   # > 30 mais 4 % de 1000
            manifest = rm.select_models(root, 1000, 0.05, 30)
        self.assertEqual(manifest["selected"], [0])
        self.assertFalse(next(m for m in manifest["models"] if m["index"] == 2)["eligible"])

    def test_ignores_non_model_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_model(root, 0, 100)
            (root / "notes").mkdir()
            manifest = rm.select_models(root, 100, 0.05, 30)
        self.assertEqual([m["index"] for m in manifest["models"]], [0])


    def test_workspace_links_model_as_sparse_zero_and_redirects_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "out"
            write_model(parent / "sfm" / "sparse", 1, 541)
            (parent / "images").mkdir()
            config = Path(tmp) / "config.sh"
            config.write_text('OUTPUT_DIR="/x"\n')
            config_copy = mw.create_workspace(parent, config, 1)
            model_link = parent / "models" / "1" / "sfm" / "sparse" / "0"
            self.assertTrue(model_link.is_symlink())
            self.assertTrue((model_link / "images.bin").is_file())
            self.assertTrue((parent / "models" / "1" / "images").exists())
            self.assertTrue(config_copy.read_text().rstrip().endswith(f'OUTPUT_DIR="{parent / "models" / "1"}"'))

    def test_largest_only_keeps_model_with_most_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_model(root, 0, 440)
            write_model(root, 1, 541)
            manifest = rm.select_models(root, 972, 0.05, 30)
        largest = max((m for m in manifest["models"] if m["eligible"]), key=lambda m: m["images"])
        self.assertEqual(largest["index"], 1)


if __name__ == "__main__":
    unittest.main()
