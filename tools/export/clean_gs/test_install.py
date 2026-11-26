from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("install.sh")


class InstallerTest(unittest.TestCase):
    def test_help_does_not_install(self):
        result = subprocess.run(["bash", str(SCRIPT), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--check-gpu", result.stdout)

    def test_bad_arguments_fail_before_creating_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "environment"
            for args in (["--bad"], ["--python"], ["--python", "/missing/python"]):
                result = subprocess.run(
                    ["bash", str(SCRIPT), "--install-dir", str(target), *args],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
