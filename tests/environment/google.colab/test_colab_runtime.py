import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

COLAB_DIR = Path(__file__).resolve().parents[3] / "environment/google.colab"
sys.path.insert(0, str(COLAB_DIR))
from colab_runtime import (
    configure_nvidia_vulkan, ensure_conda_environment, prepare_video_frames,
    restore_colmap, restore_spirula, run_live, save_colmap_zip, save_results_zip,
    save_snapshot, sha256,
)


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
        archive.unlink()
        self.assertEqual(restore_spirula(archive, assets, "build_vulkan/spirula", existing_root=root),
                         (root, binary))

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

    def test_colmap_zip_has_model_and_materialized_images_without_checkpoints(self):
        output = self.root / "run/output"
        model = output / "sfm/sparse/0"
        model.mkdir(parents=True)
        images = output / "images"
        images.mkdir()
        frame = self.root / "frame.jpg"
        frame.write_bytes(b"image")
        (images / "frame.jpg").symlink_to(frame)
        (output / "checkpoint").write_bytes(b"not included")
        drive = self.root / "drive"
        with self.assertRaisesRegex(ValueError, "incomplet"):
            save_colmap_zip(output, drive)
        for name in ("cameras", "images", "points3D"):
            (model / f"{name}.bin").write_bytes(b"model")
        with contextlib.redirect_stdout(io.StringIO()):
            first = save_colmap_zip(output, drive)
            second = save_colmap_zip(output, drive)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())
        with zipfile.ZipFile(first) as zipped:
            self.assertEqual(set(zipped.namelist()), {
                "sparse/0/cameras.bin", "sparse/0/images.bin",
                "sparse/0/points3D.bin", "images/frame.jpg",
            })
            self.assertEqual(zipped.read("images/frame.jpg"), b"image")
        self.assertEqual(list(drive.glob("*.partial")), [])
        with patch("colab_runtime.sha256", side_effect=["bad", "good"]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(OSError, "SHA256"):
                save_colmap_zip(output, drive)
        self.assertEqual(len(list(drive.glob("*.zip"))), 2)
        self.assertEqual(list(drive.glob("*.partial")), [])

    def test_results_zip_contains_only_final_splat_and_georeferencing(self):
        output = self.root / "run/output"
        (output / "georef").mkdir(parents=True)
        (output / "gs/block_000").mkdir(parents=True)
        (output / "scene.splat").write_bytes(bytes(64))
        (output / "gs/block_000/checkpoint").write_bytes(b"not included")
        (output / "gs/block_000/intermediate.splat").write_bytes(b"not included")
        (output / "georef/georef_summary.json").write_text("{}")
        (output / "georef/source.gpx").write_text("<gpx/>")
        (output / "scene.georef.json").write_text(json.dumps({
            "georef_summary": "georef/georef_summary.json",
            "source_gpx": "georef/source.gpx",
            "output_reference": {"type": "local_ENU"},
        }))
        drive = self.root / "drive"
        with contextlib.redirect_stdout(io.StringIO()):
            first = save_results_zip(output, drive, "scene.splat")
            second = save_results_zip(output, drive, "scene.splat")
        self.assertNotEqual(first, second)
        with zipfile.ZipFile(first) as zipped:
            self.assertEqual(set(zipped.namelist()), {
                "scene.splat", "scene.georef.json",
                "georef/georef_summary.json", "georef/source.gpx",
            })
            self.assertEqual(zipped.read("scene.splat"), bytes(64))
        with patch("colab_runtime.sha256", side_effect=["bad", "good"]):
            with self.assertRaisesRegex(OSError, "SHA256"):
                save_results_zip(output, drive, "scene.splat")
        self.assertEqual(len(list(drive.glob("*.zip"))), 2)
        self.assertEqual(list(drive.glob("*.partial")), [])
        (output / "georef/source.gpx").unlink()
        with self.assertRaisesRegex(ValueError, "incomplet"):
            save_results_zip(output, drive, "scene.splat")

    def test_results_zip_without_georeferencing_and_invalid_results(self):
        output = self.root / "run/output"
        output.mkdir(parents=True)
        drive = self.root / "drive"
        with self.assertRaisesRegex(ValueError, "incomplet"):
            save_results_zip(output, drive, "scene.splat")
        (output / "scene.splat").write_bytes(bytes(32))
        with contextlib.redirect_stdout(io.StringIO()):
            archive = save_results_zip(output, drive, "scene.splat")
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(zipped.namelist(), ["scene.splat"])
        with self.assertRaisesRegex(ValueError, "simple nom"):
            save_results_zip(output, drive, "../scene.splat")
        (output / "georef").mkdir()
        with self.assertRaisesRegex(ValueError, "Géoréférencement final absent"):
            save_results_zip(output, drive, "scene.splat")
        (output / "scene.georef.json").write_text(json.dumps({"source_gpx": "../../outside"}))
        with self.assertRaisesRegex(ValueError, "hors des résultats"):
            save_results_zip(output, drive, "scene.splat")

    def test_notebook_exports_colmap_before_training_and_only_final_results_afterwards(self):
        notebook = json.loads((COLAB_DIR / "colmap_to_gs_video_T4.ipynb").read_text())
        cells = {cell["metadata"]["id"]: "".join(cell["source"]) for cell in notebook["cells"]}
        for phase, failure in (("prepare", False), ("all", False), ("all", True)):
            with self.subTest(phase=phase, failure=failure):
                events = []
                def run(command, *args, **kwargs):
                    events.append(command[-1])
                    if command[-1] == "all" and failure:
                        raise subprocess.CalledProcessError(1, command)
                def save_colmap(*args):
                    events.append("colmap")
                    path = self.root / "colmap.zip"
                    path.touch()
                    return path
                def save_results(*args):
                    events.append("results")
                    path = self.root / "results.zip"
                    path.touch()
                    return path
                namespace = dict(
                    COLMAP_ARCHIVE="input.zip", CONFIG=self.root / "config", PHASE=phase,
                    PROJECT=self.root, RUN=self.root, OUTPUT=self.root / "output",
                    DRIVE_BACKUPS=self.root / "drive", SAVE_ON_SUCCESS=True,
                    SETTINGS={"EXPORT_SPLAT_NAME": "scene.splat"},
                    in_env=lambda command: command, run=run,
                    save_colmap_zip=save_colmap, save_results_zip=save_results,
                )
                with contextlib.redirect_stdout(io.StringIO()):
                    if failure:
                        with self.assertRaises(subprocess.CalledProcessError):
                            exec(cells["run"], namespace)
                    else:
                        exec(cells["run"], namespace)
                        exec(cells["save"], namespace)
                        exec(cells["save-colmap"], namespace)
                expected = ["prepare", "colmap"]
                if phase == "all":
                    expected.append("all")
                    if not failure:
                        expected.append("results")
                self.assertEqual(events, expected)

    def test_notebook_code_compiles_and_has_no_saved_outputs(self):
        notebook = json.loads((COLAB_DIR / "colmap_to_gs_video_T4.ipynb").read_text())
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), cell["metadata"]["id"], "exec")
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
        environment = next(c for c in notebook["cells"] if c["metadata"]["id"] == "environment")
        tree = ast.parse("".join(environment["source"]))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "in_env")
        namespace = dict(MAMBA=Path("/content/bin/micromamba"), MAMBA_ENV="gsplat")
        exec(compile(ast.Module(body=[function], type_ignores=[]), "in_env", "exec"), namespace)
        self.assertEqual(namespace["in_env"](["python", "-u", "-c", "print('live')"]),
                         ["/content/bin/micromamba", "run", "-n", "gsplat",
                          "python", "-u", "-c", "print('live')"])
        config = next(c for c in notebook["cells"] if c["metadata"]["id"] == "config")
        self.assertIn("'OUTPUT_DIR': str(OUTPUT)", "".join(config["source"]))

    def test_notebook_config_writes_only_local_output_and_applies_masks_switch(self):
        notebook = json.loads((COLAB_DIR / "colmap_to_gs_video_T4.ipynb").read_text())
        cells = {cell["metadata"]["id"]: "".join(cell["source"]) for cell in notebook["cells"]}
        namespace = {}
        with patch.object(Path, "mkdir"), contextlib.redirect_stdout(io.StringIO()):
            exec(cells["parameters"], namespace)
        namespace.update(RUN=self.root, OUTPUT=self.root / "output", FRAMES=self.root / "frames",
                         SOURCE_IMAGES=self.root / "frames", SPIRULA_ROOT=self.root / "spirula",
                         SPIRULA_BIN=self.root / "spirula/bin", SAM_MODEL=self.root / "sam.ggml")
        with self.assertRaisesRegex(RuntimeError, "licence"):
            exec(cells["config"], dict(namespace, ACCEPT_SAM_LICENSE=False))
        for masks in ("on", "off"):
            with self.subTest(masks=masks), contextlib.redirect_stdout(io.StringIO()):
                settings = dict(namespace, MASKS=masks, NUM_CPUS=3)
                exec(cells["config"], settings)
                config = settings["CONFIG"]
                values = subprocess.check_output(
                    ["bash", "-c", 'source "$1"; printf "%s|%s|%s|%s|%s" "$OUTPUT_DIR" "$SFM_MASKS" '
                     '"$TRAIN_MASKS" "$NUM_CPUS" "$SFM_CAMERA_SPLIT"', "_", str(config)],
                    text=True).split("|")
                self.assertEqual(values, [str(self.root / "output"), masks, masks, "3", "false"])
                self.assertNotIn("/content/drive", config.read_text())

    def test_conda_reuses_matching_env_and_updates_unmarked_without_erasing(self):
        prefix = self.root / "env"
        yaml = self.root / "env.yml"
        yaml.write_text("dependencies: [python]\n")
        def create_env(*args):
            (prefix / "conda-meta").mkdir(parents=True, exist_ok=True)
            (prefix / "conda-meta/history").touch()
        with patch("colab_runtime.run_live", side_effect=create_env) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            ensure_conda_environment("micromamba", "gsplat", prefix, yaml, self.root)
            self.assertEqual(run.call_args.args[0][1], "create")
            ensure_conda_environment("micromamba", "gsplat", prefix, yaml, self.root)
            self.assertEqual(run.call_count, 1)
            (prefix / ".colab-yaml.sha256").unlink()
            keep = prefix / "keep"
            keep.touch()
            ensure_conda_environment("micromamba", "gsplat", prefix, yaml, self.root)
            self.assertEqual(run.call_args.args[0][1:3], ["env", "update"])
            self.assertTrue(keep.exists())
            yaml.write_text("dependencies: [python, numpy]\n")
            run.side_effect = subprocess.CalledProcessError(1, ["micromamba"])
            with self.assertRaises(subprocess.CalledProcessError):
                ensure_conda_environment("micromamba", "gsplat", prefix, yaml, self.root)
            self.assertNotEqual((prefix / ".colab-yaml.sha256").read_text().strip(), sha256(yaml))

    def test_video_frames_are_reused_and_changed_inputs_rejected(self):
        video = self.root / "video.mov"
        video.write_bytes(b"video")
        frames = self.root / "frames"
        def extract(command, *_):
            folder = Path(command[command.index("--out") + 1])
            (folder / "frame.jpg").write_bytes(b"frame")
        with patch("colab_runtime.run_live", side_effect=extract) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            prepare_video_frames(video, frames, 1, ["extract"], self.root)
            stamp = (frames / "frame.jpg").stat().st_mtime_ns
            prepare_video_frames(video, frames, 1, ["extract"], self.root)
            self.assertEqual(run.call_count, 1)
            self.assertEqual((frames / "frame.jpg").stat().st_mtime_ns, stamp)
            with self.assertRaisesRegex(ValueError, "modifiés"):
                prepare_video_frames(video, frames, 2, ["extract"], self.root)
            (self.root / ".video-frames.json").unlink()
            (self.root / "output").mkdir()
            (self.root / "output/inputs.json").write_text(json.dumps({
                "source": str(frames.resolve()),
                "images": [["frame.jpg", str(frames / "frame.jpg"), 5, sha256(frames / "frame.jpg")]],
            }))
            prepare_video_frames(video, frames, 1, ["extract"], self.root)
            self.assertEqual(run.call_count, 2)
            prepare_video_frames(video, frames, 1, ["extract"], self.root)
            self.assertEqual(run.call_count, 2)

    def test_interrupted_frame_extraction_does_not_leave_partial_frames(self):
        video = self.root / "video.mov"
        video.write_bytes(b"video")
        frames = self.root / "frames"
        def fail(command, *_):
            folder = Path(command[command.index("--out") + 1])
            (folder / "partial.jpg").write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, command)
        with patch("colab_runtime.run_live", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare_video_frames(video, frames, 1, ["extract"], self.root)
        self.assertEqual(list(frames.iterdir()), [])
        self.assertFalse((self.root / ".video-frames.json").exists())

    def colmap_archive(self, name="colmap.zip", image_name="frame.jpg", include_image=True):
        archive = self.root / name
        model = {
            "cameras": struct.pack("<QIiQQ3d", 1, 1, 0, 2, 2, 2, 1, 1),
            "images": (struct.pack("<QI7dI", 1, 1, 1, 0, 0, 0, 0, 0, 0, 1)
                       + image_name.encode() + b"\0" + struct.pack("<Qddq", 1, .5, .5, 1)),
            "points3D": struct.pack("<QQdddBBBdQII", 1, 1, 0, 0, 10, 120, 130, 140, .1, 1, 1, 0),
        }
        with zipfile.ZipFile(archive, "w") as zipped:
            for key, data in model.items():
                zipped.writestr(f"dataset/sparse/0/{key}.bin", data)
            if include_image:
                # Tiny valid PPM, so the real selection pipeline can read it.
                zipped.writestr("dataset/images/frame.jpg", b"P6\n2 2\n255\n" + b"\x80" * 12)
            else:
                zipped.writestr("dataset/images/", b"")
        return archive

    def test_colmap_restore_validates_reuses_and_rejects_replaced_archive(self):
        archive = self.colmap_archive()
        run = self.root / "run"
        validator = [sys.executable, str(Path(__file__).resolve().parents[3]
                                       / "tools/sfm/resume.py")]
        with contextlib.redirect_stdout(io.StringIO()):
            images = restore_colmap(archive, run, validator, self.root)
            model = run / "output/sfm/sparse/0"
            before = (model / "points3D.bin").stat().st_mtime_ns
            self.assertEqual(restore_colmap(archive, run, validator, self.root), images)
            self.assertEqual((model / "points3D.bin").stat().st_mtime_ns, before)
            self.assertTrue((images / "frame.jpg").is_file())
            archive.write_bytes(b"different")
            with self.assertRaisesRegex(ValueError, "ZIP COLMAP modifié"):
                restore_colmap(archive, run, validator, self.root)
            self.assertTrue((model / "points3D.bin").is_file())

    def test_imported_colmap_prepares_one_block_twice_without_running_spirula(self):
        project = Path(__file__).resolve().parents[3]
        archive = self.colmap_archive()
        run = self.root / "run"
        validator = [sys.executable, str(project / "tools/sfm/resume.py")]
        with contextlib.redirect_stdout(io.StringIO()):
            images = restore_colmap(archive, run, validator, self.root)
        # A native reconstruction would fail, proving that the existing model is used.
        spirula = self.root / "spirula"
        spirula.write_text("#!/bin/sh\nexit 91\n")
        spirula.chmod(0o755)
        config = run / "config.sh"
        import shlex
        settings = dict(
            SFM_IMAGES_DIR=str(images), OUTPUT_DIR=str(run / "output"),
            SPIRULA_BIN=str(spirula), PYTHON_BIN=sys.executable,
            TRAIN_MASKS_SPIRULA_MODEL=str(self.root / "unused.ggml"),
            SFM_MASKS="off", TRAIN_MASKS="off", PIPELINE_RESUME="true",
            SPLIT_NUM_BLOCKS="1",
        )
        config.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in settings.items()))
        command = ["bash", str(project / "scripts/images_to_3dgs.sh"), str(config),
                   "--phase", "prepare"]
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with contextlib.redirect_stdout(io.StringIO()):
                restore_colmap(archive, run, validator, self.root)
        self.assertTrue((run / "output/blocks/block_000/cameras.bin").is_file())
        self.assertTrue((run / "output/.prepare_done").is_file())
        with contextlib.redirect_stdout(io.StringIO()):
            exported = save_colmap_zip(run / "output", self.root / "drive")
            restored = restore_colmap(exported, self.root / "roundtrip", validator, self.root)
            snapshot = save_snapshot(run, self.root / "drive")
        self.assertEqual((restored / "frame.jpg").read_bytes(), (images / "frame.jpg").read_bytes())
        self.assertTrue(snapshot.is_file())
        notebook = json.loads((COLAB_DIR / "colmap_to_gs_video_T4.ipynb").read_text())
        source = "".join(next(cell for cell in notebook["cells"]
                              if cell["metadata"]["id"] == "run")["source"])
        commands = []
        with contextlib.redirect_stdout(io.StringIO()):
            exec(source, dict(COLMAP_ARCHIVE=str(archive), CONFIG=config, PHASE="all",
                              PROJECT=project, RUN=run, SAVE_ON_SUCCESS=False,
                              in_env=lambda command: command, run=lambda command, *args, **kw: commands.append(command)))
        self.assertEqual(commands, [
            ["bash", "scripts/images_to_3dgs.sh", str(config), "--phase", "prepare"],
            ["bash", "scripts/images_to_3dgs.sh", str(config), "--phase", "all"],
        ])

    def test_remove_locks_requires_resume_before_creating_workspace(self):
        notebook = json.loads((COLAB_DIR / "colmap_to_gs_video_T4.ipynb").read_text())
        source = "".join(next(cell for cell in notebook["cells"]
                              if cell["metadata"]["id"] == "parameters")["source"])
        source = source.replace("PIPELINE_RESUME = 'true'", "PIPELINE_RESUME = 'false'")
        source = source.replace("REMOVE_LOCKS = 'false'", "REMOVE_LOCKS = 'true'")
        with patch.object(Path, "mkdir") as mkdir:
            with self.assertRaisesRegex(ValueError, "exige PIPELINE_RESUME=true"):
                exec(source, {})
            mkdir.assert_not_called()

    def test_parameters_reject_unknown_masks_value(self):
        notebook = json.loads((COLAB_DIR / "colmap_to_gs_video_T4.ipynb").read_text())
        source = "".join(next(cell for cell in notebook["cells"]
                              if cell["metadata"]["id"] == "parameters")["source"])
        with patch.object(Path, "mkdir"):
            with self.assertRaisesRegex(ValueError, "MASKS doit valoir on ou off"):
                exec(source.replace("MASKS = 'off'", "MASKS = 'maybe'"), {})

    def test_colmap_restore_rejects_missing_images_unsafe_paths_and_existing_workspace(self):
        validator = [sys.executable, str(Path(__file__).resolve().parents[3]
                                       / "tools/sfm/resume.py")]
        with contextlib.redirect_stdout(io.StringIO()):
            for index, name in enumerate(("missing.jpg", "../../outside.jpg")):
                archive = self.colmap_archive(f"missing-{index}.zip", name)
                run = self.root / f"bad-run-{index}"
                with self.assertRaises(subprocess.CalledProcessError):
                    restore_colmap(archive, run, validator, self.root)
                self.assertFalse((run / "output").exists())
            archive = self.colmap_archive()
            with zipfile.ZipFile(archive, "a") as zipped:
                zipped.writestr("../escaped", "no")
            with self.assertRaisesRegex(ValueError, "non sûre"):
                restore_colmap(archive, self.root / "unsafe", validator, self.root)
            archive = self.colmap_archive("fresh.zip")
            output = self.root / "existing/output"
            (output / "sfm").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "déjà utilisé"):
                restore_colmap(archive, output.parent, validator, self.root)
        self.assertFalse((self.root / "escaped").exists())

    def test_nvidia_configuration_exports_both_manifests_only_after_validation(self):
        libraries = self.root / "libraries"
        libraries.mkdir()
        for name in ("libGLX_nvidia.so.0", "libEGL_nvidia.so.0"):
            (libraries / name).touch()
        log = self.root / "vulkan.log"
        keys = ("VK_DRIVER_FILES", "VK_ICD_FILENAMES", "__EGL_VENDOR_LIBRARY_FILENAMES")
        with patch.dict(os.environ), patch("colab_runtime.run_live", return_value=log) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            before = os.environ.copy()
            log.write_text("deviceType = DISCRETE_GPU\nNVIDIA\n")
            configure_nvidia_vulkan(libraries, self.root, self.root)
            settings = run.call_args.kwargs["env"]
            self.assertEqual(settings["VK_DRIVER_FILES"], settings["VK_ICD_FILENAMES"])
            for key, name in ((keys[0], "libGLX_nvidia.so.0"), (keys[2], "libEGL_nvidia.so.0")):
                manifest = json.loads(Path(settings[key]).read_text())
                self.assertEqual(manifest["ICD"]["library_path"], str(libraries / name))
                self.assertEqual(os.environ[key], settings[key])
            os.environ.clear()
            os.environ.update(before)
            log.write_text("deviceType = CPU\nllvmpipe\n")
            with self.assertRaisesRegex(RuntimeError, "non confirmé"):
                configure_nvidia_vulkan(libraries, self.root, self.root)
            self.assertEqual(os.environ, before)
            run.side_effect = subprocess.CalledProcessError(1, ["vulkaninfo"])
            with self.assertRaises(subprocess.CalledProcessError):
                configure_nvidia_vulkan(libraries, self.root, self.root)
            self.assertEqual(os.environ, before)
        with self.assertRaises(FileNotFoundError):
            configure_nvidia_vulkan(self.root / "missing", self.root, self.root)

    def test_vulkan_diagnostic_retains_failures_without_changing_environment(self):
        notebook = json.loads((COLAB_DIR / "colmap_to_gs_video_T4.ipynb").read_text())
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
