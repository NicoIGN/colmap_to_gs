from pathlib import Path
import json
import os
import subprocess
import tempfile
import sys
import unittest


SCRIPT = Path(__file__).with_name("install.sh")


class InstallerTest(unittest.TestCase):
    def test_existing_environment_pins_numpy_and_checks_torch_bridge(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "environment"
            runtime = target / "venv" / "bin" / "python"
            runtime.parent.mkdir(parents=True)
            log = Path(temporary) / "calls.jsonl"
            runtime.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['INSTALL_LOG'], 'a') as f:\n"
                "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if len(sys.argv) > 2 and 'torch.from_numpy' in sys.argv[2]:\n"
                "    sys.exit(int(os.environ.get('BRIDGE_FAIL', '0')))\n"
                "if sys.argv[1] == '-': sys.stdin.read()\n"
                "if len(sys.argv) > 2 and 'cuda' in sys.argv[2]: print('cpu')\n"
            )
            runtime.chmod(0o755)
            env = dict(os.environ, INSTALL_LOG=str(log))
            command = ["bash", str(SCRIPT), "--install-dir", str(target),
                       "--python", sys.executable]
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            install = next(call for call in calls if "torch>=2.1,<2.9" in call)
            self.assertIn("numpy>=1.24,<2", install)
            self.assertTrue(any("torch.from_numpy" in str(call) for call in calls))
            self.assertIn('TRAIN_MASKS_BACKEND="segmentation"', result.stdout)
            log.write_text("")
            env["BRIDGE_FAIL"] = "1"
            failed = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("pont NumPy/PyTorch", failed.stderr)
            self.assertFalse(any(json.loads(line)[0] == "-"
                                 for line in log.read_text().splitlines()))

    def test_help_does_not_install(self):
        result = subprocess.run(["bash", str(SCRIPT), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--labels", result.stdout)
        self.assertIn("segformer", result.stdout)

    def test_bad_arguments_fail_before_creating_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "environment"
            for args in (["--bad"], ["--python"], ["--python", "/missing/python"],
                         ["--model", "../evil"], ["--labels"]):
                result = subprocess.run(
                    ["bash", str(SCRIPT), "--install-dir", str(target), *args],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0, args)
                self.assertFalse(target.exists(), args)


if __name__ == "__main__":
    unittest.main()
