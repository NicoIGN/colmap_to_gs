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


def restore_spirula(archive, assets, binary_relative):
    """Use an immutable local extraction, without deleting an existing installation."""
    archive, assets = Path(archive), Path(assets)
    if not archive.is_file():
        raise FileNotFoundError(archive)
    digest = sha256(archive)
    destination = assets / f"spirula-{digest[:16]}"
    binary_relative = Path(binary_relative)
    if binary_relative.is_absolute() or ".." in binary_relative.parts:
        raise ValueError("Chemin binaire relatif sans '..' requis")
    binary = destination / binary_relative
    if destination.exists():
        if not binary.is_file():
            raise ValueError(f"Extraction locale incomplète : {destination}")
        binary.chmod(binary.stat().st_mode | 0o111)
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
        temporary_drive = destination.with_suffix(".zip.partial")
        try:
            shutil.copyfile(archive, temporary_drive)
            if sha256(temporary_drive) != sha256(archive):
                raise OSError(f"Vérification SHA256 COLMAP échouée : {temporary_drive}")
            temporary_drive.rename(destination)
        finally:
            temporary_drive.unlink(missing_ok=True)
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
