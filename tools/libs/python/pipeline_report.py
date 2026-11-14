#!/usr/bin/env python3
"""Concurrent-safe phase timing reports for local pipelines and Slurm jobs."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import struct
import tempfile
import time
import uuid


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds")


def counters(workspace):
    stats = {}
    inputs = workspace/"inputs.json"
    if inputs.is_file():
        data = json.loads(inputs.read_text())
        if isinstance(data.get("images"), list):
            stats["selected_images"] = len(data["images"])
    manifests = [workspace/"blocks/manifest.json"]
    manifests.extend(sorted(workspace.glob("panoramax/sfm-*/blocks-*/manifest.json")))
    for manifest in manifests:
        if manifest.is_file():
            stats["blocks"] = len(json.loads(manifest.read_text())["blocks"])
    panorama_summary = workspace/"panoramax/summary.json"
    if panorama_summary.is_file():
        summary = json.loads(panorama_summary.read_text())
        if "selected" in summary:
            stats["selected_images"] = summary["selected"]
    for path in sorted(workspace.glob("*.splat")):
        if path.stat().st_size % 32 == 0:
            stats.setdefault("splats", {})[path.name] = {
                "count": path.stat().st_size//32, "size_bytes": path.stat().st_size}
    laz = workspace/"lidar/lidar.laz"
    if laz.is_file():
        stats["lidar_size_bytes"] = laz.stat().st_size
        manifest = laz.with_suffix(".laz.json")
        if manifest.is_file():
            data = json.loads(manifest.read_text())
            if "points" in data:
                stats["lidar_points"] = data["points"]
    models = [workspace/"colmap_lidar", workspace/"sfm/sparse/0"]
    models.extend(sorted(workspace.glob("panoramax/sfm-*/sparse/0")))
    for model in models:
        points = model/"points3D.bin"
        if points.is_file():
            with points.open("rb") as stream:
                header = stream.read(8)
            if len(header) == 8:
                stats.setdefault("sparse_points", {})[str(model.relative_to(workspace))] = (
                    struct.unpack("<Q", header)[0])
    return stats


def update(workspace, action, *, name=None, run_id=None, phase=None, code=0,
           parent=None, statistics=None):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace/"pipeline_report.json"
    now = time.time()
    with (workspace/".pipeline_report.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report = json.loads(path.read_text()) if path.is_file() else {
            "schema_version": 1, "timezone": "UTC", "runs": []}
        if action == "start":
            run_id = uuid.uuid4().hex
            run = {"id": run_id, "pipeline": name, "parent_id": parent or None,
                   "started_at": timestamp(now), "started_epoch": now,
                   "finished_at": None, "duration_seconds": None, "status": "running",
                   "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
                   "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
                   "phases": []}
            report["runs"].append(run)
        else:
            run = next((r for r in report["runs"] if r["id"] == run_id), None)
            if run is None:
                raise ValueError(f"Run inconnu : {run_id}")
            if run["status"] != "running":
                raise ValueError(f"Run déjà terminé : {run_id}")
            if run["phases"] and run["phases"][-1]["status"] == "running":
                previous = run["phases"][-1]
                previous.update(finished_at=timestamp(now),
                                duration_seconds=max(0., now-previous["started_epoch"]),
                                status="failed" if action == "finish" and code else "completed")
                if action == "finish":
                    previous["exit_code"] = code
            if action == "phase":
                run["phases"].append({"name": phase, "started_at": timestamp(now),
                                      "started_epoch": now, "finished_at": None,
                                      "duration_seconds": None, "status": "running"})
            elif action == "finish":
                run.update(finished_at=timestamp(now), duration_seconds=max(
                    0., now-run["started_epoch"]), exit_code=code,
                    status="failed" if code else "completed")
                run["statistics"] = counters(workspace)
                if statistics is not None:
                    run["statistics"].update(statistics)
        starts = [r["started_epoch"] for r in report["runs"]]
        report["started_at"] = timestamp(min(starts))
        report["updated_at"] = timestamp(now)
        report["finished_at"] = None if any(r["status"] == "running" for r in report["runs"]) else timestamp(now)
        report["history_span_seconds"] = max(0., now-min(starts))
        fd, temporary = tempfile.mkstemp(prefix=".pipeline_report.", dir=workspace)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(report, stream, indent=2, allow_nan=False)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return run_id


class Reporter:
    def __init__(self, workspace, name):
        self.workspace = Path(workspace)
        self.name = name
        self.run_id = None

    def __enter__(self):
        self.run_id = update(self.workspace, "start", name=self.name,
                             parent=os.environ.get("PIPELINE_REPORT_PARENT_ID"))
        return self

    def phase(self, name):
        update(self.workspace, "phase", run_id=self.run_id, phase=name)

    def __exit__(self, kind, error, traceback):
        update(self.workspace, "finish", run_id=self.run_id, code=1 if error else 0,
               statistics={"error": str(error)} if error else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "phase", "finish"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--name")
    parser.add_argument("--id")
    parser.add_argument("--phase")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--parent")
    args = parser.parse_args()
    try:
        result = update(args.workspace, args.action, name=args.name, run_id=args.id,
                        phase=args.phase, code=args.exit_code, parent=args.parent)
        if args.action == "start":
            print(result)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"[ERREUR] Rapport pipeline : {error}\n")


if __name__ == "__main__":
    main()
