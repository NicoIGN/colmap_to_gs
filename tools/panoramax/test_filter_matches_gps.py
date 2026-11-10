import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from pyproj import Geod


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("filter_matches_gps", ROOT / "filter_matches_gps.py")
gps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gps)


def matches_file(version=4):
    images = b"".join(struct.pack("<I", len(name)) + name + struct.pack("<I", 12)
                      for name in (b"a", b"b", b"c", b"missing"))
    pairs = [(0, 1), (0, 2), (0, 3), (2, 3)]
    records = [struct.pack("<IIiI", a, b, 2, 1) + struct.pack("<II", 3, 5) for a, b in pairs]
    tail = b""
    if version >= 3:
        tail = (
            struct.pack("<I", 1) +
            struct.pack("<IIII BdI", 1, 4096, 2160, 1, 1, 0.5, 4) +
            struct.pack("<4d", 1800, 1800, 2048, 1080) +
            struct.pack("<I4I", 4, 1, 1, 1, 1)
        )
        if version == 4:
            tail += b"\x01"
    prefix = struct.pack("<4sII", b"VKMT", version, 4) + images
    return prefix, records, tail


def metadata(root):
    root.mkdir(exist_ok=True)
    geod = Geod(ellps="WGS84")
    positions = {"a": (2.35, 48.85), "b": geod.fwd(2.35, 48.85, 90, 29)[:2],
                 "c": geod.fwd(2.35, 48.85, 90, 31)[:2]}
    for name, pos in positions.items():
        (root / f"{name}.json").write_text(json.dumps({
            "feature": {"geometry": {"type": "Point", "coordinates": list(pos)},
                        "properties": {"pers:interior_orientation": {"field_of_view": 95}}},
        }))


