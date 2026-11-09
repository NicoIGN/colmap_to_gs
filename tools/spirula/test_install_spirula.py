import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("install_spirula.sh").resolve()


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        tools = self.root / "bin"
        tools.mkdir()
        self.log = self.root / "commands"
        self.source = self.root / "spirula studio"
        helper = self.root / "build_helper"
        helper.write_text(
            '#!/bin/bash\nset -eu\n'
            'printf "build %s\\n" "$*" >> "$COMMAND_LOG"\n'
            '[ "${BUILD_FAIL:-0}" = 0 ] || exit 7\n'
            'mkdir -p build build_vulkan\n'
            "printf '#!/bin/sh\\necho test-version\\n' > build/spirula\n"
            'chmod +x build/spirula\n'
            'cp build/spirula build_vulkan/spirula\n'
        )
        scripts = {
            "git": (
                '#!/bin/bash\nset -eu\n'
                'printf "git %s\\n" "$*" >> "$COMMAND_LOG"\n'
                'if [ "$1" = clone ]; then\n'
                '  for last; do :; done\n'
                '  mkdir -p "$last/.git"\n'
                '  cp "$BUILD_HELPER" "$last/build_develop.bash"\n'
                'elif [ "${3:-}" = status ]; then\n'
                '  printf "%s" "${DIRTY:-}"\n'
                'fi\n'
            ),
            "cmake": '#!/bin/sh\nprintf "cmake %s\\n" "$*" >> "$COMMAND_LOG"\n',
            "ninja": "#!/bin/sh\nexit 0\n",
            "curl": (
                '#!/bin/bash\nset -eu\n'
                'printf "curl %s\\n" "$*" >> "$COMMAND_LOG"\n'
                'while [ "$1" != --output ]; do shift; done\n'
                'dest="$2"\n'
                'if [ "${DOWNLOAD_FAIL:-0}" = 1 ]; then printf partial > "$dest"; exit 22; fi\n'
                'if [ "${BAD_MODEL:-0}" = 1 ]; then printf html > "$dest"; exit 0; fi\n'
                "printf '3mas' > \"$dest\"\n"
                'dd if=/dev/zero of="$dest" bs=1 seek=629145599 count=1 conv=notrunc 2>/dev/null\n'
            ),
        }
        for name, text in scripts.items():
            path = tools / name
            path.write_text(text)
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}",
                        COMMAND_LOG=str(self.log), BUILD_HELPER=str(helper))
        self.command = ["bash", str(SCRIPT), "--repo-dir", str(self.source)]

    def run_installer(self, *args, **env):
        return subprocess.run(
            self.command + list(args), cwd=self.root, env=dict(self.env, **env),
            capture_output=True, text=True, timeout=30,
        )

    def test_clone_then_reuse_and_force_clean_build(self):
        result = self.run_installer("--", "-DSS_BUILD_GUI=OFF")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("test-version", result.stdout)
        self.assertIn("TRAIN_MASKS_SPIRULA_MODEL=", result.stdout)
        self.assertIn("git clone --recurse-submodules", self.log.read_text())
        self.assertIn("-DSS_BUILD_GUI=OFF", self.log.read_text())
        model = self.source / "models" / "sam3-q4_0.ggml"
        self.assertTrue(model.is_file())
        before = self.log.read_text()
        result = self.run_installer(DIRTY=" M local.cpp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("réutilisé", result.stdout)
        self.assertEqual(self.log.read_text(), before)

        result = self.run_installer("--", "-DSS_BUILD_GUI=OFF")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--clean", result.stderr)
        result = self.run_installer("--clean", DIRTY=" M local.cpp")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Modifications locales", result.stderr)

        for directory in ("build", "build_vulkan"):
            (self.source / directory / "CMakeCache.txt").touch()
        result = self.run_installer("--clean", "--", "-DSS_BUILD_GUI=OFF")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        self.assertIn("pull --ff-only", log)
        self.assertIn("cmake --build", log)
        self.assertEqual(log.count("build -DSS_BACKEND"), 2)
        self.assertEqual(log.count("curl "), 1)
        self.assertIn(":(top,exclude)models/", log)
        result = self.run_installer("--clean", BUILD_FAIL="1")
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("Prêt", result.stdout)
        result = self.run_installer("--clean", "--", "-DSS_BACKEND=cuda")
        self.assertNotEqual(result.returncode, 0)

    def test_failed_or_invalid_download_leaves_no_model_or_temporary_file(self):
        for setting in ("DOWNLOAD_FAIL", "BAD_MODEL"):
            with self.subTest(setting=setting):
                result = self.run_installer(**{setting: "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.source / "models" / "sam3-q4_0.ggml").exists())
                self.assertEqual(list((self.source / "models").glob(".sam3-*")), [])
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.source / "models" / "sam3-q4_0.ggml").is_file())

    def test_existing_invalid_model_is_not_overwritten(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        model = self.source / "models" / "sam3-q4_0.ggml"
        model.write_bytes(b"partial")
        before = self.log.read_text()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalide ou incomplet", result.stderr)
        self.assertEqual(model.read_bytes(), b"partial")
        self.assertEqual(before, self.log.read_text())

    def test_existing_binary_without_checkout_is_reused(self):
        directory = self.source / ("build" if os.uname().sysname == "Darwin" else "build_vulkan")
        directory.mkdir(parents=True)
        binary = directory / "spirula"
        binary.write_text("#!/bin/sh\necho test-version\n")
        binary.chmod(0o755)
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("git ", self.log.read_text())
        self.assertNotIn("build -D", self.log.read_text())

    def test_real_git_excludes_only_root_models(self):
        git = shutil.which("git", path=os.defpath)
        if not git:
            self.skipTest("Git requis")
        subprocess.run([git, "init", "-q", str(self.source)], check=True)
        (self.source / "models").mkdir()
        (self.source / "models" / "sam3-q4_0.ggml").write_bytes(b"model")
        other = self.source / "other"
        other.mkdir()
        (other / "change.txt").write_text("keep")
        result = subprocess.run(
            [git, "-C", str(self.source), "status", "--porcelain",
             "--untracked-files=all", "--", ".", ":(top,exclude)models/"],
            check=True, capture_output=True, text=True,
        )
        self.assertIn("other/change.txt", result.stdout)
        self.assertNotIn("sam3-q4_0.ggml", result.stdout)


if __name__ == "__main__":
    unittest.main()
