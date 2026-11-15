"""Local Colab execution, live logs and explicit Drive snapshots."""

import codecs
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def run_live(command, log_dir, name, *, cwd=None, env=None):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    log_path = log_dir / f"{stamp}-{name}.log"
    child_env = dict(os.environ, PYTHONUNBUFFERED="1", **(env or {}))
    print(f"\n[{name}] {shlex.join(map(str, command))}\nLog local : {log_path}", flush=True)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=cwd, env=child_env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                log.write(chunk)
                log.flush()
                sys.stdout.write(decoder.decode(chunk))
                sys.stdout.flush()
            sys.stdout.write(decoder.decode(b"", final=True))
            sys.stdout.flush()
            status = process.wait()
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise
        finally:
            process.stdout.close()
    if status:
        raise subprocess.CalledProcessError(status, command)
    return log_path


def configure_nvidia_vulkan(library_dir, work, log_dir):
    """Expose Colab's mounted NVIDIA libraries without installing a driver."""
    library_dir = Path(library_dir)
    glx = (library_dir / "libGLX_nvidia.so.0").resolve(strict=True)
    egl = (library_dir / "libEGL_nvidia.so.0").resolve(strict=True)
    destination = Path(work) / "nvidia-vulkan"
    destination.mkdir(parents=True, exist_ok=True)
    icd = destination / "nvidia_icd.json"
    egl_vendor = destination / "nvidia_egl.json"
    for path, library, api in ((icd, glx, {"api_version": "1.3.0"}),
                               (egl_vendor, egl, {})):
        path.write_text(json.dumps({
            "file_format_version": "1.0.0",
            "ICD": {"library_path": str(library), **api},
        }, indent=2))
    settings = {
        "VK_DRIVER_FILES": str(icd),
        "VK_ICD_FILENAMES": str(icd),
        "__EGL_VENDOR_LIBRARY_FILENAMES": str(egl_vendor),
    }
    log = run_live(["vulkaninfo", "--summary"], log_dir, "vulkan-nvidia", env=settings)
    text = log.read_text(errors="replace")
    if "NVIDIA" not in text or "deviceType" not in text:
        raise RuntimeError(f"GPU NVIDIA Vulkan non confirmé ; voir {log}")
    os.environ.update(settings)
    print("Vulkan NVIDIA validé ; configuration transmise aux processus suivants.", flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_conda_environment(mamba, name, prefix, yaml, log_dir):
    """Reuse a matching environment or reconcile an existing unmarked one."""
    prefix, yaml = Path(prefix), Path(yaml)
    stamp = prefix / ".colab-yaml.sha256"
    fingerprint = sha256(yaml)
    if prefix.exists():
        if not (prefix / "conda-meta/history").is_file():
            raise ValueError(f"Dossier existant sans environnement Conda : {prefix}")
        if stamp.is_file() and stamp.read_text().strip() == fingerprint:
            print(f"[Conda] Environnement inchangé réutilisé : {prefix}", flush=True)
            return
        print("[Conda] Environnement existant : synchronisation avec le YAML.", flush=True)
        command = ["env", "update"]
    else:
        command = ["create"]
    run_live([str(mamba), *command, "--yes", "--name", name, "--file", str(yaml)],
             log_dir, "conda-environment")
    stamp.write_text(fingerprint + "\n")


def _file_manifest(folder):
    return {path.relative_to(folder).as_posix(): sha256(path)
            for path in sorted(folder.rglob("*")) if path.is_file()}


def prepare_video_frames(video, frames, count, extractor, log_dir):
    """Keep verified frames on rerun instead of invoking the resetting extractor."""
    video, frames = Path(video), Path(frames)
    marker = frames.parent / ".video-frames.json"
    source = {"video_sha256": sha256(video), "num_frames": count}
    if marker.is_file():
        record = json.loads(marker.read_text())
        if record.get("source") != source or record.get("files") != _file_manifest(frames):
            raise ValueError("Vidéo, NUM_FRAMES ou frames modifiés : choisir un nouveau RUN_NAME.")
        if not record["files"]:
            raise ValueError("Extraction enregistrée vide : choisir un nouveau RUN_NAME.")
        print("[vidéo] Frames vérifiées : extraction réutilisée.", flush=True)
        return
    if frames.exists() and any(frames.iterdir()):
        inputs = frames.parent / "output/inputs.json"
        previous = json.loads(inputs.read_text()) if inputs.is_file() else {}
        signed = {image[0]: image[3] for image in previous.get("images", [])}
        if (previous.get("source") != str(frames.resolve()) or not signed
                or signed != _file_manifest(frames)):
            raise ValueError("Frames existantes non vérifiables : choisir un nouveau RUN_NAME.")
        print("[vidéo] Anciennes frames signées : vérification unique contre la vidéo.", flush=True)
        with tempfile.TemporaryDirectory(prefix="verify-frames-", dir=frames.parent) as temporary:
            run_live([*map(str, extractor), "--out", temporary, "--num-frames", str(count),
                      "--video", str(video)], log_dir, "verify-legacy-frames")
            if signed != _file_manifest(Path(temporary)):
                raise ValueError("Les frames existantes ne correspondent pas à la vidéo : "
                                 "choisir un nouveau RUN_NAME.")
        marker.write_text(json.dumps({"source": source, "files": signed}, indent=2))
        return
    frames.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="video-extract-", dir=frames.parent) as temporary:
        staged = Path(temporary) / "frames"
        staged.mkdir()
        run_live([*map(str, extractor), "--out", str(staged), "--num-frames", str(count),
                  "--video", str(video)], log_dir, "video-frames")
        files = _file_manifest(staged)
        if not files:
            raise ValueError(f"Aucune frame extraite : {frames}")
        frames.rmdir()
        staged.rename(frames)
    marker.write_text(json.dumps({"source": source, "files": files}, indent=2))