class GpsMatchesTest(unittest.TestCase):
    def test_versions_preserve_records_and_cameras_and_keep_unknown_gps(self):
        for version in (2, 3, 4):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                metadata(root / "metadata")
                prefix, records, tail = matches_file(version)
                original = prefix + struct.pack("<I", 4) + b"".join(records) + tail
                source, target = root / "matches.bin", root / "filtered.bin"
                source.write_bytes(original)
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    counts = gps.filter_matches(source, target, root / "metadata", 30)
                self.assertEqual(counts["kept_pairs"], 3)
                self.assertEqual(counts["removed_pairs"], 1)
                self.assertEqual(counts["kept_without_gps"], 2)
                self.assertEqual(target.read_bytes(), prefix + struct.pack("<I", 3) +
                                 records[0] + records[2] + records[3] + tail)
                self.assertEqual(source.read_bytes(), original)
                boundary = Geod(ellps="WGS84").inv(
                    *gps.position(root / "metadata", "a"),
                    *gps.position(root / "metadata", "b"),
                )[2]
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    counts = gps.filter_matches(source, target, root / "metadata", boundary)
                self.assertEqual(counts["kept_pairs"], 2)  # Strictly less, not <=.

    def test_corruption_and_bad_parameters_do_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata(root / "metadata")
            source, target = root / "matches.bin", root / "filtered.bin"
            prefix, records, tail = matches_file()
            good = prefix + struct.pack("<I", 4) + b"".join(records) + tail
            target.write_bytes(b"existing")
            variants = [
                good[:8], good[:-1], b"NOPE" + good[4:],
                good[:4] + struct.pack("<I", 99) + good[8:],
                prefix + struct.pack("<I", 1) + struct.pack("<IIiI", 99, 0, 0, 0) + tail,
                prefix + struct.pack("<I", 1) + records[1][:-1],
            ]
            for data in variants:
                source.write_bytes(data)
                with self.assertRaises(ValueError):
                    gps.filter_matches(source, target, root / "metadata", 30)
                self.assertEqual(target.read_bytes(), b"existing")
                self.assertEqual(list(root.glob("*.part")), [])
            source.write_bytes(good)
            for radius in (0, -1, float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    gps.filter_matches(source, target, root / "metadata", radius)
            with self.assertRaises(ValueError):
                gps.filter_matches(source, source, root / "metadata", 30)

    def test_unknown_gps_preserves_candidates_and_zero_pairs_cli_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata(root / "metadata")
            source, target = root / "matches.bin", root / "filtered.bin"
            prefix, records, tail = matches_file()
            source.write_bytes(prefix + struct.pack("<I", 1) + records[1] + tail)
            result = subprocess.run(
                [sys.executable, str(ROOT / "filter_matches_gps.py"), "--input", str(source),
                 "--output", str(target), "--metadata-dir", str(root / "metadata"),
                 "--max-distance", "30"],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Aucune paire après filtrage", result.stderr)
            for name in ("a", "c"):
                (root / f"metadata/{name}.json").write_text(json.dumps({
                    "feature": {"geometry": {"type": "Point", "coordinates": [True, 48]}}
                }))
            warning = io.StringIO()
            with contextlib.redirect_stderr(warning), contextlib.redirect_stdout(io.StringIO()):
                counts = gps.filter_matches(source, target, root / "metadata", 30)
            self.assertEqual(counts["kept_pairs"], 1)
            self.assertEqual(counts["kept_without_gps"], 1)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertIn("AVERTISSEMENT", warning.getvalue())

    def test_staged_pipeline_maps_filtered_matches_and_checks_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            images = dataset / "images"
            images.mkdir(parents=True)
            metadata(dataset / "metadata")
            # Minimal JPEG sufficient for prepare_sfm's dimension reader.
            jpeg = (b"\xff\xd8\xff\xc0" + (11).to_bytes(2, "big") + b"\x08" +
                    (2160).to_bytes(2, "big") + (4096).to_bytes(2, "big") +
                    b"\x01\x01\x11\x00\xff\xd9")
            for name in ("a", "b", "c", "missing"):
                (images / f"{name}.jpg").write_bytes(jpeg)
            prefix, records, tail = matches_file()
            fixture = root / "fixture.bin"
            fixture.write_bytes(prefix + struct.pack("<I", 4) + b"".join(records) + tail)
            log = root / "commands.jsonl"
            spirula = root / "spirula"
            spirula.write_text(f"#!{sys.executable}\n" + """
import json, os, pathlib, shutil, struct, sys
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
stage = args[1]
if stage == os.environ.get("FAIL_STAGE"):
    sys.exit(7)
out = pathlib.Path(args[args.index("--output") + 1])
if stage == "extract":
    out.mkdir(exist_ok=True)
elif stage == "match":
    shutil.copyfile(os.environ["MATCHES_FIXTURE"], out)
elif stage == "map":
    assert pathlib.Path(args[2]).name == "matches-gps.bin"
    assert "--metric-gps" in args and args[args.index("--metric-gps") + 1] == "full"
    assert "--image-dir" in args
    sparse = out / "0"
    sparse.mkdir(parents=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        (sparse / name).write_bytes(b"colmap")
""")
            spirula.chmod(0o755)
            output = dataset / "sfm-3"
            masks = root / "masks"
            masks.mkdir()
            (masks / "a.jpg.png").write_bytes(b"mask")
            (masks / "masks.json").write_text(json.dumps({
                "settings": {"labels": ["sky"]}, "summary": {"generated": 1}
            }))
            command = [
                sys.executable, str(ROOT / "run_sfm_filtered.py"), "--dataset", str(dataset),
                "--output", str(output), "--spirula-bin", str(spirula), "--max-distance", "30",
                "--matcher", "bruteforce", "--threads", "4",
                "--feature-masks", str(masks),
            ]
            env = dict(os.environ, COMMAND_LOG=str(log), MATCHES_FIXTURE=str(fixture))

            def execute(args=command):
                return subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)

            result = execute()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = json.loads((output / "gps_filter_summary.json").read_text())
            self.assertEqual(summary["kept_pairs"], 3)
            self.assertEqual((output / "matches.bin").read_bytes(), fixture.read_bytes())
            commands = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual([cmd[1] for cmd in commands], ["extract", "match", "map"])
            self.assertIn("--focal", commands[1])
            self.assertEqual(commands[0][commands[0].index("--feature-masks") + 1], str(masks))
            self.assertNotIn("--sensor-pairs", commands[1])
            self.assertEqual(commands[1][commands[1].index("--max-error") + 1], "2")
            self.assertEqual(commands[1][commands[1].index("--min-inliers") + 1], "30")
            for flag, value in (("--max-error", "2"), ("--min-tri-angle", "3"),
                                ("--ba-loss", "huber"), ("--ba-loss-param", "2")):
                self.assertEqual(commands[2][commands[2].index(flag) + 1], value)
            self.assertEqual(commands[2][2], str(output / "matches-gps.bin"))
            # Rewriting identical metadata does not invalidate reuse.
            metadata(dataset / "metadata")
            # Resume statistics change, but mask pixels/settings do not.
            (masks / "masks.json").write_text(json.dumps({
                "settings": {"labels": ["sky"]}, "summary": {"reused": 1}
            }))
            result = execute()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(log.read_text().splitlines()), 3)
            changed_geometry = command + ["--min-tri-angle", "4"]
            result = execute(changed_geometry)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sauvegarde", result.stderr)
            (masks / "a.jpg.png").write_bytes(b"changed")
            result = execute()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sauvegarde", result.stderr)
            (masks / "a.jpg.png").write_bytes(b"mask")
            changed = command.copy()
            changed[changed.index("--max-distance") + 1] = "40"
            result = execute(changed)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sauvegarde", result.stderr)
            env["FAIL_STAGE"] = "match"
            failed = command.copy()
            failed[failed.index("--output") + 1] = str(dataset / "failed-sfm")
            result = execute(failed)
            self.assertEqual(result.returncode, 7)
            self.assertFalse(json.loads((dataset / "failed-sfm/gps_filter_run.json").read_text())["complete"])
            self.assertFalse((dataset / "failed-sfm/sparse").exists())
            env.pop("FAIL_STAGE")
            fixture.write_bytes(prefix + struct.pack("<I", 1) + records[1] + tail)
            empty = command.copy()
            empty[empty.index("--output") + 1] = str(dataset / "empty-sfm")
            result = execute(empty)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("Aucune paire après filtrage", result.stderr)
            self.assertEqual(json.loads(log.read_text().splitlines()[-1])[1], "match")
            self.assertFalse((dataset / "empty-sfm/sparse").exists())


if __name__ == "__main__":
    unittest.main()
