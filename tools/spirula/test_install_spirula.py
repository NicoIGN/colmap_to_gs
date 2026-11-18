import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("install_spirula.sh").resolve()
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def write_executable(path, text):
    path.write_text(text)
    path.chmod(0o755)


@unittest.skipUnless(shutil.which("git"), "Git requis")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.build_log = self.root / "build.log"
        self.origin = self.root / "origin"
        self.source = self.root / "spirula"
        self.model = self.root / "models" / "sam3-q4_0.ggml"
        (self.root / "conda").mkdir()

        # Le test se fait passer pour macOS afin d'éviter les contrôles Linux/Vulkan.
        write_executable(self.bin / "uname", "#!/bin/sh\necho Darwin\n")
        write_executable(self.bin / "sysctl", "#!/bin/sh\necho 2\n")
        write_executable(self.bin / "curl", (
            "#!/bin/sh\n"
            'if [ "${CURL_FAIL:-0}" = 1 ]; then exit 22; fi\n'
            'while [ "$1" != -o ]; do shift; done\n'
            'echo model > "$2"\n'
        ))

        self.origin.mkdir()
        write_executable(self.origin / "build_develop.bash", (
            "#!/bin/bash\n"
            'echo "build $*" >> "$BUILD_LOG"\n'
            "mkdir -p build\n"
            "printf '#!/bin/sh\\necho test-version\\n' > build/spirula\n"
            "chmod +x build/spirula\n"
        ))
        self.git("init", "-q", cwd=self.origin)
        self.git("add", ".", cwd=self.origin)
        self.git("commit", "-qm", "initial", cwd=self.origin)
        self.git("clone", "-q", str(self.origin), str(self.source), cwd=self.root)

        self.env = dict(
            os.environ,
            **GIT_IDENTITY,
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            BUILD_LOG=str(self.build_log),
            CONDA_PREFIX=str(self.root / "conda"),
            SPIRULA_REPO_DIR=str(self.source),
            SPIRULA_SAM3_MODEL=str(self.model),
        )

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def run_installer(self, *args, **env):
        return subprocess.run(
            ["bash", str(SCRIPT), *args], cwd=self.root,
            env=dict(self.env, **env), capture_output=True, text=True, timeout=60,
        )

    def builds(self):
        if not self.build_log.exists():
            return 0
        return self.build_log.read_text().count("build ")

    def commit_upstream(self):
        (self.origin / "change.txt").write_text("new")
        self.git("add", "change.txt", cwd=self.origin)
        self.git("commit", "-qm", "change", cwd=self.origin)

    def test_default_always_rebuilds(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.builds(), 1)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.builds(), 2)

    def test_update_skips_build_when_repo_unchanged(self):
        self.assertEqual(self.run_installer().returncode, 0)
        result = self.run_installer("--update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("compilation ignorée", result.stdout)
        self.assertEqual(self.builds(), 1)

    def test_update_rebuilds_on_change_and_keeps_artifacts(self):
        self.assertEqual(self.run_installer().returncode, 0)
        marker = self.source / "build" / "marker.o"
        marker.write_text("artifact")
        self.commit_upstream()
        result = self.run_installer("--update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Dépôt modifié", result.stdout)
        self.assertEqual(self.builds(), 2)
        self.assertEqual(marker.read_text(), "artifact")

    def test_clean_removes_build_directory(self):
        self.assertEqual(self.run_installer().returncode, 0)
        marker = self.source / "build" / "marker.o"
        marker.write_text("artifact")
        result = self.run_installer("--clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.builds(), 2)

    def test_failed_model_download_leaves_no_model(self):
        result = self.run_installer(CURL_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.model.exists())


if __name__ == "__main__":
    unittest.main()
