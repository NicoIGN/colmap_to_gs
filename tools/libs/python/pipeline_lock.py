#!/usr/bin/env python3
"""Own stage locks and recover only demonstrably abandoned local locks."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def process_start(pid):
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def alive(owner):
    if owner["host"] != socket.gethostname():
        raise ValueError("Verrou d'un autre hôte : reprise automatique refusée.")
    boot = Path("/proc/sys/kernel/random/boot_id")
    if boot.is_file() and owner.get("boot") != boot.read_text().strip():
        return False
    try:
        os.kill(owner["pid"], 0)
    except ProcessLookupError:
        return False
    current = process_start(owner["pid"])
    return current == owner.get("start") if current is not None else True


def list_processes():
    """Yield (pid, ppid, command, proc_dir); proc_dir is None when /proc is unavailable (macOS)."""
    if Path("/proc/self/stat").is_file():
        for process in Path("/proc").glob("[0-9]*"):
            try:
                ppid = int(Path(process / "stat").read_text().rsplit(")", 1)[1].split()[1])
                command = (process / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as error:
                raise ValueError(f"Impossible de vérifier le processus {process.name}.") from error
            yield int(process.name), ppid, command, process
        return
    output = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,command="],
                            capture_output=True, text=True, check=True).stdout
    for line in output.splitlines():
        pid, ppid, command = line.split(None, 2)
        yield int(pid), int(ppid), command, None


def legacy_active(workspace):
    processes = list(list_processes())
    parents = {pid: ppid for pid, ppid, _, _ in processes}
    ancestors = set()
    pid = os.getpid()
    while pid and pid not in ancestors:
        ancestors.add(pid)
        pid = parents.get(pid, 0)
    for pid, _, command, process in processes:
        if pid in ancestors:
            continue
        if not any(name in command for name in
                   ("images_to_3dgs", "images_to_colmap", "run_3dgs_block",
                    "run_spirula", "spirula", "colmap", "ns-train")):
            continue
        if str(workspace) in command:
            return True
        if process is None:
            continue
        try:
            environment = (process / "environ").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as error:
            raise ValueError(f"Impossible de vérifier le processus {pid}.") from error
        if f"OUTPUT_DIR={workspace}".encode() in environment:
            return True
    return False


def stage_lock(path, pid, resume, release=False, workspace=None, ignore=False):
    path = Path(path)
    if ignore and not resume and not release:
        raise ValueError("REMOVE_LOCKS=true exige PIPELINE_RESUME=true.")
    workspace = Path(workspace or path.parent).resolve()
    guards = path.parent / ".lock-guards"
    guards.mkdir(parents=True, exist_ok=True)
    with (guards / f"{path.name}.guard").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        owner_file = path / "owner.json"
        if release:
            if not path.exists():
                return
            owner = json.loads(owner_file.read_text())
            if owner["pid"] != pid or owner["host"] != socket.gethostname():
                raise ValueError(f"Propriétaire du verrou modifié : {path}")
            owner_file.unlink()
            path.rmdir()
            return
        if path.exists():
            if not resume and not ignore:
                raise ValueError(f"Étape verrouillée : {path}")
            if ignore:
                print(f"[AVERTISSEMENT] REMOVE_LOCKS=true : verrou forcé {path}. "
                      "Un autre calcul actif pourrait écrire dans les mêmes sorties.",
                      file=sys.stderr, flush=True)
                active = False
            elif owner_file.is_file():
                active = alive(json.loads(owner_file.read_text()))
                if not active:
                    active = legacy_active(workspace)
            else:
                if list(path.iterdir()):
                    raise ValueError(f"Verrou sans propriétaire avec contenu inconnu : {path}")
                active = legacy_active(workspace)
            if active:
                raise ValueError(f"Calcul encore actif : {path}")
            if any(child.name != "owner.json" for child in path.iterdir()):
                raise ValueError(f"Contenu inconnu dans le verrou : {path}")
            if owner_file.exists():
                owner_file.unlink()
            path.rmdir()
            print(f"[reprise] Verrou abandonné récupéré : {path}", flush=True)
        path.mkdir()
        boot = Path("/proc/sys/kernel/random/boot_id")
        owner_file.write_text(json.dumps({
            "pid": pid, "host": socket.gethostname(), "start": process_start(pid),
            "boot": boot.read_text().strip() if boot.is_file() else None,
        }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--resume", choices=("true", "false"), default="false")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--ignore", choices=("true", "false"), default="false")
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()
    try:
        stage_lock(args.path, args.pid, args.resume == "true", args.release,
                   args.workspace, args.ignore == "true")
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f"[ERREUR] Verrou : {error}")
