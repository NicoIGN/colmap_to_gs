import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("install_spirula.sh").resolve()


class InstallTest(unittest.TestCase):
    def test_clone_update_build_and_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / "bin"
            tools.mkdir()
            log = root / "commands"
            source = root / "spirula studio"
            helper = root / "build_helper"
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
                "cmake": "#!/bin/sh\nexit 0\n",
                "ninja": "#!/bin/sh\nexit 0\n",
            }
            for name, text in scripts.items():
                path = tools / name
                path.write_text(text)
                path.chmod(0o755)
            env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}",
                       COMMAND_LOG=str(log), BUILD_HELPER=str(helper))
            command = ["bash", str(SCRIPT), "--repo-dir", str(source)]

            def run(extra_env=None, extra_args=()):
                return subprocess.run(
                    command + list(extra_args), cwd=root,
                    env=dict(env, **(extra_env or {})),
                    capture_output=True, text=True,
                )

            result = run(extra_args=("--", "-DSS_BUILD_GUI=OFF"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("test-version", result.stdout)
            self.assertIn("git clone --recurse-submodules", log.read_text())
            self.assertIn("-DSS_BUILD_GUI=OFF", log.read_text())
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("pull --ff-only", log.read_text())
            before = log.read_text().count("build ")
            result = run({"DIRTY": " M local.cpp"})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Modifications locales", result.stderr)
            self.assertEqual(log.read_text().count("build "), before)
            result = run({"BUILD_FAIL": "1"})
            self.assertEqual(result.returncode, 7)
            self.assertNotIn("Prêt", result.stdout)
            result = run(extra_args=("--", "-DSS_BACKEND=cuda"))
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