def restore_colmap(archive, run_dir, validator, log_dir):
    """Import one immutable COLMAP dataset and seed only a fresh SfM workspace."""
    archive, run_dir = Path(archive), Path(run_dir)
    digest = sha256(archive)
    destination = run_dir / "colmap-input"
    output = run_dir / "output"
    marker = run_dir / ".colmap-import.json"
    if marker.is_file():
        record = json.loads(marker.read_text())
        if record.get("archive_sha256") != digest:
            raise ValueError("ZIP COLMAP modifié : choisir un nouveau RUN_NAME.")
        if record.get("files") != _file_manifest(destination):
            raise ValueError("Import COLMAP local modifié ou incomplet : choisir un nouveau RUN_NAME.")
        print("[COLMAP] Import local vérifié et réutilisé.", flush=True)
    else:
        if destination.exists() or any((output / name).exists() for name in
                                     ("inputs.json", "images", "sfm", "blocks", "gs", "gs-nerfstudio")):
            raise ValueError("Workspace déjà utilisé sans cet import : choisir un nouveau RUN_NAME.")
        run_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="colmap-import-", dir=run_dir) as temporary:
            temporary = Path(temporary)
            local_archive = temporary / "colmap.zip"
            print("[COLMAP] Copie du ZIP Drive vers le disque local...", flush=True)
            shutil.copyfile(archive, local_archive)
            if sha256(local_archive) != digest:
                raise OSError("Copie du ZIP COLMAP non conforme.")
            root = temporary / "extracted"
            root.mkdir()
            with zipfile.ZipFile(local_archive) as zipped:
                names = set()
                for entry in zipped.infolist():
                    path = (root / entry.filename).resolve()
                    if (not path.is_relative_to(root.resolve()) or path in names
                            or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                        raise ValueError(f"Entrée ZIP non sûre : {entry.filename}")
                    names.add(path)
                zipped.extractall(root)
            candidates = [path for path in root.rglob("sparse/0")
                          if (path.parent.parent / "images").is_dir()
                          and all((path / f"{name}.bin").is_file()
                                  for name in ("cameras", "images", "points3D"))]
            if len(candidates) != 1:
                raise ValueError("ZIP COLMAP : un unique sparse/0 binaire avec images/ est requis.")
            model = candidates[0]
            images = model.parent.parent / "images"
            run_live([*map(str, validator), str(model), "--images", str(images)],
                     log_dir, "colmap-import-validation")
            payload = temporary / "payload"
            shutil.copytree(model, payload / "sparse/0")
            shutil.copytree(images, payload / "images")
            record = {"archive_sha256": digest, "files": _file_manifest(payload)}
            payload.rename(destination)
            marker.write_text(json.dumps(record, indent=2))
    model = destination / "sparse/0"
    images = destination / "images"
    run_live([*map(str, validator), str(model), "--images", str(images)],
             log_dir, "colmap-resume-validation")
    target = output / "sfm/sparse/0"
    if target.exists():
        if _file_manifest(target) != _file_manifest(model):
            raise ValueError("Sparse du workspace différent du ZIP : choisir un nouveau RUN_NAME.")
    else:
        if (output / "inputs.json").exists():
            raise ValueError("Sparse importé supprimé après préparation : choisir un nouveau RUN_NAME.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="colmap-seed-", dir=target.parent) as temporary:
            staged = Path(temporary) / "0"
            shutil.copytree(model, staged)
            staged.rename(target)
    return images


def restore_spirula(archive, assets, binary_relative, existing_root=None):
    """Use an immutable local extraction, without deleting an existing installation."""
    binary_relative = Path(binary_relative)
    if binary_relative.is_absolute() or ".." in binary_relative.parts:
        raise ValueError("Chemin binaire relatif sans '..' requis")
    if existing_root:
        root = Path(existing_root).resolve()
        binary = root / binary_relative
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError(f"Binaire Spirula local absent ou non exécutable : {binary}")
        print(f"[Spirula] Installation locale réutilisée : {root}", flush=True)
        return root, binary
    archive, assets = Path(archive), Path(assets)
    if not archive.is_file():
        raise FileNotFoundError(archive)
    digest = sha256(archive)
    destination = assets / f"spirula-{digest[:16]}"
    binary = destination / binary_relative
    if destination.exists():
        if not binary.is_file():
            raise ValueError(f"Extraction locale incomplète : {destination}")
        binary.chmod(binary.stat().st_mode | 0o111)
        print(f"[Spirula] Extraction existante réutilisée : {destination}", flush=True)
        return destination, binary
    assets.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="spirula-extract-", dir=assets) as temporary:
        root = (Path(temporary) / "extracted").resolve()
        root.mkdir()
        local_archive = Path(temporary) / "spirula.zip"
        print("[Spirula] Copie de l'archive vers le disque local...", flush=True)
        shutil.copyfile(archive, local_archive)
        if sha256(local_archive) != digest:
            raise OSError("Copie de l'archive Spirula non conforme")
        print("[Spirula] Extraction locale...", flush=True)
        with zipfile.ZipFile(local_archive) as zipped:
            for entry in zipped.infolist():
                path = (root / entry.filename).resolve()
                if not path.is_relative_to(root) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"Entrée ZIP non sûre : {entry.filename}")
            zipped.extractall(root)
        candidates = [p for p in root.rglob(binary_relative.name)
                      if p.is_file() and p.as_posix().endswith("/"+binary_relative.as_posix())]
        if len(candidates) != 1:
            raise ValueError(f"Un unique {binary_relative} attendu ; trouvé : {len(candidates)}")
        source = candidates[0]
        for _ in binary_relative.parts:
            source = source.parent
        source.rename(destination)
    binary.chmod(binary.stat().st_mode | 0o111)
    return destination, binary


