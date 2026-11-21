import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "tools/libs/scripts/sparse_density.sh"

FAKE_PYTHON = textwrap.dedent("""\
    #!/usr/bin/env bash
    echo "$@" >> "$CALL_LOG"
    case "$1" in
      *filter_sparse_density.py)
        [[ "$2" == --version ]] && { echo 2; exit 0; }
        output=""
        while (($#)); do
          [[ "$1" == --output ]] && output="$2"
          shift
        done
        mkdir -p "${output}_clean"
        echo '{}' > "${output}_clean/density_report.json"
        ;;
    esac
    """)


class SparseDensityStageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.python = self.tmp / "python"
        self.python.write_text(FAKE_PYTHON, encoding="utf-8")
        self.python.chmod(self.python.stat().st_mode | stat.S_IEXEC)
        self.log = self.tmp / "calls.log"
        self.sparse = self.tmp / "sparse"
        self.guard = self.tmp / "blocks"

    def run_stage(self, vote="0.75", guard_exists=False):
        if guard_exists:
            self.guard.mkdir(exist_ok=True)
            (self.guard / "manifest.json").write_text("{}", encoding="utf-8")
        script = (
            f'source "{HELPER}"; '
            'die() { echo "$*" >&2; exit 1; }; '
            'sparse_density_stage "$IN" "$OUT" "$GUARD"'
        )
        env = {
            **os.environ,
            "repo_root": str(REPO_ROOT),
            "PYTHON_BIN": str(self.python),
            "NUM_CPUS": "2",
            "SFM_DENSITY_VOTE_FRACTION": vote,
            "SFM_DENSITY_FAR_SUPPORT_RATE": "0.25",
            "CALL_LOG": str(self.log),
            "IN": str(self.tmp / "in"),
            "OUT": str(self.sparse),
            "GUARD": str(self.guard),
        }
        return subprocess.run(["bash", "-c", script], env=env,
                              capture_output=True, text=True)

    def filter_calls(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [line for line in lines if "filter_sparse_density.py --input" in line]

    def test_second_run_with_same_settings_reuses_cache(self):
        self.assertEqual(self.run_stage().returncode, 0)
        self.assertEqual(self.run_stage().returncode, 0)
        self.assertEqual(len(self.filter_calls()), 1)
        self.assertTrue((self.tmp / "sparse_clean/.density_done").is_file())

    def test_changed_settings_refused_when_outputs_exist(self):
        self.assertEqual(self.run_stage().returncode, 0)
        result = self.run_stage(vote="0.5", guard_exists=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Réglages ou version du filtre de densité modifiés", result.stderr)
        self.assertEqual(len(self.filter_calls()), 1)


if __name__ == "__main__":
    unittest.main()
