import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pipeline_report import Reporter, update


class PipelineReportTests(unittest.TestCase):
    def test_dates_durations_failure_and_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"inputs.json").write_text(json.dumps({"images": [1, 2, 3]}))
            (root/"scene.splat").write_bytes(bytes(64))
            with patch("pipeline_report.time.time", side_effect=[100., 101., 104., 109.]):
                run = update(root, "start", name="test")
                update(root, "phase", run_id=run, phase="prepare")
                update(root, "phase", run_id=run, phase="train")
                update(root, "finish", run_id=run, code=7)
            report = json.loads((root/"pipeline_report.json").read_text())
            data = report["runs"][0]
            self.assertEqual(data["duration_seconds"], 9)
            self.assertEqual(data["phases"][0]["duration_seconds"], 3)
            self.assertEqual(data["phases"][1]["duration_seconds"], 5)
            self.assertEqual(data["phases"][1]["status"], "failed")
            self.assertEqual(data["exit_code"], 7)
            self.assertIn("+00:00", data["started_at"])
            self.assertEqual(data["statistics"]["selected_images"], 3)
            self.assertEqual(data["statistics"]["splats"]["scene.splat"]["count"], 2)

    def test_concurrent_jobs_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(__file__).with_name("pipeline_report.py")
            jobs = [subprocess.Popen([sys.executable, str(script), "start",
                                      "--workspace", directory, "--name", f"job{i}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for i in range(8)]
            ids = []
            for job in jobs:
                stdout, stderr = job.communicate(timeout=20)
                self.assertEqual(job.returncode, 0, stderr)
                ids.append(stdout.strip())
            self.assertEqual(len(set(ids)), 8)
            report = json.loads((Path(directory)/"pipeline_report.json").read_text())
            self.assertEqual(len(report["runs"]), 8)
            for run in ids:
                update(directory, "finish", run_id=run)
            report = json.loads((Path(directory)/"pipeline_report.json").read_text())
            self.assertIsNotNone(report["finished_at"])

    def test_context_records_exception_and_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"PIPELINE_REPORT_PARENT_ID": "parent"}):
                with self.assertRaisesRegex(ValueError, "failed"):
                    with Reporter(directory, "test") as report:
                        report.phase("extraction")
                        raise ValueError("failed")
            data = json.loads((Path(directory)/"pipeline_report.json").read_text())["runs"][0]
            self.assertEqual(data["parent_id"], "parent")
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["statistics"]["error"], "failed")


if __name__ == "__main__":
    unittest.main()