def _copy_verified_zip(archive, destination):
    temporary_drive = destination.with_suffix(".zip.partial")
    try:
        shutil.copyfile(archive, temporary_drive)
        if sha256(temporary_drive) != sha256(archive):
            raise OSError(f"Vérification SHA256 ZIP échouée : {temporary_drive}")
        temporary_drive.rename(destination)
    finally:
        temporary_drive.unlink(missing_ok=True)


def save_results_zip(output_dir, drive_dir, splat_name):
    """Export only the final splat and its self-contained georeferencing metadata."""
    output_dir, drive_dir = Path(output_dir).resolve(), Path(drive_dir).resolve()
    if Path(splat_name).name != splat_name or not splat_name.endswith(".splat"):
        raise ValueError("Le résultat doit être un simple nom de fichier .splat")
    splat = output_dir / splat_name
    sources = [splat]
    sidecar = splat.with_suffix(".georef.json")
    if sidecar.is_file():
        sources.append(sidecar)
        reference = json.loads(sidecar.read_text())
        for key in ("georef_summary", "source_gpx"):
            if key in reference:
                relative = Path(reference[key])
                path = output_dir / relative
                if relative.is_absolute() or not path.resolve().is_relative_to(output_dir):
                    raise ValueError(f"Référence géographique hors des résultats : {relative}")
                sources.append(path)
    elif (output_dir / "georef").exists():
        raise ValueError(f"Géoréférencement final absent : {sidecar}")
    for path in sources:
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Résultat absent ou incomplet : {path}")
        if not path.resolve().is_relative_to(output_dir):
            raise ValueError(f"Résultat hors du dossier de sortie : {path}")
    if drive_dir == output_dir or drive_dir.is_relative_to(output_dir):
        raise ValueError("La destination doit être hors des résultats locaux")
    drive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    destination = drive_dir / f"{output_dir.parent.name}-3dgs-{stamp}.zip"
    print(f"[3DGS] Création locale du ZIP final ; destination Drive : {destination}", flush=True)
    with tempfile.TemporaryDirectory(prefix="results-export-", dir=output_dir.parent) as temporary:
        archive = Path(temporary) / destination.name
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=1) as zipped:
            for path in sources:
                zipped.write(path, path.relative_to(output_dir).as_posix())
        _copy_verified_zip(archive, destination)
    print(f"[3DGS] ZIP final vérifié : {destination}", flush=True)
    return destination


