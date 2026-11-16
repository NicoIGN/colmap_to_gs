import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pipeline_lock import stage_lock


class StageLockTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.lock = self.root / ".all-all.lock"

    def test_live_lock_is_never_removed_even_in_resume(self):
        stage_lock(self.lock, os.getpid(), False)
        original = (self.lock / "owner.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "encore actif"):
            stage_lock(self.lock, os.getpid(), True)
        self.assertEqual((self.lock / "owner.json").read_bytes(), original)
        stage_lock(self.lock, os.getpid(), False, release=True)
        self.assertFalse(self.lock.exists())

    def test_dead_process_lock_is_reclaimed_only_with_resume(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            stage_lock(self.lock, process.pid, False)
        finally:
            process.terminate()
            process.wait(timeout=10)
        with self.assertRaisesRegex(ValueError, "verrouillée"):
            stage_lock(self.lock, os.getpid(), False)
        with contextlib.redirect_stdout(io.StringIO()):
            stage_lock(self.lock, os.getpid(), True)
        self.assertEqual(json.loads((self.lock / "owner.json").read_text())["pid"], os.getpid())
        stage_lock(self.lock, os.getpid(), False, release=True)

    def test_explicit_remove_locks_can_override_live_owner_with_warning(self):
        stage_lock(self.lock, os.getpid(), False)
        original = (self.lock / "owner.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "exige PIPELINE_RESUME=true"):
            stage_lock(self.lock, os.getpid(), False, ignore=True)
        self.assertEqual((self.lock / "owner.json").read_bytes(), original)
        warning = io.StringIO()
        with contextlib.redirect_stderr(warning), contextlib.redirect_stdout(io.StringIO()):
            stage_lock(self.lock, os.getpid(), True, ignore=True)
        self.assertIn("REMOVE_LOCKS=true", warning.getvalue())
        self.assertIn("autre calcul actif", warning.getvalue())
        stage_lock(self.lock, os.getpid(), False, release=True)

    def test_foreign_host_and_unknown_content_are_not_deleted(self):
        stage_lock(self.lock, os.getpid(), False)
        owner_file = self.lock / "owner.json"
        owner = json.loads(owner_file.read_text())
        owner["host"] = "another-host"
        owner_file.write_text(json.dumps(owner))
        with self.assertRaisesRegex(ValueError, "autre hôte"):
            stage_lock(self.lock, os.getpid(), True)
        self.assertTrue(owner_file.exists())

    def test_dead_owner_does_not_hide_an_orphaned_native_worker(self):
        owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            stage_lock(self.lock, owner.pid, False)
        finally:
            owner.terminate()
            owner.wait(timeout=10)
        worker = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", "colmap", str(self.root)])
        try:
            with self.assertRaisesRegex(ValueError, "encore actif"):
                stage_lock(self.lock, os.getpid(), True, workspace=self.root)
        finally:
            worker.terminate()
            worker.wait(timeout=10)

    def test_empty_legacy_lock_recovered_but_not_while_native_process_is_active(self):
        self.lock.mkdir()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", "spirula", str(self.root)])
        try:
            with self.assertRaisesRegex(ValueError, "encore actif"):
                stage_lock(self.lock, os.getpid(), True, workspace=self.root)
        finally:
            process.terminate()
            process.wait(timeout=10)
        with contextlib.redirect_stdout(io.StringIO()):
            stage_lock(self.lock, os.getpid(), True, workspace=self.root)
        stage_lock(self.lock, os.getpid(), False, release=True)
        self.lock.mkdir()
        (self.lock / "unknown").touch()
        with self.assertRaisesRegex(ValueError, "contenu inconnu"):
            stage_lock(self.lock, os.getpid(), True)


if __name__ == "__main__":
    unittest.main()
