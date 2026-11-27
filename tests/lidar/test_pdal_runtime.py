import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/lidar"))
import pdal_runtime


class PdalRuntimeTests(unittest.TestCase):
    def test_create_once_and_run_in_child_environment(self):
        original = dict(os.environ)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(pdal_runtime.Path, "home", return_value=Path(temporary)), \
                patch.object(pdal_runtime.shutil, "which", return_value="/bin/mamba"):
            prefix = Path(temporary) / ".cache/colmap_to_gs/lidar-pdal"

            def create(command, check):
                self.assertEqual(command[:3], ["/bin/mamba", "env", "create"])
                self.assertIn("environment/ign.slurm/pdal_env.yml", command[-1])
                (prefix / "bin").mkdir(parents=True)
                executable = prefix / "bin/pdal"
                executable.write_text("#!/bin/sh\nexit 0\n")
                executable.chmod(0o755)

            with patch.object(pdal_runtime.subprocess, "run", side_effect=create) as run:
                first = pdal_runtime.pdal_command()
                second = pdal_runtime.pdal_command()
                self.assertEqual(first, ["/bin/mamba", "run", "--prefix", str(prefix), "pdal"])
                self.assertEqual(second, first)
                self.assertEqual(run.call_count, 1)
        self.assertEqual(dict(os.environ), original)

    def test_creation_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(pdal_runtime.Path, "home", return_value=Path(temporary)), \
                patch.object(pdal_runtime.shutil, "which", return_value="/bin/mamba"), \
                patch.object(pdal_runtime.subprocess, "run",
                             side_effect=subprocess.CalledProcessError(1, ["mamba"])):
            with self.assertRaises(subprocess.CalledProcessError):
                pdal_runtime.pdal_command()

    def test_missing_manager_and_explicit_executable(self):
        with patch.object(pdal_runtime.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Mamba"):
                pdal_runtime.pdal_command()
            with self.assertRaisesRegex(ValueError, "configuré introuvable"):
                pdal_runtime.pdal_command("/custom/pdal")
        with patch.object(pdal_runtime.shutil, "which", return_value="/custom/pdal"), \
                patch.object(pdal_runtime.subprocess, "run") as run:
            self.assertEqual(pdal_runtime.pdal_command("/custom/pdal"), ["/custom/pdal"])
            run.assert_not_called()

    def test_incomplete_environment_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(pdal_runtime.Path, "home", return_value=Path(temporary)), \
                patch.object(pdal_runtime.shutil, "which", return_value="/bin/mamba"):
            prefix = Path(temporary) / ".cache/colmap_to_gs/lidar-pdal"
            prefix.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "incomplet"):
                pdal_runtime.pdal_command()
            self.assertTrue(prefix.is_dir())


if __name__ == "__main__":
    unittest.main()