def save_colmap_zip(output_dir, drive_dir):
    """Export the global COLMAP model and images, not training checkpoints."""
    output_dir, drive_dir = Path(output_dir).resolve(), Path(drive_dir).resolve()
    model = output_dir / "sfm/sparse/0"
    images = output_dir / "images"
    complete = any(all((model / f"{name}.{extension}").is_file()
                       and (model / f"{name}.{extension}").stat().st_size
                       for name in ("cameras", "images", "points3D"))
                   for extension in ("bin", "txt"))
    if not complete:
        raise ValueError(f"Modèle COLMAP absent ou incomplet : {model}")
    if not images.is_dir() or not any(path.is_file() for path in images.rglob("*")):
        raise ValueError(f"Images COLMAP absentes : {images}")
    for source in (model, images):
        if drive_dir == source.resolve() or drive_dir.is_relative_to(source.resolve()):
            raise ValueError("La destination doit être hors des données COLMAP")
    drive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    destination = drive_dir / f"{output_dir.parent.name}-colmap-{stamp}.zip"
    print(f"[COLMAP] Création locale du ZIP ; destination Drive : {destination}", flush=True)
    with tempfile.TemporaryDirectory(prefix="colmap-export-", dir=output_dir.parent) as temporary:
        archive = Path(temporary) / destination.name
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=1) as zipped:
            for source, prefix in ((model, Path("sparse/0")), (images, Path("images"))):
                for path in sorted(source.rglob("*")):
                    if path.is_file():
                        zipped.write(path, (prefix / path.relative_to(source)).as_posix())
                    elif path.is_symlink():
                        raise ValueError(f"Lien non exportable : {path}")
        _copy_verified_zip(archive, destination)
    print(f"[COLMAP] ZIP vérifié : {destination}", flush=True)
    return destination


def save_snapshot(run_dir, drive_dir):
    """Archive stopped local work into one Drive file, without replacing prior snapshots."""
    run_dir, drive_dir = Path(run_dir).resolve(), Path(drive_dir).resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(run_dir)
    if drive_dir == run_dir or drive_dir.is_relative_to(run_dir):
        raise ValueError("La destination de sauvegarde doit être hors du run local")
    for path in run_dir.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(run_dir):
            raise ValueError(f"Lien hors du run local : {path}")
    drive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    destination = drive_dir / f"{run_dir.name}-{stamp}.tar.gz"
    print(f"[sauvegarde] Archivage local de {run_dir} ; puis copie unique vers Drive.", flush=True)
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=run_dir.parent) as temporary:
        archive = Path(temporary) / destination.name
        with tarfile.open(archive, "w:gz", compresslevel=1, dereference=True) as tar:
            tar.add(run_dir, arcname=run_dir.name)
        temporary_drive = destination.with_suffix(destination.suffix+".partial")
        try:
            shutil.copyfile(archive, temporary_drive)
            if sha256(temporary_drive) != sha256(archive):
                raise OSError(f"Vérification SHA256 de sauvegarde échouée : {temporary_drive}")
            temporary_drive.rename(destination)
        finally:
            temporary_drive.unlink(missing_ok=True)
    print(f"[sauvegarde] Vérifiée : {destination} ({destination.stat().st_size/2**20:.1f} Mio)", flush=True)
    return destination
