"""Run native PDAL in an isolated Conda environment without changing the caller."""

import fcntl
import os
from pathlib import Path
import shutil
import subprocess


def pdal_command(executable="pdal"):
    if executable != "pdal":
        if not shutil.which(executable):
            raise ValueError(f"PDAL configuré introuvable : {executable}")
        return [executable]
    manager = next((path for name in ("mamba", "micromamba", "conda")
                    if (path := shutil.which(name))), None)
    if manager is None:
        raise ValueError("Mamba, micromamba ou conda requis pour créer l'environnement lidar-pdal")
    prefix = Path.home() / ".cache/colmap_to_gs/lidar-pdal"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    definition = Path(__file__).resolve().parents[2] / "environment/ign.slurm/pdal_env.yml"
    # Several Slurm jobs may request their first extraction at the same time.
    with prefix.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (prefix / "bin/pdal").is_file():
            if prefix.exists():
                raise ValueError(f"Environnement PDAL incomplet : {prefix}. "
                                 "Le déplacer avant de relancer ; aucune suppression automatique.")
            print(f"[PDAL] Création de l'environnement dédié : {prefix}", flush=True)
            subprocess.run([manager, "env", "create", "--yes", "--prefix", str(prefix),
                            "--file", str(definition)], check=True)
        if not os.access(prefix / "bin/pdal", os.X_OK):
            raise ValueError(f"Exécutable PDAL absent ou non exécutable : {prefix / 'bin/pdal'}")
    return [manager, "run", "--prefix", str(prefix), "pdal"]
