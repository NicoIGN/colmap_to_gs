import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from colab_runtime import restore_spirula, run_live, save_snapshot


class ColabRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_live_output_before_exit_and_progress_without_newline(self):
        finished = self.root / "finished"
        received = []
        class Console(io.StringIO):
            def write(console, text):
                if "progress\r" in text:
                    received.append(not finished.exists())
                return super().write(text)
        console = Console()
        code = ("import sys,time; from pathlib import Path; "
                "sys.stdout.write('progress\\r'); sys.stdout.flush(); time.sleep(.2); "
                f"Path({str(finished)!r}).touch(); print('done')")
        with contextlib.redirect_stdout(console):
            log = run_live([sys.executable, "-u", "-c", code], self.root, "stream")
        self.assertEqual(received, [True])
        self.assertIn(b"progress\r", log.read_bytes())
        self.assertIn("done", console.getvalue())

    def test_failure_retains_log_and_each_run_has_distinct_log(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError) as error:
                run_live([sys.executable, "-c", "print('failure'); exit(3)"], self.root, "fail")
            first = run_live([sys.executable, "-c", "print('one')"], self.root, "same")
            second = run_live([sys.executable, "-c", "print('two')"], self.root, "same")
        self.assertEqual(error.exception.returncode, 3)
        self.assertNotEqual(first, second)
        self.assertIn("failure", next(self.root.glob("*fail.log")).read_text())

    def test_utf8_split_across_chunks_and_keyboard_interrupt(self):
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            run_live([sys.executable, "-c",
                      "import os,time; os.write(1,b'\\xc3'); time.sleep(.03); os.write(1,b'\\xa9')"],
                     self.root, "utf8")
        self.assertTrue(console.getvalue().endswith("é"))
        class Interrupt(io.StringIO):
            def write(stream, text):
                if text == "ready\r":
                    raise KeyboardInterrupt()
                return super().write(text)
        with contextlib.redirect_stdout(Interrupt()), self.assertRaises(KeyboardInterrupt):
            run_live([sys.executable, "-u", "-c",
                      "import time; print('ready', end='\\r', flush=True); time.sleep(60)"],
                     self.root, "interrupt")
        self.assertIn(b"ready\r", next(self.root.glob("*interrupt.log")).read_bytes())

    def test_restore_is_reused_and_does_not_overwrite_other_installations(self):
        archive = self.root / "spirula.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("studio/build_vulkan/spirula", "binary")
            zipped.writestr("studio/models/sam3.ggml", "model")
        assets = self.root / "assets"
        root, binary = restore_spirula(archive, assets, "build_vulkan/spirula")
        self.assertEqual(binary.read_text(), "binary")
        self.assertTrue(binary.stat().st_mode & 0o111)
        self.assertEqual((root / "models/sam3.ggml").read_text(), "model")
        self.assertEqual(restore_spirula(archive, assets, "build_vulkan/spirula"), (root, binary))

    def test_unsafe_or_ambiguous_zip_is_refused(self):
        for entries in (["../escaped"], ["a/build/spirula", "b/build/spirula"]):
            archive = self.root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                for entry in entries:
                    zipped.writestr(entry, "data")
            with self.assertRaises(ValueError):
                restore_spirula(archive, self.root / "assets", "build/spirula")
        self.assertFalse((self.root / "escaped").exists())

    def test_snapshot_materializes_internal_images_and_keeps_previous_backups(self):
        run = self.root / "run"
        (run / "frames").mkdir(parents=True)
        (run / "output/images").mkdir(parents=True)
        (run / "frames/a.jpg").write_bytes(b"image")
        (run / "output/images/a.jpg").symlink_to(run / "frames/a.jpg")
        with contextlib.redirect_stdout(io.StringIO()):
            first = save_snapshot(run, self.root / "drive")
            second = save_snapshot(run, self.root / "drive")
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())
        with tarfile.open(first) as archive:
            member = archive.getmember("run/output/images/a.jpg")
            self.assertTrue(member.isfile())
            self.assertEqual(archive.extractfile(member).read(), b"image")
        self.assertFalse(list((self.root / "drive").glob("*.partial")))
        (run / "outside").symlink_to(self.root / "drive")
        with self.assertRaisesRegex(ValueError, "hors du run"):
            save_snapshot(run, self.root / "drive")

    def test_notebook_code_compiles_and_has_no_saved_outputs(self):
        notebook = json.loads(Path(__file__).with_name("colmap_to_gs_video_T4.ipynb").read_text())
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), cell["metadata"]["id"], "exec")
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
        environment = next(c for c in notebook["cells"] if c["metadata"]["id"] == "environment")
        self.assertIn("--no-capture-output", "".join(environment["source"]))
        config = next(c for c in notebook["cells"] if c["metadata"]["id"] == "config")
        self.assertIn("'OUTPUT_DIR': str(OUTPUT)", "".join(config["source"]))

    def test_notebook_config_writes_only_local_output_and_requires_license(self):
        notebook = json.loads(Path(__file__).with_name("colmap_to_gs_video_T4.ipynb").read_text())
        source = "".join(next(c for c in notebook["cells"]
                              if c["metadata"]["id"] == "config")["source"])
        namespace = dict(ACCEPT_SAM_LICENSE=False)
        with self.assertRaisesRegex(RuntimeError, "licence"):
            exec(source, namespace)
        namespace.update(ACCEPT_SAM_LICENSE=True, RUN=self.root,
                         OUTPUT=self.root / "output", FRAMES=self.root / "frames",
                         SPIRULA_ROOT=self.root / "spirula", SPIRULA_BIN=self.root / "spirula/bin",
                         SAM_MODEL=self.root / "sam.ggml", RUN_NAME="video", PHASE="all")
        with contextlib.redirect_stdout(io.StringIO()):
            exec(source, namespace)
        config = namespace["CONFIG"]
        result = subprocess.check_output(
            ["bash", "-c", 'source "$1"; printf "%s" "$OUTPUT_DIR"', "_", str(config)], text=True)
        self.assertEqual(result, str(self.root / "output"))
        self.assertNotIn("/content/drive", config.read_text())

    def test_vulkan_diagnostic_retains_failures_without_changing_environment(self):
        notebook = json.loads(Path(__file__).with_name("colmap_to_gs_video_T4.ipynb").read_text())
        cells = notebook["cells"]
        ids = [cell["metadata"]["id"] for cell in cells]
        self.assertLess(ids.index("system"), ids.index("vulkan-diagnostic"))
        self.assertLess(ids.index("vulkan-diagnostic"), ids.index("vulkan-check"))
        self.assertLess(ids.index("vulkan-check"), ids.index("run"))
        source = "".join(cells[ids.index("vulkan-diagnostic")]["source"])
        icd = self.root / "nvidia_icd.json"
        icd.write_text('{"ICD": {"library_path": "libGLX_nvidia.so.0"}}')
        calls = []

        def command_result(command, **kwargs):
            calls.append((command, kwargs))
            if command[0] == "vulkaninfo":
                return subprocess.CompletedProcess(command, 1, "VK_ERROR_INCOMPATIBLE_DRIVER\n")
            if command[0] == "ldconfig":
                return subprocess.CompletedProcess(command, 0, "")
            return subprocess.CompletedProcess(command, 0, "Tesla T4, 580.82.07\n")

        console = io.StringIO()
        with patch.dict(os.environ, VK_DRIVER_FILES=str(icd)), \
                patch("shutil.which", side_effect=lambda name: None if name == "dpkg-query" else name), \
                patch("subprocess.run", side_effect=command_result), \
                contextlib.redirect_stdout(console):
            before = os.environ.copy()
            exec(source, dict(LOGS=self.root, Path=Path, os=os, subprocess=subprocess))
            self.assertEqual(os.environ, before)
        text = console.getvalue()
        self.assertIn("Commande indisponible : dpkg-query", text)
        self.assertIn("VK_ERROR_INCOMPATIBLE_DRIVER", text)
        self.assertIn("Code de sortie : 1", text)
        report = next(self.root.glob("vulkan-diagnostic-*.log")).read_text()
        self.assertIn("libGLX_nvidia.so.0", report)
        self.assertIn("VK_ERROR_INCOMPATIBLE_DRIVER", report)
        self.assertEqual(calls[-1][0], ["vulkaninfo", "--summary"])
        self.assertEqual(calls[-1][1]["env"]["VK_DRIVER_FILES"], str(icd))
        self.assertEqual(calls[-1][1]["env"]["VK_LOADER_DEBUG"], "error,warn,driver")


if __name__ == "__main__":
    unittest.main()
