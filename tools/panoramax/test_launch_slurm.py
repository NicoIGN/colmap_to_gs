import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parent


class SlurmLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tools = self.root / "bin"
        self.tools.mkdir()
        self.pipeline_dir = self.root / "repo with spaces" / "tools" / "panoramax"
        self.pipeline_dir.mkdir(parents=True)
        for name in ("launch.sh", "launch.slurm", "validate_config.sh"):
            shutil.copyfile(SOURCE / name, self.pipeline_dir / name)
        self.output = self.root / "output with spaces,comma"
        self.config = self.root / "dataset config.sh"
        self.config.write_text(
            f'OUTPUT_DIR="{self.output}"\n'
            'PANORAMAX_BBOX_LAMBERT93="651000,6861000,652000,6862000"\n'
            'SPLIT_NUM_BLOCKS=2\nTRAIN_NUM_ITER=3\nTRAIN_CAP_MAX=1000\nTRAIN_PRESET=3dgs\n'
            'PYTHON_BIN=job-python\n'
        )
        (self.pipeline_dir / "run_pipeline.sh").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\nsource "$1"\n'
            'mkdir -p "$OUTPUT_DIR"\n'
            'phase="$2"; block="${3:-none}"\n'
            'printf "%s\\n" "$1" "$PWD" "$OMP_NUM_THREADS" '
            '"${SETUP_LOADED:-}" "${ACTIVATED_ENV:-}" "$XDG_RUNTIME_DIR" '
            '> "$OUTPUT_DIR/$phase-$block.txt"\n'
            'if [[ "$phase" == "${TEST_FAIL_STAGE:-}" && '
            '"$block" == "${TEST_FAIL_BLOCK:-none}" ]]; then exit 7; fi\n'
            'if [[ "$phase" == train-block ]]; then\n'
            '  touch "$OUTPUT_DIR/start-$block"\n'
            '  if [[ "${TEST_REQUIRE_CONCURRENT:-0}" == 1 ]]; then\n'
            '    for ((i=0; i<100; i++)); do\n'
            '      [[ -f "$OUTPUT_DIR/start-0" && -f "$OUTPUT_DIR/start-1" ]] && break\n'
            '      sleep 0.02\n'
            '    done\n'
            '    [[ -f "$OUTPUT_DIR/start-0" && -f "$OUTPUT_DIR/start-1" ]] || exit 8\n'
            '  fi\n'
            'fi\n'
        )
        self.record = self.root / "sbatch.json"
        self.env = dict(
            os.environ, PATH=f"{self.tools}:{os.environ['PATH']}",
            TEST_SBATCH_RECORD=str(self.record), SLURM_TMPDIR=str(self.root),
        )
        for key in ("SLURM_JOB_ID", "SLURM_ARRAY_JOB_ID", "SLURM_ARRAY_TASK_ID",
                    "SLURM_CONDA_ENV", "SLURM_CONDA_BASE", "SLURM_ENV_SETUP",
                    "SLURM_CPUS_PER_TASK", "SLURM_ACCOUNT", "SLURM_PARTITION",
                    "SLURM_QOS", "SLURM_GPU_TYPE", "SLURM_MEMORY",
                    "SLURM_TIME_LIMIT", "SLURM_BLOCK_PARALLELISM"):
            self.env.pop(key, None)
        self.make_tool("sbatch", f"#!{sys.executable}\n" + '''
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys

record = Path(os.environ["TEST_SBATCH_RECORD"])
jobs = json.loads(record.read_text()) if record.exists() else []
args = sys.argv[1:]
job_id = str(12345 + len(jobs))
jobs.append(args)
record.write_text(json.dumps(jobs))
if os.environ.get("TEST_SUBMIT_FAIL_AT") == str(len(jobs)):
    print("submission rejected", file=sys.stderr)
    sys.exit(3)
print(job_id + ";test-cluster", flush=True)

def execute(index):
    args = jobs[index]
    values = dict(arg[2:].split("=", 1) for arg in args if arg.startswith("--") and "=" in arg)
    if "dependency" in values:
        parent = int(values["dependency"].split(":")[1]) - 12345
        if execute(parent):
            return 1
    def run_task(task=None):
        env = dict(os.environ, SLURM_JOB_ID=str(12345 + index),
                   SLURM_CPUS_PER_TASK=values["cpus-per-task"])
        if task is not None:
            env.update(SLURM_ARRAY_TASK_ID=str(task), SLURM_ARRAY_JOB_ID=str(12345 + index))
        def log_path(value):
            return value.replace("%j", str(12345 + index)).replace(
                "%A", str(12345 + index)).replace("%a", str(task))
        with open(log_path(values["output"]), "w") as out, open(log_path(values["error"]), "w") as err:
            return subprocess.run(["bash", *args[-4:]], env=env, cwd=values["chdir"],
                                  stdout=out, stderr=err).returncode
    if "array" in values:
        span, limit = values["array"].split("%")
        start, end = map(int, span.split("-"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=int(limit)) as pool:
            return max(pool.map(run_task, range(start, end + 1)))
    return run_task()

if "--wait" in args:
    sys.exit(execute(len(jobs) - 1))
''')
        self.make_tool(
            "scancel", '#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_SBATCH_RECORD.cancelled"\n',
        )
        self.make_tool(
            "srun",
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            '[[ "$1" == --unbuffered && "$2" == --export=ALL ]]\nshift 2\nexec "$@"\n',
        )
        self.make_tool(
            "nvidia-smi", '#!/bin/sh\necho "GPU 0: test NVIDIA GPU"\n'
            'exit "${TEST_GPU_STATUS:-0}"\n',
        )
        self.make_tool(
            "job-python", '#!/usr/bin/env bash\n'
            '[[ "$1" == -c && "$2" == "import numpy, scipy, tqdm, pyproj, plyfile" ]] '
            '|| exit 99\nexit "${TEST_IMPORT_STATUS:-0}"\n',
        )

    def make_tool(self, name, content):
        path = self.tools / name
        path.write_text(content)
        path.chmod(0o755)

    def launch(self, *args):
        return subprocess.run(
            ["bash", str(self.pipeline_dir / "launch.sh"), str(self.config), *args],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=15,
        )

    def jobs(self):
        return json.loads(self.record.read_text())

    def test_submit_dag_defaults_without_running_pipeline(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        prepare, blocks, merge = self.jobs()
        for args in (prepare, blocks, merge):
            for flag in (
                "--partition=jean-zellou", "--cpus-per-task=16", "--mem=64G",
                "--time=48:00:00", "--nodes=1", "--ntasks=1", "--export=ALL", "--parsable",
            ):
                self.assertIn(flag, args)
            self.assertNotIn("--wait", args)
            self.assertEqual(args[-2], str(self.config))
            self.assertEqual(args[-3], str(self.pipeline_dir.parents[1]))
        self.assertEqual(prepare[-1], "prepare")
        self.assertEqual(blocks[-1], "train-block")
        self.assertEqual(merge[-1], "merge")
        self.assertIn("--gres=gpu:1", prepare)
        self.assertIn("--gres=gpu:1", blocks)
        self.assertIn("--gres=none", merge)
        self.assertIn("--array=0-1%2", blocks)
        self.assertIn("--dependency=afterok:12345", blocks)
        self.assertIn("--dependency=afterok:12346", merge)
        for args in (blocks, merge):
            self.assertIn("--kill-on-invalid-dep=yes", args)
        self.assertIn("scancel 12345 12346 12347", result.stdout)
        self.assertTrue((self.output / "logs/submit.log").is_file())
        self.assertFalse((self.output / "prepare-none.txt").exists())

    def test_wait_runs_all_blocks_concurrently_with_setup_and_conda(self):
        setup = self.root / "site setup.sh"
        setup.write_text("export SETUP_LOADED=yes\n")
        conda = self.root / "conda" / "etc/profile.d"
        conda.mkdir(parents=True)
        (conda / "conda.sh").write_text(
            'conda() { [[ "$1" == activate ]] || return 1; export ACTIVATED_ENV="$2"; }\n'
        )
        with self.config.open("a") as stream:
            stream.write(
                'SLURM_PARTITION=gpu-test\nSLURM_CPUS_PER_TASK=6\n'
                'SLURM_MEMORY=32G\nSLURM_TIME_LIMIT=02:30:00\n'
                'SLURM_GPU_TYPE=a100\nSLURM_ACCOUNT=project\nSLURM_QOS=normal\n'
                f'SLURM_ENV_SETUP="{setup}"\nSLURM_CONDA_ENV=gsplat\n'
                f'SLURM_CONDA_BASE="{self.root / "conda"}"\n'
            )
        self.env["TEST_REQUIRE_CONCURRENT"] = "1"
        result = self.launch("--wait")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        prepare, blocks, merge = self.jobs()
        self.assertNotIn("--wait", prepare)
        self.assertNotIn("--wait", blocks)
        self.assertIn("--wait", merge)
        for flag in (
            "--partition=gpu-test", "--cpus-per-task=6", "--mem=32G",
            "--time=02:30:00", "--gres=gpu:a100:1", "--account=project", "--qos=normal",
        ):
            self.assertIn(flag, blocks)
        for name in ("prepare-none", "train-block-0", "train-block-1", "merge-none"):
            lines = (self.output / f"{name}.txt").read_text().splitlines()
            self.assertEqual(lines[:5], [str(self.config), str(self.root), "6", "yes", "gsplat"])
            self.assertFalse(Path(lines[5]).exists())
        self.assertIn("Pipeline terminé avec succès", result.stdout)
        for index in range(2):
            self.assertIn("GPU 0", (self.output / f"logs/panoramax-block-12346_{index}.out").read_text())
        self.assertNotIn("GPU 0", (self.output / "logs/panoramax-merge-12347.out").read_text())

    def test_parallelism_limit(self):
        with self.config.open("a") as stream:
            stream.write("SLURM_BLOCK_PARALLELISM=1\n")
        result = self.launch("--wait")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--array=0-1%1", self.jobs()[1])

    def test_cleangs_merge_allocates_gpu(self):
        with self.config.open("a") as stream:
            stream.write('POSTPROCESS_CLEANER=cleangs\nCLEANGS_MODEL="/local/model"\n')
        result = self.launch("--wait")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--gres=gpu:1", self.jobs()[2])
        self.assertIn("GPU 0", (self.output / "logs/panoramax-merge-12347.out").read_text())

    def test_prepare_failure_prevents_all_training_and_merge(self):
        self.env["TEST_FAIL_STAGE"] = "prepare"
        result = self.launch("--wait")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.output / "train-block-0.txt").exists())
        self.assertFalse((self.output / "merge-none.txt").exists())
        error = (self.output / "logs/panoramax-prepare-12345.err").read_text()
        self.assertIn("job 12345", error)
        self.assertIn("code 7", error)
        self.assertIn("Étape : prepare", error)

    def test_one_failed_block_prevents_merge(self):
        self.env.update(TEST_FAIL_STAGE="train-block", TEST_FAIL_BLOCK="0")
        result = self.launch("--wait")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.output / "train-block-1.txt").exists())
        self.assertFalse((self.output / "merge-none.txt").exists())
        self.assertNotIn("Pipeline terminé avec succès", result.stdout)
        error = (self.output / "logs/panoramax-block-12346_0.err").read_text()
        self.assertIn("code 7", error)
        self.assertIn("panoramax-block-12346_0", error)

    def test_gpu_and_import_failures_stop_before_pipeline(self):
        for variable in ("TEST_GPU_STATUS", "TEST_IMPORT_STATUS"):
            with self.subTest(variable=variable):
                self.env[variable] = "9"
                result = self.launch("--wait")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.output / "prepare-none.txt").exists())
                self.env.pop(variable)

    def test_partial_submission_failure_cancels_submitted_jobs(self):
        self.env["TEST_SUBMIT_FAIL_AT"] = "2"
        result = self.launch()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("submission rejected", result.stdout + result.stderr)
        self.assertNotIn("Jobs soumis", result.stdout)
        self.assertEqual(Path(str(self.record) + ".cancelled").read_text().splitlines(), ["12345"])

    def test_invalid_parallelism_fails_before_submission(self):
        with self.config.open("a") as stream:
            stream.write("SLURM_BLOCK_PARALLELISM=0\n")
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SLURM_BLOCK_PARALLELISM", result.stderr)
        self.assertFalse(self.record.exists())

    def test_missing_config_fails_before_submission(self):
        self.config.unlink()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.record.exists())

    def test_batch_requires_slurm_allocation(self):
        result = subprocess.run(
            ["bash", str(self.pipeline_dir / "launch.slurm"),
             str(self.pipeline_dir.parents[1]), str(self.config)],
            env=self.env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SLURM_JOB_ID", result.stderr)


if __name__ == "__main__":
    unittest.main()
