import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "panoramax_to_3dgs.sh"


class PanoramaxPipelineTest(unittest.TestCase):
    def test_existing_lambert93_request_uses_default_epsg(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "output" / "panoramax"
            (dataset / "images").mkdir(parents=True)
            (dataset / "images" / "photo.jpg").write_bytes(b"old")
            (dataset / "request.json").write_text(json.dumps({
                "bbox": [651000, 6861000, 652000, 6862000],
                "crs": "EPSG:2154",
                "api_url": "https://explore.panoramax.fr/api",
                "datetime": None, "quality": "hd", "image_max": None,
            }))
            python = root / "python3"
            python.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if sys.argv[1:2] == ['-'] or sys.argv[1].endswith('pipeline_report.py'):\n"
                f"  os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
                "sys.exit(42)\n"
            )
            python.chmod(0o755)
            config = root / "config.sh"
            config.write_text(
                f'OUTPUT_DIR="{root / "output"}"\n'
                'PANORAMAX_BBOX="651000,6861000,652000,6862000"\n'
                'SPLIT_NUM_BLOCKS=2\nTRAIN_NUM_ITER=3\nTRAIN_CAP_MAX=1000\nTRAIN_PRESET=3dgs\n'
                f'PYTHON_BIN="{python}"\n'
            )
            result = subprocess.run(
                ["bash", str(SCRIPT), str(config), "prepare"],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 42, result.stderr)
            self.assertNotIn("PANORAMAX_REPLACE_REQUEST", result.stderr)
            self.assertNotIn("archivée", result.stdout)
            self.assertTrue((dataset / "images" / "photo.jpg").is_file())

    def test_download_fatal_error_names_stage_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "failed-python"
            python.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if sys.argv[1].endswith('pipeline_report.py'):\n"
                "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
                "sys.exit(13)\n")
            python.chmod(0o755)
            config = root / "config.sh"
            config.write_text(
                f'OUTPUT_DIR="{root / "output"}"\n'
                'PANORAMAX_BBOX="651000,6861000,652000,6862000"\n'
                'SPLIT_NUM_BLOCKS=2\nTRAIN_NUM_ITER=3\nTRAIN_CAP_MAX=1000\nTRAIN_PRESET=3dgs\n'
                f'PYTHON_BIN="{python}"\n'
            )
            result = subprocess.run(
                ["bash", str(SCRIPT), str(config)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 13)
            self.assertIn("téléchargement des photos Panoramax", result.stderr)
            self.assertIn("code 13", result.stderr)
            self.assertIn("summary.json", result.stderr)
            self.assertIn("manifest.jsonl", result.stderr)
            report = json.loads((root/"output/pipeline_report.json").read_text())["runs"][0]
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["exit_code"], 13)
            self.assertEqual(report["phases"][-1]["name"], "téléchargement des photos Panoramax")

    def test_changed_request_stops_or_archives_previous_dataset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "output" / "panoramax"
            (dataset / "images").mkdir(parents=True)
            (dataset / "images" / "old.jpg").write_bytes(b"old")
            (dataset / "request.json").write_text(json.dumps({
                "bbox": [652600, 6863300, 652900, 6863500],
                "api_url": "https://explore.panoramax.fr/api", "datetime": None,
                "quality": "hd", "image_max": None,
            }))
            python = root / "python3"
            python.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "if sys.argv[1:2] == ['-'] or sys.argv[1].endswith('pipeline_report.py'):\n"
                f"  os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
                "sys.exit(42)\n"
            )
            python.chmod(0o755)
            config = root / "config.sh"
            config.write_text(
                f'OUTPUT_DIR="{root / "output"}"\n'
                'PANORAMAX_BBOX="652600,6863400,652800,6863500"\n'
                'SPLIT_NUM_BLOCKS=2\nTRAIN_NUM_ITER=3\nTRAIN_CAP_MAX=1000\nTRAIN_PRESET=3dgs\n'
                f'PYTHON_BIN="{python}"\n'
            )
            run = lambda: subprocess.run(["bash", str(SCRIPT), str(config), "prepare"],
                                         capture_output=True, text=True, timeout=15)
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn("PANORAMAX_REPLACE_REQUEST", result.stderr)
            self.assertIn("bbox", result.stderr)
            self.assertTrue((dataset / "images" / "old.jpg").is_file())
            with config.open("a") as stream:
                stream.write('PANORAMAX_REPLACE_REQUEST="true"\n')
            result = run()
            self.assertEqual(result.returncode, 42, result.stderr)
            self.assertIn("archivée", result.stdout)
            archives = list(dataset.parent.glob("panoramax.replaced-*"))
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "images" / "old.jpg").is_file())
            self.assertFalse((dataset / "images" / "old.jpg").exists())

    def test_config_drives_download_blocks_merge_and_georef_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            dataset = output / "panoramax"
            sparse = dataset / "sfm-3" / "sparse" / "0"
            images = dataset / "sfm-3" / "images"
            sparse.mkdir(parents=True)
            images.mkdir()
            for name in ("cameras.bin", "images.bin", "points3D.bin"):
                (sparse / name).write_bytes(b"colmap")
            (images / "img.jpg").write_bytes(b"prepared")

            tools = root / "bin"
            tools.mkdir()
            fake_python = tools / "python3"
            fake_python.write_text(
                f"#!{sys.executable}\n"
                "import json, os, runpy, sys\n"
                "args = sys.argv[1:]\n"
                "if args and args[0] != '-' and not args[0].endswith('pipeline_report.py'):\n"
                "  with open(os.environ['COMMAND_LOG'], 'a') as log:\n"
                "    log.write(os.path.basename(args[0]) + '\\n')\n"
                "if args and args[0].endswith('download.py'):\n"
                "  root = args[args.index('--output-dir') + 1]\n"
                "  bbox = [float(x) for x in args[args.index('--bbox') + 1].split(',')]\n"
                "  os.makedirs(os.path.join(root, 'images'), exist_ok=True)\n"
                "  os.makedirs(os.path.join(root, 'metadata'), exist_ok=True)\n"
                "  json.dump({'bbox':bbox,'api_url':args[args.index('--api-url')+1],"
                "    'datetime':None,'quality':args[args.index('--quality')+1],"
                "    'image_max':int(args[args.index('--image-max')+1]),"
                "    'bbox_epsg':int(args[args.index('--bbox-epsg')+1]),"
                "    'bbox_crs':'EPSG:'+args[args.index('--bbox-epsg')+1]},"
                "    open(os.path.join(root,'request.json'),'w'))\n"
                "  json.dump({'available':1,'selected':1}, open(os.path.join(root,'summary.json'),'w'))\n"
                "  open(os.path.join(root,'images','photo.jpg'),'wb').write(b'jpeg')\n"
                "  json.dump({'download':{}}, open(os.path.join(root,'metadata','photo.json'),'w'))\n"
                "  json.dump({'image':'images/photo.jpg','status':'downloaded'},"
                "    open(os.path.join(root,'manifest.jsonl'),'w'))\n"
                "elif args and args[0].endswith('run_sfm_filtered.py'):\n"
                "  root=args[args.index('--output')+1]\n"
                "  json.dump(args[1:],open(os.path.join(root,'gps_filter_args.json'),'w'))\n"
                "  json.dump({'max_distance_m':float(args[args.index('--max-distance')+1])},"
                "    open(os.path.join(root,'gps_filter_summary.json'),'w'))\n"
                "  json.dump({'complete':True},open(os.path.join(root,'gps_filter_run.json'),'w'))\n"
                "elif args and args[0].endswith('prepare_masks.py'):\n"
                "  root=args[2]\n"
                "  json.dump({k:v for k,v in os.environ.items() if k.startswith('SFM_MASKS') or k=='MASKS_ACCEPT_LICENSE'},"
                "    open(os.path.join(root,'mask_env.json'),'w'))\n"
                "elif args and args[0].endswith('colmap_split_blocks.py'):\n"
                "  out=args[args.index('--output')+1]; n=int(args[args.index('--num-blocks')+1]); os.makedirs(out,exist_ok=True); blocks=[]\n"
                "  for i in range(n):\n"
                "    name=f'block_{i:03d}'; folder=os.path.join(out,name); os.makedirs(folder)\n"
                "    [open(os.path.join(folder,x),'wb').write(b'colmap') for x in ('cameras.bin','points3D.bin')]\n"
                "    open(os.path.join(folder,'images.bin'),'wb').write((3+i).to_bytes(8,'little'))\n"
                "    blocks.append({'block_id':i})\n"
                "  json.dump({'blocks':blocks},open(os.path.join(out,'manifest.json'),'w'))\n"
                "elif args and args[0].endswith('export_ply.py'):\n"
                "  assert args[args.index('--filter-mode')+1]=='off'\n"
                "  out=args[args.index('--out-ply')+1]; os.makedirs(os.path.dirname(out),exist_ok=True); open(out,'wb').write(b'splat')\n"
                "elif args and args[0].endswith('merge_splats.py'):\n"
                "  if os.environ.get('TEST_PREMERGE_BBOX')=='1':\n"
                "    assert all(x.endswith('.bbox.splat') for x in args[1:args.index('--output')]), args\n"
                "  out=args[args.index('--output')+1]; open(out,'wb').write(b'merged-splat')\n"
                "elif args and args[0].endswith('filter_splats.py'):\n"
                "  import pathlib, shutil\n"
                "  premerge=args[args.index('--output')+1].endswith('.bbox.splat')\n"
                "  assert args[args.index('--mode')+1]==('off' if premerge else 'apply')\n"
                "  if not premerge: assert args[args.index('--size-factor')+1]=='12'\n"
                "  assert args[args.index('--sparse-points')+1].endswith('sfm-3/sparse/0/points3D.bin')\n"
                "  assert args[args.index('--sparse-bbox-mode')+1]==('apply' if premerge or os.environ.get('TEST_PREMERGE_BBOX')=='1' else 'diagnostic')\n"
                "  assert args[args.index('--sparse-bbox-margin')+1]=='0.1'\n"
                "  out=pathlib.Path(args[args.index('--output')+1])\n"
                "  shutil.copyfile(args[args.index('--input')+1],out)\n"
                "  out.with_suffix('.rejected.splat').write_bytes(b'')\n"
                "  out.with_suffix('.filter.json').write_text('{}')\n"
                "elif args and args[0].endswith('clean_sky.py'):\n"
                "  import pathlib, shutil\n"
                "  assert args[args.index('--scene')+1].endswith('sfm-3')\n"
                "  assert args[args.index('--model')+1]=='/local/model'\n"
                "  assert args[args.index('--mode')+1]=='diagnostic'\n"
                "  out=pathlib.Path(args[args.index('--output')+1])\n"
                "  shutil.copyfile(args[args.index('--input')+1],out)\n"
                "  out.with_suffix('.filter.json').write_text('{\"algorithm\":\"cleangs\"}')\n"
                "else:\n"
                "  os.execv(sys.executable,[sys.executable,*args])\n"
            )
            fake_python.chmod(0o755)
            spirula = tools / "spirula"
            spirula.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "args=sys.argv[1:]\n"
                "if args[0]=='train':\n"
                "  get=lambda k: args[args.index(k)+1]\n"
                "  assert get('--cap-max') in ('600','800'), args\n"
                "  assert get('--quality')=='high', args\n"
                "  assert get('--distraction-robustness')=='mild', args\n"
                "  assert get('--num-iterations')=='3', args\n"
                "  assert (get('--growth-factor'),get('--refine-stop-iter'),get('--refine-stop-num-iter'),get('--floater-suppression'))==('1.03','2','1','mild'), args\n"
                "  root=args[args.index('--output-dir-prefix')+1]; name=args[args.index('--output-dir-name')+1]; n=int(args[args.index('--num-iterations')+1]); splats=os.path.join(root,name); ckpt=os.path.join(splats,f'step-{n:09d}.ckpt'); os.makedirs(ckpt,exist_ok=True)\n"
                "  open(os.path.join(ckpt,'splat.ply'),'wb').write(b'ply')\n"
                "  json.dump({'format':'spirula-scene-transform','version':1,'train_from_world':{'matrix_4x4':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}},open(os.path.join(splats,'scene_transform.json'),'w'))\n"
            )
            spirula.chmod(0o755)
            config = root / "dataset.sh"
            config.write_text(
                f'OUTPUT_DIR="{output}"\n'
                'PANORAMAX_BBOX="651000,6861000,652000,6862000"\n'
                'PANORAMAX_BBOX_EPSG="4326"\n'
                'PANORAMAX_API_URL="https://example.test/api"\n'
                'PANORAMAX_QUALITY="hd"\n'
                'PANORAMAX_IMAGE_MAX="5"\n'
                f'SPIRULA_BIN="{spirula}"\n'
                f'PYTHON_BIN="{fake_python}"\n'
                'SPLIT_NUM_BLOCKS="2"\nTRAIN_NUM_ITER="3"\nTRAIN_CAP_MAX="1000"\n'
                'TRAIN_SP_PRESET="3dgs"\nTRAIN_QUALITY="high"\nTRAIN_DISTRACTION_ROBUSTNESS="mild"\nSFM_MAX_IMAGE_SIZE="1600"\n'
                'SFM_ALIKED_MAX_FEATURES="2048"\nSFM_PAIRS="prefilter"\n'
                'SFM_SP_PAIR_MAX_DISTANCE="30"\nSFM_MATCHER="bruteforce"\nSFM_THREADS="6"\n'
                'SFM_MAX_ERROR="1.5"\nSFM_MIN_INLIERS="40"\nSFM_MIN_TRI_ANGLE="4"\n'
                'SFM_SP_BA_LOSS="cauchy"\nSFM_BA_LOSS_PARAM="1"\n'
                'SPLIT_OVERLAP_MAX_FRACTION="0.05"\nTRAIN_CLEAN="false"\n'
                'EXPORT_SPLAT_NAME="test.splat"\nMERGE_STRENGTH="1.0"\n'
                'POSTPROCESS_FILTER_MODE="apply"\nPOSTPROCESS_FILTER_SIZE_FACTOR="12"\n'
                'POSTPROCESS_SPARSE_BBOX_MODE="diagnostic"\nPOSTPROCESS_SPARSE_BBOX_MARGIN="0.1"\n'
                'TRAIN_SP_SPLATS_PER_IMAGE="200"\nTRAIN_GROWTH_FACTOR="1.03"\n'
                'TRAIN_SP_REFINE_STOP_ITER="2"\nTRAIN_REFINE_STOP_NUM_ITER="1"\nTRAIN_FLOATER_SUPPRESSION="mild"\n'
            )
            log = root / "commands.log"

            def execute(*phase):
                return subprocess.run(
                    ["bash", str(SCRIPT), str(config), *phase], cwd=root,
                    env=dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}",
                             COMMAND_LOG=str(log)),
                    capture_output=True, text=True, timeout=30,
                )

            result = execute("prepare")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            request_metadata = json.loads((dataset / "request.json").read_text())
            self.assertEqual(request_metadata["bbox_epsg"], 4326)
            self.assertEqual(request_metadata["bbox_crs"], "EPSG:4326")
            self.assertFalse((dataset / "gs-3-blocks-2").exists())
            self.assertFalse((output / "test.splat").exists())
            gps_args = json.loads((dataset / "sfm-3/gps_filter_args.json").read_text())
            for flag, value in (("--max-distance", "30"), ("--matcher", "bruteforce"),
                                ("--threads", "6"), ("--max-image-size", "1600"),
                                ("--aliked-max-features", "2048"),
                                ("--max-error", "1.5"), ("--min-inliers", "40"),
                                ("--min-tri-angle", "4"), ("--ba-loss", "cauchy"),
                                ("--ba-loss-param", "1")):
                self.assertEqual(gps_args[gps_args.index(flag) + 1], value)
            # The versioned pipeline must forward independent SfM mask settings,
            # even when training masks are disabled.
            with config.open("a") as stream:
                stream.write(
                    'SFM_MASKS=on\nSFM_MASKS_BACKEND=spirula\n'
                    'SFM_MASKS_SPIRULA_MODEL=/models/sam3.ggml\n'
                    'SFM_MASKS_LABELS=sky,car,person\nSFM_MASKS_CONFIDENCE=0.6\n'
                    'SFM_MASKS_DILATE=0.003\nTRAIN_MASKS=off\nMASKS_ACCEPT_LICENSE=true\n')
            result = execute("prepare")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            mask_env = json.loads((dataset / "sfm-3/mask_env.json").read_text())
            self.assertEqual(mask_env["SFM_MASKS_LABELS"], "sky,car,person")
            self.assertEqual(mask_env["SFM_MASKS_BACKEND"], "spirula")
            self.assertEqual(mask_env["SFM_MASKS_CONFIDENCE"], "0.6")
            self.assertEqual(mask_env["SFM_MASKS_DILATE"], "0.003")
            self.assertEqual(mask_env["MASKS_ACCEPT_LICENSE"], "true")
            gps_args = json.loads((dataset / "sfm-3/gps_filter_args.json").read_text())
            self.assertEqual(gps_args[gps_args.index("--feature-masks") + 1],
                             str(dataset / "sfm-3/feature_masks"))
            result = execute("train-block", "0")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((dataset / "gs-3-blocks-2/block_001").exists())
            result = execute("merge")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Splat du bloc absent", result.stderr)
            self.assertFalse((output / "test.splat").exists())
            result = execute("train-block", "1")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = execute("merge")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            commands = log.read_text().splitlines()
            self.assertEqual(commands.count("download.py"), 2)
            self.assertEqual(commands.count("colmap_split_blocks.py"), 1)
            self.assertEqual(commands.count("merge_splats.py"), 1)
            self.assertEqual(commands.count("filter_splats.py"), 1)
            self.assertEqual((output / "test.unfiltered.splat").read_bytes(), b"merged-splat")
            self.assertEqual((output / "test.splat").read_bytes(), b"merged-splat")
            georef = output / "test.georef"
            self.assertTrue((georef / "georeference.json").is_file())
            self.assertTrue((georef / "metadata/photo.json").is_file())
            self.assertTrue((georef / "sparse/images.bin").is_file())
            self.assertTrue((georef / "blocks_manifest.json").is_file())
            self.assertTrue((georef / "splat_filter_summary.json").is_file())
            self.assertEqual(json.loads((georef / "gps_filter_summary.json").read_text())["max_distance_m"], 30)
            self.assertTrue((georef / "scene_transforms/block_001.json").is_file())
            metadata = json.loads((georef / "georeference.json").read_text())
            self.assertEqual(metadata["coordinate_frame"], "Spirula SfM metric frame aligned from Panoramax EXIF GPS")
            self.assertEqual(metadata["source_bbox_crs"], "EPSG:4326")
            with config.open("a") as stream:
                stream.write('POSTPROCESS_CLEANER="cleangs"\nCLEANGS_MODEL="/local/model"\n')
            result = execute("merge")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads((georef / "splat_filter_summary.json").read_text())["algorithm"], "cleangs")
            with config.open("a") as stream:
                stream.write('POSTPROCESS_CLEANER="contextual"\n')
            result = execute()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((georef / "sparse/0").exists())
            result = execute("train-block", "2")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hors de", result.stderr)
            with config.open("a") as stream:
                stream.write('POSTPROCESS_SPARSE_BBOX_MODE="apply"\n')
            os.environ["TEST_PREMERGE_BBOX"] = "1"
            try:
                before = len(log.read_text().splitlines())
                result = execute("merge")
            finally:
                os.environ.pop("TEST_PREMERGE_BBOX")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(log.read_text().splitlines()[before:], [
                "filter_splats.py", "filter_splats.py",
                "merge_splats.py", "filter_splats.py"])
            for index in range(2):
                splats = dataset / f"gs-3-blocks-2/block_{index:03d}/splats"
                self.assertTrue((splats / f"Paris-3-block-{index:03d}.bbox.splat").is_file())
                self.assertEqual((splats / f"Paris-3-block-{index:03d}.splat").read_bytes(),
                                 b"splat")
            with config.open("a") as stream:
                stream.write('SFM_SP_PAIR_MAX_DISTANCE=""\n')
            result = execute("prepare")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Ce SfM a été filtré par GPS", result.stderr)
            with config.open("a") as stream:
                stream.write('SFM_SP_PAIR_MAX_DISTANCE="30"\n')
            (dataset / "sfm-3").rename(dataset / "sfm-3-backup")
            result = execute("prepare")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("anciens entraînements", result.stderr)


if __name__ == "__main__":
    unittest.main()
