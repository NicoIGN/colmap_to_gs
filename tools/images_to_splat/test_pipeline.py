import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent


class ImagesPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.images = self.root / "source images"
        for name in ("root.jpg", "h00/photo.jpg", "h01/photo.jpg"):
            path = self.images / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"jpeg")
        (self.images / "notes.txt").write_text("not an image")
        self.output = self.root / "output with spaces"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.events = self.root / "events.jsonl"
        self.jobs = self.root / "jobs.json"
        self.model = self.root / "sam3.ggml"
        self.model.write_bytes(b"model")
        self.config = self.root / "config with spaces.sh"
        self.config.write_text(
            f'SFM_IMAGES_DIR="{self.images}"\nOUTPUT_DIR="{self.output}"\n'
            f'SPIRULA_BIN="{self.bin / "spirula"}"\nPYTHON_BIN="{self.bin / "python"}"\n'
            f'TRAIN_MASKS_SPIRULA_MODEL="{self.model}"\nMASKS_ACCEPT_LICENSE=true\n'
            'SLURM_BLOCK_PARALLELISM=2\n'
        )
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        TEST_EVENTS=str(self.events), TEST_JOBS=str(self.jobs),
                        SLURM_TMPDIR=str(self.root))
        for key in ("SLURM_JOB_ID", "SLURM_ARRAY_TASK_ID", "SLURM_ARRAY_JOB_ID"):
            self.env.pop(key, None)
        self.tool("python", f"#!{sys.executable}\n" + r'''
import json, os
from pathlib import Path
import shutil, sys
args = sys.argv[1:]
name = Path(args[0]).name if args else ""
known = {"generate_masks.py", "colmap_split_blocks.py", "georef_colmap_from_gpx.py", "export_ply.py",
         "filter_splats.py", "merge_splats.py"}
def get(flag): return args[args.index(flag) + 1]
if name not in known:
    os.execv(sys.executable, [sys.executable, *args])
with open(os.environ["TEST_EVENTS"], "a") as log:
    log.write(json.dumps([name, *args[1:]]) + "\n")
if os.environ.get("TEST_FAIL_TOOL") == name:
    sys.exit(7)
if name == "generate_masks.py":
    source, output = Path(get("--images")), Path(get("--output"))
    prefix = "SFM_MASKS" if output.name == "feature_masks" else "TRAIN_MASKS"
    expected_labels = ",".join(sorted(os.environ[prefix + "_LABELS"].split(",")))
    assert get("--labels") == expected_labels
    assert get("--backend") == os.environ[prefix + "_BACKEND"]
    assert float(get("--confidence")) == float(os.environ[prefix + "_CONFIDENCE"])
    assert float(get("--max-masked-fraction")) == float(os.environ[prefix + "_MAX_FRACTION"])
    if get("--backend") == "spirula":
        assert "--accept-spirula-license" in args
        assert get("--model") == os.environ[prefix + "_SPIRULA_MODEL"]
        assert get("--spirula-device") == os.environ[prefix + "_SPIRULA_DEVICE"]
    else:
        assert "--accept-spirula-license" not in args
        assert get("--model") == os.environ[prefix + "_MODEL"]
        assert get("--device") == os.environ[prefix + "_DEVICE"]
    names = sorted(p.relative_to(source).as_posix() for p in source.rglob("*.jpg"))
    output.mkdir(parents=True, exist_ok=True)
    for relative in names:
        target = output / (relative + ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"mask")
    (output / "masks.json").write_text(json.dumps({
        "status": "complete", "settings": {"labels": get("--labels")}, "images": names}))
elif name == "georef_colmap_from_gpx.py":
    output = Path(get("--output-dir"))
    assert get("--sparse-dir").endswith("/sfm/sparse")
    assert get("--view-prefix") == "h00"
    assert get("--epsg") == "2154"
    source = Path(get("--sparse-dir")) / "0"
    model = output / "0"
    model.mkdir(parents=True)
    for file in ("cameras.bin", "images.bin", "points3D.bin"):
        shutil.copyfile(source / file, model / file)
    (output / "georef_summary.json").write_text(json.dumps({"models": [{
        "model": "0", "status": "exported_requires_validation"}]}))
    (model / "georef_stats.json").write_text(json.dumps({
        "output_reference": {"type": "local_ENU", "units": "meters",
            "axes": ["East", "North", "Up"], "origin_lat_lon_height": [48.87, 2.34, 50.0]},
        "ambiguous": os.environ.get("TEST_GEOREF_AMBIGUOUS") == "1",
        "normalized_ordered_rmse": float(os.environ.get("TEST_GEOREF_ERROR", "0.01")),
        "warnings": ["Solution à contrôler"]}))
elif name == "colmap_split_blocks.py":
    expected = "/georef/0" if os.environ.get("GEOREF_GPX_FILE") else "/sfm/sparse/0"
    assert get("--input").endswith(expected)
    output = Path(get("--output"))
    count = int(get("--num-blocks"))
    for i in range(count):
        block = output / f"block_{i:03d}"
        block.mkdir(parents=True)
        for file in ("cameras.bin", "images.bin", "points3D.bin"):
            (block / file).write_bytes(b"colmap")
    (output / "manifest.json").write_text(json.dumps({"blocks": list(range(count))}))
elif name == "export_ply.py":
    assert get("--filter-mode") == "off"
    Path(get("--out-ply")).write_bytes(b"raw-splat")
elif name == "filter_splats.py":
    assert get("--mode") == "off"
    expected = "/georef/0/points3D.bin" if os.environ.get("GEOREF_GPX_FILE") else "/sfm/sparse/0/points3D.bin"
    assert get("--sparse-points").endswith(expected)
    assert Path(get("--input")).read_bytes() == b"raw-splat"
    output = Path(get("--output"))
    output.write_bytes(b"cropped" if get("--sparse-bbox-mode") == "apply" else b"raw-splat")
    output.with_suffix(".filter.json").write_text("{}")
    output.with_suffix(".rejected.splat").write_bytes(b"rejected")
elif name == "merge_splats.py":
    paths = args[1:args.index("--output")]
    assert len(paths) == 4, paths
    mode = os.environ["POSTPROCESS_SPARSE_BBOX_MODE"]
    if mode != "off":
        assert all(p.endswith(".bbox.splat") for p in paths), paths
    else:
        assert all(".bbox." not in p for p in paths), paths
    expected = b"cropped" if mode == "apply" else b"raw-splat"
    assert all(Path(p).read_bytes() == expected for p in paths), paths
    Path(get("--output")).write_bytes(b"merged")
''')
        self.tool("spirula", f"#!{sys.executable}\n" + r'''
import json, os
from pathlib import Path
import sys
args = sys.argv[1:]
def get(flag): return args[args.index(flag) + 1]
with open(os.environ["TEST_EVENTS"], "a") as log:
    log.write(json.dumps(["spirula", *args]) + "\n")
if args[0] == "sfm":
    assert get("--data-type") == "individual"
    assert get("--camera-mode") == "folder"
    assert get("--metric-gps") == "none"
    assert "--sequence" not in args and "--sensor-pairs" not in args
    for flag, variable in (
        ("--max-error", "SFM_MAX_ERROR"), ("--min-inliers", "SFM_MIN_INLIERS"),
        ("--min-tri-angle", "SFM_MIN_TRI_ANGLE"), ("--ba-loss", "SFM_BA_LOSS"),
        ("--ba-loss-param", "SFM_BA_LOSS_PARAM"),
    ):
        assert get(flag) == os.environ[variable]
    assert get("--reuse-matches") == "redo"
    if os.environ["SFM_MASKS"] == "on":
        if os.environ.get("PIPELINE_RESUME") == "true":
            assert Path(get("--feature-masks"), "root.jpg.png").is_file()
        else:
            assert Path(get("--feature-masks"), "masks.json").is_file()
    else:
        assert "--feature-masks" not in args
    sparse = Path(get("--output")) / "sparse/0"
    sparse.mkdir(parents=True, exist_ok=True)
    import struct
    (sparse / "cameras.bin").write_bytes(
        struct.pack("<QIiQQ3d", 1, 1, 0, 2, 2, 2, 1, 1))
    (sparse / "images.bin").write_bytes(
        struct.pack("<QI7dI", 1, 1, 1, 0, 0, 0, 0, 0, 0, 1)
        + b"root.jpg\0" + struct.pack("<Qddq", 1, 0.5, 0.5, 1))
    (sparse / "points3D.bin").write_bytes(
        struct.pack("<QQdddBBBdQII", 1, 1, 0, 0, 10, 120, 130, 140, 0.1, 1, 1, 0))
    if os.environ.get("TEST_INVALID_SPARSE") == "1":
        (sparse / "points3D.bin").write_bytes(b"interrupted")
    elif os.environ.get("TEST_INVALID_SPARSE") == "tracks":
        path = sparse / "points3D.bin"
        path.write_bytes(path.read_bytes()[:-4] + struct.pack("<I", 7))
else:
    assert get("--cap-max") == "2000000"
    assert get("--num-iterations") == "20000"
    assert get("--quality") == "ultra"
    output = Path(get("--output-dir-prefix"))
    if os.environ["TRAIN_MASKS"] == "on":
        assert get("--apply-loss-for-mask") == "1"
        expected = "masks" if (Path(os.environ["OUTPUT_DIR"]) / "masks").is_dir() else "train_masks"
        assert (output / "masks").resolve() == Path(os.environ["OUTPUT_DIR"]) / expected
    else:
        assert get("--load-masks") == "0"
        assert "--mask-dir" not in args
    assert (output / "images").resolve() == Path(os.environ["OUTPUT_DIR"]) / "images"
    if os.environ.get("TEST_FAIL_BLOCK") == output.name:
        sys.exit(9)
    splats = output / "splats"
    ckpt = splats / "step-000020000.ckpt"
    ckpt.mkdir(parents=True)
    (ckpt / "splat.ply").write_bytes(b"ply")
    (splats / "scene_transform.json").write_text("{}")
''')
        self.tool("srun", "#!/usr/bin/env bash\nset -euo pipefail\n"
                  '[[ "$1" == --unbuffered && "$2" == --export=ALL ]]\nshift 2\nexec "$@"\n')
        self.tool("nvidia-smi", "#!/bin/sh\necho 'GPU test'\n")
        self.tool("scancel", '#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_JOBS.cancelled"\n')
        self.tool("sbatch", f"#!{sys.executable}\n" + r'''
import concurrent.futures, json, os
from pathlib import Path
import shutil, subprocess, sys
record = Path(os.environ["TEST_JOBS"])
jobs = json.loads(record.read_text()) if record.exists() else []
args = sys.argv[1:]
job = str(100 + len(jobs))
if os.environ.get("TEST_SUBMIT_FAIL_AT") == str(len(jobs) + 1):
    sys.exit(3)
jobs.append(args)
record.write_text(json.dumps(jobs))
print(job + ";cluster", flush=True)
def execute(index):
    args = jobs[index]
    flags = dict(arg[2:].split("=", 1) for arg in args if arg.startswith("--") and "=" in arg)
    if "dependency" in flags:
        status = execute(int(flags["dependency"].split(":")[1]) - 100)
        if status: return status
    def run(task=None):
        env = dict(os.environ, SLURM_JOB_ID=str(index + 100),
                   SLURM_CPUS_PER_TASK=flags["cpus-per-task"])
        if task is not None:
            env.update(SLURM_ARRAY_JOB_ID=str(index + 100), SLURM_ARRAY_TASK_ID=str(task))
        spool = record.parent / f"spooled-{index}-{task}.sh"
        shutil.copyfile(args[-5], spool)
        result = subprocess.run(["bash", str(spool), *args[-4:]], env=env, cwd=flags["chdir"],
                                capture_output=True, text=True)
        def path(value):
            return value.replace("%j", str(index + 100)).replace(
                "%A", str(index + 100)).replace("%a", str(task))
        Path(path(flags["output"])).write_text(result.stdout)
        Path(path(flags["error"])).write_text(result.stderr)
        return result.returncode
    if "array" in flags:
        span, limit = flags["array"].split("%")
        end = int(span.split("-")[1])
        with concurrent.futures.ThreadPoolExecutor(max_workers=int(limit)) as pool:
            return max(pool.map(run, range(end + 1)))
    return run()
if "--wait" in args:
    sys.exit(execute(len(jobs) - 1))
''')

    def tool(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def append(self, text):
        with self.config.open("a") as stream:
            stream.write(text + "\n")

    def run_pipeline(self, *args, slurm=False):
        return subprocess.run(
            ["bash", str(SOURCE / ("launch.sh" if slurm else "run_pipeline.sh")),
             str(self.config), *args],
            env=self.env, cwd=self.root, capture_output=True, text=True, timeout=40)

    def commands(self):
        return [json.loads(line) for line in self.events.read_text().splitlines()]

    def assert_ok(self, result):
        logs = ""
        if (self.output / "logs").exists():
            logs = "\n".join(p.read_text() for p in (self.output / "logs").glob("*.err"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + logs)

    def test_local_full_run_reuse_and_merge_only(self):
        self.assert_ok(self.run_pipeline())
        self.assertEqual((self.output / "scene.splat").read_bytes(), b"merged")
        self.assertFalse((self.output / "georef").exists())
        self.assertFalse((self.output / "scene.georef.json").exists())
        selected = self.output / "images"
        for name in ("root.jpg", "h00/photo.jpg", "h01/photo.jpg"):
            self.assertTrue((selected / name).is_symlink())
        commands = self.commands()
        self.assertEqual(sum(cmd[:2] == ["spirula", "sfm"] for cmd in commands), 1)
        self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 4)
        self.assertEqual([cmd[0] for cmd in commands][-5:],
                         ["filter_splats.py"] * 4 + ["merge_splats.py"])
        self.assert_ok(self.run_pipeline())
        commands = self.commands()
        self.assertEqual(sum(cmd[:2] == ["spirula", "train"] for cmd in commands), 4)
        before = len(commands)
        self.assert_ok(self.run_pipeline("--phase", "merge"))
        self.assertEqual([cmd[0] for cmd in self.commands()[before:]],
                         ["filter_splats.py"] * 4 + ["merge_splats.py"])
        self.assertEqual((self.output / "gs/block_000/splats/block_000.splat").read_bytes(),
                         b"raw-splat")

    def test_nonrecursive_and_input_override(self):
        self.append('SFM_IMAGES_RECURSIVE=false\nSFM_IMAGES_DIR="/does/not/exist"')
        self.assert_ok(self.run_pipeline("--images", str(self.images), "--phase", "prepare"))
        names = [p.relative_to(self.output / "images").as_posix()
                 for p in (self.output / "images").rglob("*.jpg")]
        self.assertEqual(names, ["root.jpg"])
        self.assertEqual(len(json.loads((self.output / "inputs.json").read_text())["images"]), 1)

    def test_changed_images_or_mask_policy_refuse_reuse(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        (self.images / "root.jpg").write_bytes(b"changed")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def interrupted_workspace(self):
        from PIL import Image

        for path in self.images.rglob("*.jpg"):
            Image.new("RGB", (2, 2)).save(path)
        self.output.mkdir()
        selected = self.output / "images"
        masks = self.output / "masks"
        for source in self.images.rglob("*.jpg"):
            relative = source.relative_to(self.images)
            target = selected / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)
            mask = masks / (relative.as_posix() + ".png")
            mask.parent.mkdir(parents=True, exist_ok=True)
            Image.new("L", (2, 2), 255).save(mask)
        sparse = self.output / "sfm/sparse/0"
        sparse.mkdir(parents=True)
        for name in ("cameras.bin", "images.bin", "points3D.bin"):
            (sparse / name).write_bytes(b"interrupted")
        return masks, sparse

    def test_partial_workspace_remains_refused_by_default(self):
        (self.output / "sfm").mkdir(parents=True)
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Workspace sans inputs.json", result.stdout + result.stderr)

    def test_verified_resume_reuses_masks_and_restarts_incomplete_sfm_slurm(self):
        self.interrupted_workspace()
        self.append("PIPELINE_RESUME=true")
        self.assert_ok(self.run_pipeline("--wait", slurm=True))
        commands = self.commands()
        self.assertFalse(any(cmd[0] == "generate_masks.py" for cmd in commands))
        self.assertEqual(sum(cmd[:2] == ["spirula", "sfm"] for cmd in commands), 1)
        self.assertTrue((self.output / "scene.splat").is_file())
        self.assertTrue((self.output / "inputs.json").is_file())
        adopted_masks = json.loads((self.output / "masks/masks.json").read_text())
        self.assertEqual(adopted_masks["settings"]["provenance"], "unknown")
        before = len(commands)
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.assertFalse(any(cmd[:2] == ["spirula", "sfm"]
                             for cmd in self.commands()[before:]))

    def test_resume_rejects_missing_or_invalid_masks_before_sfm(self):
        from PIL import Image

        masks, _ = self.interrupted_workspace()
        self.append("PIPELINE_RESUME=true")
        mask = masks / "root.jpg.png"
        for data in ("missing", "corrupt", "size", "nonbinary", "color"):
            with self.subTest(data=data):
                if data == "missing":
                    mask.unlink()
                elif data == "corrupt":
                    mask.write_bytes(b"invalid")
                elif data == "size":
                    Image.new("L", (3, 2), 255).save(mask)
                elif data == "nonbinary":
                    Image.new("L", (2, 2), 127).save(mask)
                else:
                    Image.new("RGB", (2, 2), (255, 255, 255)).save(mask)
                result = self.run_pipeline("--phase", "prepare")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.events.exists())

    def test_resume_does_not_override_known_image_or_policy_changes(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.append("PIPELINE_RESUME=true")
        self.append("SFM_MIN_TRI_ANGLE=4")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_resume_rejects_changed_images(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.append("PIPELINE_RESUME=true")
        (self.images / "root.jpg").write_bytes(b"changed")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_resume_checks_new_sparse_before_splitting(self):
        self.interrupted_workspace()
        self.append("PIPELINE_RESUME=true")
        for kind in ("1", "tracks"):
            with self.subTest(kind=kind):
                self.env["TEST_INVALID_SPARSE"] = kind
                result = self.run_pipeline("--phase", "prepare")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Sparse absent ou incomplet", result.stdout + result.stderr)
                self.assertFalse((self.output / ".prepare_done").exists())
                self.assertFalse((self.output / "blocks").exists())

    def test_resume_does_not_ignore_mask_policy_metadata(self):
        masks, _ = self.interrupted_workspace()
        (masks / "masks.json").write_text(json.dumps({
            "status": "complete", "settings": {"labels": ["sky"]},
        }))
        self.append("PIPELINE_RESUME=true")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Labels de masques modifiés", result.stdout + result.stderr)
        self.assertFalse(self.events.exists())

    def test_resume_rejects_extra_workspace_images(self):
        self.interrupted_workspace()
        (self.output / "images/extra.jpg").symlink_to(self.images / "root.jpg")
        self.append("PIPELINE_RESUME=true")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Images inattendues", result.stdout + result.stderr)
        self.assertFalse((self.output / "inputs.json").exists())

    def test_resume_migrates_additive_signature_before_downstream_steps(self):
        self.interrupted_workspace()
        self.append("PIPELINE_RESUME=true")
        # Obtain the current signature without allowing the mocked SfM to finish.
        self.env["TEST_FAIL_TOOL"] = "colmap_split_blocks.py"
        self.assertNotEqual(self.run_pipeline("--phase", "prepare").returncode, 0)
        self.env.pop("TEST_FAIL_TOOL")
        marker = self.output / "inputs.json"
        previous = json.loads(marker.read_text())
        previous["settings"].pop("SFM_MIN_TRI_ANGLE")
        marker.write_text(json.dumps(previous))
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.assertEqual(
            json.loads((self.output / "inputs.before_resume.json").read_text()), previous)
        self.assertIn("SFM_MIN_TRI_ANGLE", json.loads(marker.read_text())["settings"])

    def test_resume_refuses_unknown_provenance_after_downstream_steps(self):
        self.interrupted_workspace()
        (self.output / "blocks").mkdir()
        self.append("PIPELINE_RESUME=true")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("après recalage/découpage/entraînement", result.stdout + result.stderr)

    def test_invalid_resume_value_stops_before_submission(self):
        self.append("PIPELINE_RESUME=yes")
        result = self.run_pipeline(slurm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PIPELINE_RESUME", result.stderr)
        self.assertFalse(self.jobs.exists())

    def test_changed_sfm_refuses_even_merge_only(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.append("SFM_MAX_IMAGE_SIZE=2048")
        result = self.run_pipeline("--phase", "merge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_strict_geometry_defaults_and_overrides_local_and_slurm(self):
        variables = ("SFM_MAX_ERROR", "SFM_MIN_INLIERS", "SFM_MIN_TRI_ANGLE",
                     "SFM_BA_LOSS", "SFM_BA_LOSS_PARAM")
        flags = ("--max-error", "--min-inliers", "--min-tri-angle", "--ba-loss", "--ba-loss-param")
        for slurm, values in ((False, ("2", "30", "3", "huber", "2")),
                              (True, ("1.5", "50", "4", "cauchy", "1"))):
            with self.subTest(slurm=slurm):
                self.append(f'OUTPUT_DIR="{self.root / str(slurm)}"')
                if slurm:
                    self.append("\n".join(f'{key}="{value}"' for key, value in zip(variables, values)))
                before = len(self.commands()) if self.events.exists() else 0
                self.assert_ok(self.run_pipeline(*(["--wait"] if slurm else []), slurm=slurm))
                sfm = next(cmd for cmd in self.commands()[before:] if cmd[:2] == ["spirula", "sfm"])
                self.assertEqual(tuple(sfm[sfm.index(flag) + 1] for flag in flags), values)

    def test_geometry_change_refuses_reuse(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.append("SFM_MIN_TRI_ANGLE=4")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_invalid_geometry_stops_before_submission(self):
        self.append("SFM_MAX_ERROR=nan\nSFM_MIN_INLIERS=0\nSFM_MIN_TRI_ANGLE=91\n"
                    "SFM_BA_LOSS=none\nSFM_BA_LOSS_PARAM=-1")
        result = self.run_pipeline(slurm=True)
        self.assertNotEqual(result.returncode, 0)
        for name in ("SFM_MAX_ERROR", "SFM_MIN_INLIERS", "SFM_MIN_TRI_ANGLE",
                     "SFM_BA_LOSS", "SFM_BA_LOSS_PARAM"):
            self.assertIn(name, result.stderr)
        self.assertFalse(self.jobs.exists())

    def test_mask_policy_changes_refuse_workspace_reuse(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.append('TRAIN_MASKS_LABELS="sky,person"')
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_masks_can_be_disabled_or_used_for_one_stage(self):
        for sfm, train in (("off", "off"), ("on", "off"), ("off", "on")):
            with self.subTest(sfm=sfm, train=train):
                output = self.root / f"masks-{sfm}-{train}"
                self.append(f'SFM_MASKS="{sfm}"\nTRAIN_MASKS="{train}"\nOUTPUT_DIR="{output}"')
                self.assert_ok(self.run_pipeline())
                self.assertTrue((output / "scene.splat").is_file())
                self.assertEqual((output / "masks").exists(), sfm == train == "on")
                self.assertEqual((output / "feature_masks").exists(), sfm == "on" and train == "off")
                self.assertEqual((output / "train_masks").exists(), train == "on" and sfm == "off")

    def test_prefixed_policies_are_independent_local_and_slurm(self):
        self.append('SFM_MASKS_LABELS="sky"\nSFM_MASKS_SPIRULA_DEVICE="NVIDIA A40"\n'
                    'SFM_MASKS_CONFIDENCE=0.7\nSFM_MASKS_MAX_FRACTION=0.9\n'
                    'TRAIN_MASKS_LABELS="sky,cloud"')
        for slurm in (False, True):
            with self.subTest(slurm=slurm):
                output = self.root / f"independent-{slurm}"
                self.append(f'OUTPUT_DIR="{output}"')
                before = len(self.commands()) if self.events.exists() else 0
                self.assert_ok(self.run_pipeline(*(["--wait"] if slurm else []), slurm=slurm))
                commands = self.commands()[before:]
                generated = [cmd for cmd in commands if cmd[0] == "generate_masks.py"]
                self.assertEqual(len(generated), 2)
                self.assertTrue((output / "feature_masks/masks.json").exists())
                self.assertTrue((output / "train_masks/masks.json").exists())
                sfm = next(cmd for cmd in commands if cmd[:2] == ["spirula", "sfm"])
                self.assertEqual(sfm[sfm.index("--feature-masks") + 1], str(output / "feature_masks"))
                self.assertTrue((output / "scene.splat").is_file())

    def test_identical_prefixed_policies_share_one_generation(self):
        self.append('SFM_MASKS_SPIRULA_DEVICE=""\nSFM_MASKS_LABELS=""\n'
                    'TRAIN_MASKS_SPIRULA_DEVICE="NVIDIA A40"')
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        generated = [cmd for cmd in self.commands() if cmd[0] == "generate_masks.py"]
        self.assertEqual(len(generated), 1)
        self.assertTrue((self.output / "masks/masks.json").exists())

    def test_segmentation_backend_reuses_panoramax_variables(self):
        model = self.root / "transformers-model"
        model.mkdir()
        self.append(f'TRAIN_MASKS_BACKEND=segmentation\nTRAIN_MASKS_MODEL="{model}"\n'
                    f'TRAIN_MASKS_PYTHON="{self.bin / "python"}"\nTRAIN_MASKS_DEVICE=cpu')
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        generated = [cmd for cmd in self.commands() if cmd[0] == "generate_masks.py"]
        self.assertEqual(len(generated), 1)
        self.assertIn("segmentation", generated[0])

    def test_legacy_unprefixed_config_is_rejected_before_submission(self):
        self.append('MASKS_LABELS="sky"')
        result = self.run_pipeline(slurm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SFM_MASKS_LABELS / TRAIN_MASKS_LABELS", result.stderr)
        self.assertFalse(self.jobs.exists())

    def test_invalid_prefixed_policy_is_rejected_before_submission(self):
        self.append('SFM_MASKS_CONFIDENCE=1.5')
        result = self.run_pipeline(slurm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SFM_MASKS_CONFIDENCE", result.stderr)
        self.assertFalse(self.jobs.exists())

    def test_sfm_only_policy_does_not_require_a_training_model(self):
        self.append(f'TRAIN_MASKS=off\nTRAIN_MASKS_SPIRULA_MODEL=""\n'
                    f'SFM_MASKS_SPIRULA_MODEL="{self.model}"\n'
                    'SFM_MASKS_LABELS=sky\nSFM_MASKS_SPIRULA_DEVICE="NVIDIA A40"')
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        generated = [cmd for cmd in self.commands() if cmd[0] == "generate_masks.py"]
        self.assertEqual(len(generated), 1)
        self.assertTrue((self.output / "feature_masks/masks.json").exists())

    def test_training_policy_change_refuses_reuse_with_independent_sfm(self):
        self.append('SFM_MASKS_LABELS=sky')
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.append('TRAIN_MASKS_CONFIDENCE=0.8')
        result = self.run_pipeline("--phase", "merge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_missing_block_prevents_fusion(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.assert_ok(self.run_pipeline("--phase", "train-block", "--block", "0"))
        result = self.run_pipeline("--phase", "merge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Splat brut absent", result.stdout + result.stderr)
        self.assertFalse(any(cmd[0] == "merge_splats.py" for cmd in self.commands()))

    def test_bbox_diagnostic_and_off_preserve_raw_records(self):
        for mode in ("diagnostic", "off"):
            with self.subTest(mode=mode):
                output = self.root / f"bbox-{mode}"
                self.append(f'POSTPROCESS_SPARSE_BBOX_MODE="{mode}"\nOUTPUT_DIR="{output}"')
                self.assert_ok(self.run_pipeline())
                self.assertTrue((output / "scene.splat").is_file())
                cropped = output / "gs/block_000/splats/block_000.bbox.splat"
                if mode == "diagnostic":
                    self.assertEqual(cropped.read_bytes(), b"raw-splat")
                else:
                    self.assertFalse(cropped.exists())

    def test_validation_and_no_images(self):
        self.append("TRAIN_CAP_MAX=0")
        result = self.run_pipeline(slurm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRAIN_CAP_MAX", result.stderr)
        self.assertFalse(self.jobs.exists())
        self.append(f'TRAIN_CAP_MAX=2000000\nOUTPUT_DIR="{self.images / "output"}"')
        result = self.run_pipeline()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("extérieur", result.stderr)

    def test_empty_top_level_is_an_error(self):
        (self.images / "root.jpg").unlink()
        self.append("SFM_IMAGES_RECURSIVE=false")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Aucune image", result.stdout + result.stderr)

    def test_failing_crop_stops_before_merge(self):
        self.env["TEST_FAIL_TOOL"] = "filter_splats.py"
        result = self.run_pipeline()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("recadrage COLMAP", result.stdout + result.stderr)
        self.assertFalse(any(cmd[0] == "merge_splats.py" for cmd in self.commands()))
        self.assertFalse((self.output / "scene.splat").exists())

    def test_slurm_runs_same_config_and_array_then_crops(self):
        self.assert_ok(self.run_pipeline("--wait", slurm=True))
        prepare, train, merge = json.loads(self.jobs.read_text())
        self.assertIn("--gres=gpu:1", prepare)
        self.assertIn("--array=0-3%2", train)
        self.assertIn("--dependency=afterok:100", train)
        self.assertIn("--dependency=afterok:101", merge)
        self.assertIn("--gres=none", merge)
        for job in (prepare, train, merge):
            self.assertEqual(job[-3], str(self.config))
            self.assertEqual(job[-1], str(self.images))
        self.assertEqual((self.output / "scene.splat").read_bytes(), b"merged")
        self.assertEqual([cmd[0] for cmd in self.commands()][-5:],
                         ["filter_splats.py"] * 4 + ["merge_splats.py"])

    def test_slurm_failure_prevents_merge_and_is_reported(self):
        self.env["TEST_FAIL_BLOCK"] = "block_001"
        result = self.run_pipeline("--wait", slurm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.output / "scene.splat").exists())
        self.assertTrue(Path(str(self.jobs) + ".cancelled").exists())
        self.assertFalse(any(cmd[0] == "merge_splats.py" for cmd in self.commands()))

    def test_submission_failure_cancels_previous_jobs(self):
        self.env["TEST_SUBMIT_FAIL_AT"] = "2"
        result = self.run_pipeline(slurm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(Path(str(self.jobs) + ".cancelled").read_text().strip(), "100")

    def enable_gpx(self):
        gpx = self.root / "track.gpx"
        gpx.write_text("<gpx/>")
        self.append(f'GEOREF_GPX_FILE="{gpx}"\nGEOREF_VIEW_PREFIX="h00"')
        return gpx

    def test_optional_gpx_local_and_slurm_reference_and_order(self):
        self.enable_gpx()
        for slurm in (False, True):
            with self.subTest(slurm=slurm):
                output = self.root / f"gpx-{slurm}"
                self.append(f'OUTPUT_DIR="{output}"')
                before = len(self.commands()) if self.events.exists() else 0
                self.assert_ok(self.run_pipeline(*(["--wait"] if slurm else []), slurm=slurm))
                commands = self.commands()[before:]
                stages = [cmd[0] for cmd in commands]
                georef = stages.index("georef_colmap_from_gpx.py")
                split = stages.index("colmap_split_blocks.py")
                self.assertLess(georef, split)
                self.assertTrue(any(cmd[:2] == ["spirula", "sfm"] for cmd in commands[:georef]))
                self.assertTrue((output / "georef/source.gpx").exists())
                reference = json.loads((output / "scene.georef.json").read_text())
                self.assertEqual(reference["output_reference"]["axes"], ["East", "North", "Up"])
                self.assertEqual(reference["splat"], "scene.splat")
                self.assertTrue((output / "sfm/sparse/0/points3D.bin").exists())

    def test_gpx_reuse_and_content_change_guard(self):
        gpx = self.enable_gpx()
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.assertEqual(sum(cmd[0] == "georef_colmap_from_gpx.py" for cmd in self.commands()), 1)
        gpx.write_text("<gpx>changed</gpx>")
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_adding_gpx_to_prepared_workspace_is_refused(self):
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.enable_gpx()
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_uncertain_gpx_requires_explicit_acceptance_before_splitting(self):
        self.enable_gpx()
        self.env["TEST_GEOREF_AMBIGUOUS"] = "1"
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ambigu", result.stdout + result.stderr)
        self.assertFalse((self.output / "blocks").exists())
        self.append("GEOREF_ALLOW_UNCERTAIN=true")
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.assertTrue((self.output / "blocks/manifest.json").exists())
        self.assertEqual(sum(cmd[0] == "georef_colmap_from_gpx.py" for cmd in self.commands()), 1)

    def test_high_gpx_residual_is_rejected(self):
        self.enable_gpx()
        self.env["TEST_GEOREF_ERROR"] = "0.2"
        result = self.run_pipeline("--phase", "prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("erreur normalisée", result.stdout + result.stderr)
        self.assertFalse((self.output / ".prepare_done").exists())

    def test_missing_gpx_rejected_before_submission(self):
        self.append('GEOREF_GPX_FILE="/missing/trace.gpx"')
        result = self.run_pipeline(slurm=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GEOREF_GPX_FILE", result.stderr)
        self.assertFalse(self.jobs.exists())

    def test_removing_gpx_from_prepared_workspace_is_refused(self):
        self.enable_gpx()
        self.assert_ok(self.run_pipeline("--phase", "prepare"))
        self.append('GEOREF_GPX_FILE=""')
        result = self.run_pipeline("--phase", "merge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("choisir un nouvel OUTPUT_DIR", result.stdout + result.stderr)

    def test_gpx_failure_stops_before_partition_and_training(self):
        self.enable_gpx()
        self.env["TEST_FAIL_TOOL"] = "georef_colmap_from_gpx.py"
        result = self.run_pipeline()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Géoréférencement GPX", result.stdout + result.stderr)
        self.assertFalse((self.output / "blocks").exists())
        self.assertFalse(any(cmd[:2] == ["spirula", "train"] for cmd in self.commands()))


if __name__ == "__main__":
    unittest.main()
