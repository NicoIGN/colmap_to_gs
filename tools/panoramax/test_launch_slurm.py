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
        for name in ("launch.sh", "launch.slurm"):
            shutil.copyfile(SOURCE / name, self.pipeline_dir / name)
        self.output = self.root / "output with spaces,comma"
        self.config = self.root / "dataset config.sh"
        self.config.write_text(
            f'OUTPUT_DIR="{self.output}"\n'
            'BBOX_LAMBERT93="651000,6861000,652000,6862000"\n'
            'NUM_BLOCKS=2\nNUM_ITER=3\nCAP_MAX=1000\nTRAIN_PRESET=3dgs\n'
            'PYTHON_BIN=job-python\n'
        )
        (self.pipeline_dir / "run_pipeline.sh").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\nsource "$1"\n'
            'mkdir -p "$OUTPUT_DIR"\n'
            'printf "%s\\n" "$1" "$PWD" "$OMP_NUM_THREADS" '
            '"${SETUP_LOADED:-}" "${ACTIVATED_ENV:-}" "$XDG_RUNTIME_DIR" '
            '> "$OUTPUT_DIR/pipeline.txt"\n'
            'exit "${TEST_PIPELINE_STATUS:-0}"\n'
        )
        self.record = self.root / "sbatch.json"
        self.env = dict(
            os.environ,
            PATH=f"{self.tools}:{os.environ['PATH']}",
            TEST_SBATCH_RECORD=str(self.record),
            SLURM_TMPDIR=str(self.root),
        )
        for key in ("SLURM_JOB_ID", "SLURM_CONDA_ENV", "SLURM_CONDA_BASE",
                    "SLURM_ENV_SETUP", "SLURM_CPUS_PER_TASK", "SLURM_ACCOUNT",
                    "SLURM_PARTITION", "SLURM_QOS", "SLURM_GPU_TYPE",
                    "SLURM_MEMORY", "SLURM_TIME_LIMIT"):
            self.env.pop(key, None)
        self.make_tool(
            "sbatch",
            f"#!{sys.executable}\n"
            "import json, os, subprocess, sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "Path(os.environ['TEST_SBATCH_RECORD']).write_text(json.dumps(args))\n"
            "status = int(os.environ.get('TEST_SUBMIT_STATUS', '0'))\n"
            "if status:\n"
            "    print('submission rejected', file=sys.stderr)\n"
            "    sys.exit(status)\n"
            "print('12345;test-cluster', flush=True)\n"
            "if '--wait' in args:\n"
            "    env = dict(os.environ, SLURM_JOB_ID='12345')\n"
            "    for arg in args:\n"
            "        if arg.startswith('--cpus-per-task='):\n"
            "            env['SLURM_CPUS_PER_TASK'] = arg.split('=', 1)[1]\n"
            "    values = dict(arg[2:].split('=', 1) for arg in args if '=' in arg)\n"
            "    with open(values['output'].replace('%j', '12345'), 'w') as out, "
            "open(values['error'].replace('%j', '12345'), 'w') as err:\n"
            "        result = subprocess.run(['bash', *args[-3:]], env=env,\n"
            "                                cwd=values['chdir'], stdout=out, stderr=err)\n"
            "    sys.exit(result.returncode)\n",
        )
        self.make_tool(
            "srun",
            '#!/usr/bin/env bash\nset -euo pipefail\n'
            '[[ "$1" == --unbuffered && "$2" == --export=ALL ]]\n'
            'shift 2\nexec "$@"\n',
        )
        self.make_tool(
            "nvidia-smi",
            '#!/usr/bin/env bash\n'
            'echo "GPU 0: test NVIDIA GPU"\nexit "${TEST_GPU_STATUS:-0}"\n',
        )
        self.make_tool(
            "job-python",
            '#!/usr/bin/env bash\n'
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

    def sbatch_args(self):
        return json.loads(self.record.read_text())

    def test_submit_defaults_without_running_pipeline(self):
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = self.sbatch_args()
        for flag in (
            "--partition=jean-zellou", "--cpus-per-task=16", "--mem=64G",
            "--time=48:00:00", "--gres=gpu:1", "--nodes=1", "--ntasks=1",
            "--export=ALL", "--parsable",
        ):
            self.assertIn(flag, args)
        self.assertNotIn("--wait", args)
        self.assertEqual(args[-1], str(self.config))
        self.assertEqual(args[-2], str(self.pipeline_dir.parents[1]))
        self.assertIn(str(self.output / "logs/panoramax-12345.out"), result.stdout)
        self.assertTrue((self.output / "logs/submit.log").is_file())
        self.assertFalse((self.output / "pipeline.txt").exists())

    def test_wait_uses_resources_setup_conda_and_shared_paths(self):
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
        result = self.launch("--wait")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for flag in (
            "--wait", "--partition=gpu-test", "--cpus-per-task=6", "--mem=32G",
            "--time=02:30:00", "--gres=gpu:a100:1", "--account=project", "--qos=normal",
        ):
            self.assertIn(flag, self.sbatch_args())
        lines = (self.output / "pipeline.txt").read_text().splitlines()
        self.assertEqual(lines[:5], [str(self.config), str(self.root), "6", "yes", "gsplat"])
        self.assertFalse(Path(lines[5]).exists())
        self.assertIn("Job terminé avec succès", result.stdout)
        self.assertIn("GPU 0", (self.output / "logs/panoramax-12345.out").read_text())

    def test_wait_propagates_pipeline_failure(self):
        self.env["TEST_PIPELINE_STATUS"] = "7"
        result = self.launch("--wait")
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertNotIn("Job terminé avec succès", result.stdout)
        self.assertIn("Pipeline GPU en échec", (self.output / "logs/panoramax-12345.err").read_text())
        error = (self.output / "logs/panoramax-12345.err").read_text()
        self.assertIn("job 12345", error)
        self.assertIn("code 7", error)
        self.assertIn("Journaux :", error)

    def test_gpu_and_import_failures_stop_before_pipeline(self):
        for variable in ("TEST_GPU_STATUS", "TEST_IMPORT_STATUS"):
            with self.subTest(variable=variable):
                self.env[variable] = "9"
                result = self.launch("--wait")
                self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
                self.assertFalse((self.output / "pipeline.txt").exists())
                self.env.pop(variable)

    def test_submission_failure_is_not_reported_as_success(self):
        self.env["TEST_SUBMIT_STATUS"] = "3"
        result = self.launch()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("submission rejected", result.stdout + result.stderr)
        self.assertNotIn("Job soumis", result.stdout)

    def test_missing_config_and_invalid_cpu_fail_before_submission(self):
        self.config.unlink()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.record.exists())
        self.config.write_text(
            f'OUTPUT_DIR="{self.output}"\nBBOX_LAMBERT93="1,2,3,4"\n'
            'NUM_BLOCKS=2\nNUM_ITER=3\nCAP_MAX=1000\nTRAIN_PRESET=3dgs\n'
            'SLURM_CPUS_PER_TASK=0\n'
        )
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SLURM_CPUS_PER_TASK", result.stderr)
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
