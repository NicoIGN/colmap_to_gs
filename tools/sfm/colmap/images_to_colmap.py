#!/usr/bin/env python3
"""
Reconstruit un modèle COLMAP depuis un simple dossier d'images.

Aucun fichier .CON ni aucune géoréférenciation n'est requis.

Exemples :
    python run_colmap_from_images.py --image_dir /my_folder
    python run_colmap_from_images.py --image_dir /my_folder --output_dir /out --matcher sequential
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}


def log(message):
    print(message, flush=True)


def run_command(command, label, timeout, verbose=False, check=True):
    command = [str(v) for v in command]
    log(f"\n[{label}] " + " ".join(command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if verbose:
        if result.stdout:
            log(result.stdout[-5000:])
        if result.stderr:
            log(result.stderr[-5000:])

    if check and result.returncode != 0:
        output = result.stderr or result.stdout or "Aucune sortie"
        raise RuntimeError(
            f"{label} a échoué (code {result.returncode}):\n{output[-4000:]}"
        )

    return result


def check_colmap_installed(colmap_executable):
    try:
        result = subprocess.run(
            [colmap_executable, "help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def colmap_gpu_seems_available(colmap_executable, verbose=False):
    """
    Heuristique simple :
    - si colmap n'existe pas -> False
    - si on peut lancer 'colmap feature_extractor -h' -> on ne sait pas vraiment
      si le GPU marchera, mais on laisse tenter
    - sur macOS, le GPU COLMAP/CUDA est souvent indisponible -> fallback CPU recommandé
    """
    try:
        result = subprocess.run(
            [colmap_executable, "feature_extractor", "-h"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
    except Exception:
        return False

    if sys.platform == "darwin":
        log("[GPU] macOS détecté : fallback prudent vers CPU par défaut pour le matching COLMAP")
        return False

    return True


def find_images(image_dir):
    image_dir = Path(image_dir).resolve()

    if not image_dir.exists():
        raise FileNotFoundError(f"Le dossier d'images n'existe pas : {image_dir}")
    if not image_dir.is_dir():
        raise NotADirectoryError(f"Le chemin n'est pas un dossier : {image_dir}")

    images = [
        p
        for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(images)


def prepare_images(image_dir, output_dir):
    source_dir = Path(image_dir).resolve()
    output_images_dir = Path(output_dir).resolve() / "images"
    output_images_dir.mkdir(parents=True, exist_ok=True)

    images = find_images(source_dir)
    if not images:
        raise RuntimeError(
            f"Aucune image trouvée dans {source_dir}\n"
            f"Extensions supportées : {sorted(IMAGE_EXTENSIONS)}"
        )

    for image in images:
        destination = output_images_dir / image.name
        if not destination.exists():
            shutil.copy2(image, destination)

    log(f"[IMAGES] {len(images)} images copiées vers {output_images_dir}")
    return output_images_dir, len(images)


def run_feature_extraction(
    colmap_executable,
    database_path,
    image_dir,
    camera_model,
    single_camera,
    use_gpu,
    max_image_size,
    verbose,
):
    log("\n[FEATURES] Extraction des features SIFT...")
    command = [
        colmap_executable,
        "feature_extractor",
        f"--database_path={database_path}",
        f"--image_path={image_dir}",
        f"--ImageReader.camera_model={camera_model}",
        f"--ImageReader.single_camera={1 if single_camera else 0}",
        f"--SiftExtraction.use_gpu={1 if use_gpu else 0}",
        f"--SiftExtraction.max_image_size={max_image_size}",
    ]
    run_command(command, "FEATURES", timeout=4 * 60 * 60, verbose=verbose)
    log(f"[OK] Extraction des features terminée (GPU={use_gpu})")


def build_matcher_command(colmap_executable, database_path, matcher, use_gpu):
    if matcher == "exhaustive":
        matcher_command = "exhaustive_matcher"
    elif matcher == "sequential":
        matcher_command = "sequential_matcher"
    elif matcher == "spatial":
        matcher_command = "spatial_matcher"
    elif matcher == "vocab_tree":
        matcher_command = "vocab_tree_matcher"
    else:
        raise ValueError(
            f"Matcher inconnu : {matcher}. "
            "Choix possibles : exhaustive, sequential, spatial, vocab_tree."
        )

    return [
        colmap_executable,
        matcher_command,
        f"--database_path={database_path}",
        f"--SiftMatching.use_gpu={1 if use_gpu else 0}",
    ]


def run_feature_matching_with_fallback(
    colmap_executable,
    database_path,
    matcher,
    preferred_gpu,
    verbose,
):
    attempts = []

    if matcher == "sequential":
        attempts = [
            ("sequential", preferred_gpu),
            ("sequential", False),
            ("exhaustive", False),
        ]
    else:
        attempts = [
            (matcher, preferred_gpu),
            (matcher, False),
        ]

    last_error = None

    for current_matcher, current_gpu in attempts:
        label = f"MATCHING {current_matcher} GPU={current_gpu}"
        log(f"\n[MATCHING] Tentative avec matcher={current_matcher}, GPU={current_gpu}...")

        command = build_matcher_command(
            colmap_executable=colmap_executable,
            database_path=database_path,
            matcher=current_matcher,
            use_gpu=current_gpu,
        )

        result = run_command(
            command,
            label=label,
            timeout=12 * 60 * 60,
            verbose=verbose,
            check=False,
        )

        if result.returncode == 0:
            log(f"[OK] Appariement terminé avec matcher={current_matcher}, GPU={current_gpu}")
            return current_matcher, current_gpu

        output = result.stderr or result.stdout or "Aucune sortie"
        last_error = RuntimeError(
            f"{label} a échoué (code {result.returncode}):\n{output[-4000:]}"
        )
        log(f"[WARN] Échec du matching avec matcher={current_matcher}, GPU={current_gpu}")
        log(output[-2000:])

    raise last_error if last_error else RuntimeError("Le matching a échoué sans message exploitable")


def run_mapper(colmap_executable, database_path, image_dir, sparse_root, verbose):
    sparse_root = Path(sparse_root)
    sparse_root.mkdir(parents=True, exist_ok=True)

    log("\n[MAPPER] Reconstruction SfM incrémentale...")
    command = [
        colmap_executable,
        "mapper",
        f"--database_path={database_path}",
        f"--image_path={image_dir}",
        f"--output_path={sparse_root}",
    ]
    run_command(command, "MAPPER", timeout=24 * 60 * 60, verbose=verbose)
    log("[OK] Reconstruction SfM terminée")


def read_number_of_images_from_bin(images_bin):
    images_bin = Path(images_bin)
    if not images_bin.exists():
        return 0
    with open(images_bin, "rb") as file:
        raw = file.read(8)
    if len(raw) != 8:
        return 0
    return int.from_bytes(raw, byteorder="little", signed=False)


def find_mapper_models(sparse_root):
    sparse_root = Path(sparse_root)
    models = []
    for directory in sorted(sparse_root.iterdir()):
        if not directory.is_dir():
            continue
        required_files = [
            directory / "cameras.bin",
            directory / "images.bin",
            directory / "points3D.bin",
        ]
        if all(path.exists() for path in required_files):
            models.append(directory)
    return models


def select_best_model(sparse_root):
    sparse_root = Path(sparse_root)
    models = find_mapper_models(sparse_root)
    if not models:
        raise RuntimeError(f"Aucun modèle COLMAP valide trouvé dans {sparse_root}")

    model_sizes = {}
    for model in models:
        images_bin = model / "images.bin"
        n_images = read_number_of_images_from_bin(images_bin)
        model_sizes[model] = n_images
        log(f"[MAPPER] Modèle {model.name}: {n_images} images enregistrées")

    best_model = max(model_sizes, key=model_sizes.get)
    target_model = sparse_root / "0"

    if best_model != target_model:
        log(f"[MAPPER] Sélection du meilleur modèle : {best_model.name} -> 0")
        temp_model = sparse_root / "_temporary_model"

        if temp_model.exists():
            shutil.rmtree(temp_model)

        if target_model.exists():
            target_model.rename(temp_model)

        best_model.rename(target_model)

        if temp_model.exists():
            shutil.rmtree(temp_model)

    return target_model, model_sizes[best_model]


def run_bundle_adjustment(colmap_executable, model_path, verbose):
    model_path = Path(model_path)
    log("\n[BUNDLE ADJUSTMENT] Optimisation supplémentaire...")
    output_path = model_path.parent / "_bundle_adjusted"

    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    command = [
        colmap_executable,
        "bundle_adjuster",
        f"--input_path={model_path}",
        f"--output_path={output_path}",
    ]

    try:
        run_command(command, "BUNDLE_ADJUSTMENT", timeout=6 * 60 * 60, verbose=verbose)
        if not any(output_path.iterdir()):
            raise RuntimeError("Le bundle adjustment n'a généré aucun fichier")

        for file in list(model_path.iterdir()):
            if file.is_dir():
                shutil.rmtree(file)
            else:
                file.unlink()

        for file in output_path.iterdir():
            destination = model_path / file.name
            if file.is_dir():
                shutil.copytree(file, destination)
            else:
                shutil.copy2(file, destination)

        shutil.rmtree(output_path)
        log("[OK] Bundle adjustment terminé")

    except Exception as error:
        log(f"[WARNING] Bundle adjustment non critique échoué:\n{error}")
        if output_path.exists():
            shutil.rmtree(output_path)


def convert_model_to_txt(colmap_executable, sparse_model_dir, sparse_txt_dir, verbose):
    sparse_model_dir = Path(sparse_model_dir)
    sparse_txt_dir = Path(sparse_txt_dir)

    if sparse_txt_dir.exists():
        shutil.rmtree(sparse_txt_dir)

    sparse_txt_dir.mkdir(parents=True, exist_ok=True)

    log("\n[CONVERSION] Conversion du modèle BIN vers TXT...")
    command = [
        colmap_executable,
        "model_converter",
        f"--input_path={sparse_model_dir}",
        f"--output_path={sparse_txt_dir}",
        "--output_type=TXT",
    ]
    run_command(command, "CONVERSION", timeout=10 * 60, verbose=verbose)

    expected = [
        sparse_txt_dir / "cameras.txt",
        sparse_txt_dir / "images.txt",
        sparse_txt_dir / "points3D.txt",
    ]
    missing = [str(p) for p in expected if not p.exists()]

    if missing:
        raise RuntimeError("Conversion TXT incomplète :\n" + "\n".join(missing))

    log(f"[OK] Modèle TXT écrit dans {sparse_txt_dir}")
    return sparse_txt_dir


def read_points3d_txt(points3d_txt):
    points3d_txt = Path(points3d_txt)
    xyz = []
    rgb = []

    with open(points3d_txt, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            if len(tokens) < 7:
                continue
            try:
                xyz.append([float(tokens[1]), float(tokens[2]), float(tokens[3])])
                rgb.append([int(tokens[4]), int(tokens[5]), int(tokens[6])])
            except ValueError:
                continue

    if not xyz:
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0, 3), dtype=np.uint8)

    return np.asarray(xyz, dtype=np.float64), np.asarray(rgb, dtype=np.uint8)


def summarize_points3d(xyz, label):
    xyz = np.asarray(xyz, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError(f"{label}: xyz doit avoir la forme (N, 3), reçu {xyz.shape}")

    valid_mask = np.isfinite(xyz).all(axis=1)
    valid_xyz = xyz[valid_mask]

    log(f"[POINTS3D][{label}] total={len(xyz)} valid={len(valid_xyz)} invalid={len(xyz) - len(valid_xyz)}")

    if len(valid_xyz) == 0:
        log(f"[POINTS3D][{label}] Aucun point valide")
        return

    minimum = valid_xyz.min(axis=0)
    maximum = valid_xyz.max(axis=0)
    mean = valid_xyz.mean(axis=0)
    diagonal = np.linalg.norm(maximum - minimum)

    log(f"[POINTS3D][{label}] bbox min={minimum}")
    log(f"[POINTS3D][{label}] bbox max={maximum}")
    log(f"[POINTS3D][{label}] bbox diag={diagonal:.6f}")
    log(f"[POINTS3D][{label}] mean={mean}")


def write_ply_xyzrgb(output_ply, xyz, rgb, verbose=True):
    output_ply = Path(output_ply)
    output_ply.parent.mkdir(parents=True, exist_ok=True)

    xyz = np.asarray(xyz, dtype=np.float64)
    rgb = np.asarray(rgb)

    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError(f"xyz doit avoir la forme (N, 3), reçu {xyz.shape}")
    if rgb.ndim != 2 or rgb.shape[1] != 3:
        raise ValueError(f"rgb doit avoir la forme (N, 3), reçu {rgb.shape}")
    if len(xyz) != len(rgb):
        raise ValueError(f"xyz et rgb ont des tailles différentes : {len(xyz)} contre {len(rgb)}")

    finite_mask = np.isfinite(xyz).all(axis=1)
    xyz = xyz[finite_mask]
    rgb = rgb[finite_mask]
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)

    if len(xyz) == 0:
        raise RuntimeError("Aucun point valide à écrire dans sparse_pc.ply")

    iterable = zip(xyz, rgb)
    if verbose and tqdm is not None:
        iterable = tqdm(iterable, total=len(xyz), desc="Écriture sparse_pc.ply", unit="pt")

    with open(output_ply, "w", encoding="utf-8") as file:
        file.write("ply\n")
        file.write("format ascii 1.0\n")
        file.write(f"element vertex {len(xyz)}\n")
        file.write("property float x\n")
        file.write("property float y\n")
        file.write("property float z\n")
        file.write("property uchar red\n")
        file.write("property uchar green\n")
        file.write("property uchar blue\n")
        file.write("end_header\n")

        for point, color in iterable:
            file.write(
                f"{float(point[0]):.12f} "
                f"{float(point[1]):.12f} "
                f"{float(point[2]):.12f} "
                f"{int(color[0])} {int(color[1])} {int(color[2])}\n"
            )

    log(f"[OK] PLY écrit : {output_ply}")
    log(f"[PLY] Nombre de points : {len(xyz)}")


def export_sparse_ply(sparse_txt_dir, output_ply, verbose):
    points3d_txt = Path(sparse_txt_dir) / "points3D.txt"

    if not points3d_txt.exists():
        raise RuntimeError(f"Fichier absent : {points3d_txt}")

    xyz, rgb = read_points3d_txt(points3d_txt)
    summarize_points3d(xyz, "colmap")

    if len(xyz) == 0:
        raise RuntimeError(
            "COLMAP n'a généré aucun point 3D.\n"
            "Causes possibles :\n"
            "- images sans recouvrement ;\n"
            "- trop peu de features communes ;\n"
            "- mauvais choix de matcher ;\n"
            "- images trop différentes ;\n"
            "- reconstruction impossible."
        )

    write_ply_xyzrgb(output_ply, xyz, rgb, verbose=verbose)


def build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Reconstruit un modèle COLMAP depuis un dossier d'images."
    )

    parser.add_argument("--image_dir", "--image-dir", required=True, help="Dossier contenant les images.")
    parser.add_argument("--output_dir", "--output-dir", default=None, help="Dossier de sortie. Par défaut : <image_dir>_colmap.")
    parser.add_argument("--colmap", default="colmap", help="Exécutable COLMAP à utiliser.")
    parser.add_argument(
        "--matcher",
        choices=["exhaustive", "sequential", "spatial", "vocab_tree"],
        default="exhaustive",
        help="Type d'appariement.",
    )
    parser.add_argument(
        "--camera_model",
        "--camera-model",
        choices=[
            "SIMPLE_PINHOLE",
            "PINHOLE",
            "SIMPLE_RADIAL",
            "RADIAL",
            "OPENCV",
            "FULL_OPENCV",
            "SIMPLE_RADIAL_FISHEYE",
            "RADIAL_FISHEYE",
            "OPENCV_FISHEYE",
            "THIN_PRISM_FISHEYE",
        ],
        default="SIMPLE_RADIAL",
        help="Modèle caméra COLMAP.",
    )
    parser.add_argument("--separate_cameras", "--separate-cameras", action="store_true", help="Utilise une caméra différente par image.")
    parser.add_argument("--max_image_size", "--max-image-size", type=int, default=3200, help="Taille maximale des images pour SIFT.")
    parser.add_argument("--no_gpu", "--no-gpu", action="store_true", help="Force le CPU.")
    parser.add_argument("--bundle_adjustment", "--bundle-adjustment", action="store_true", help="Lance un bundle adjustment supplémentaire après le mapper.")
    parser.add_argument("--verbose", action="store_true", help="Affiche davantage de sorties COLMAP.")

    return parser


def main():
    parser = build_argument_parser()
    args = parser.parse_args()

    image_dir = Path(args.image_dir).resolve()

    if args.output_dir is None:
        output_dir = image_dir.parent / f"{image_dir.name}_colmap"
    else:
        output_dir = Path(args.output_dir).resolve()

    colmap_executable = args.colmap
    single_camera = not args.separate_cameras

    if args.no_gpu:
        use_gpu = False
        log("[GPU] --no-gpu activé : utilisation CPU forcée")
    else:
        use_gpu = colmap_gpu_seems_available(colmap_executable, verbose=args.verbose)
        log(f"[GPU] Utilisation GPU initiale : {use_gpu}")

    log("==============================")
    log("🚀 COLMAP FROM IMAGES")
    log("==============================")
    log(f"image_dir       : {image_dir}")
    log(f"output_dir      : {output_dir}")
    log(f"colmap          : {colmap_executable}")
    log(f"matcher         : {args.matcher}")
    log(f"camera_model    : {args.camera_model}")
    log(f"single_camera   : {single_camera}")
    log(f"GPU initial     : {use_gpu}")
    log(f"max_image_size  : {args.max_image_size}")
    log(f"verbose         : {args.verbose}")

    if not check_colmap_installed(colmap_executable):
        raise RuntimeError(
            f"COLMAP est introuvable ou inutilisable : {colmap_executable}\n"
            f"Vérifie avec : which {colmap_executable}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    colmap_dir = output_dir / "colmap"
    database_path = colmap_dir / "database.db"
    sparse_root = colmap_dir / "sparse"
    sparse_txt_root = colmap_dir / "sparse_txt"
    output_ply = output_dir / "sparse_pc.ply"

    colmap_dir.mkdir(parents=True, exist_ok=True)

    output_images_dir, n_images = prepare_images(image_dir=image_dir, output_dir=output_dir)

    if n_images < 2:
        raise RuntimeError("Il faut au moins 2 images pour une reconstruction COLMAP.")

    if database_path.exists():
        log(f"[CLEANUP] Suppression de l'ancienne base : {database_path}")
        database_path.unlink()

    if sparse_root.exists():
        log(f"[CLEANUP] Suppression de l'ancien modèle : {sparse_root}")
        shutil.rmtree(sparse_root)

    if sparse_txt_root.exists():
        log(f"[CLEANUP] Suppression de l'ancien modèle TXT : {sparse_txt_root}")
        shutil.rmtree(sparse_txt_root)

    if output_ply.exists():
        log(f"[CLEANUP] Suppression de l'ancien PLY : {output_ply}")
        output_ply.unlink()

    run_feature_extraction(
        colmap_executable=colmap_executable,
        database_path=database_path,
        image_dir=output_images_dir,
        camera_model=args.camera_model,
        single_camera=single_camera,
        use_gpu=use_gpu,
        max_image_size=args.max_image_size,
        verbose=args.verbose,
    )

    used_matcher, used_gpu_for_matching = run_feature_matching_with_fallback(
        colmap_executable=colmap_executable,
        database_path=database_path,
        matcher=args.matcher,
        preferred_gpu=use_gpu,
        verbose=args.verbose,
    )

    sparse_root.mkdir(parents=True, exist_ok=True)

    run_mapper(
        colmap_executable=colmap_executable,
        database_path=database_path,
        image_dir=output_images_dir,
        sparse_root=sparse_root,
        verbose=args.verbose,
    )

    sparse_model_dir, registered_images = select_best_model(sparse_root=sparse_root)

    log(f"[MAPPER] Meilleur modèle : {sparse_model_dir}")
    log(f"[MAPPER] Images enregistrées : {registered_images}/{n_images}")

    if registered_images < 2:
        raise RuntimeError("COLMAP n'a enregistré qu'une seule image ou aucune image.")

    if args.bundle_adjustment:
        run_bundle_adjustment(
            colmap_executable=colmap_executable,
            model_path=sparse_model_dir,
            verbose=args.verbose,
        )

    sparse_txt_dir = sparse_txt_root / "0"
    convert_model_to_txt(
        colmap_executable=colmap_executable,
        sparse_model_dir=sparse_model_dir,
        sparse_txt_dir=sparse_txt_dir,
        verbose=args.verbose,
    )

    export_sparse_ply(
        sparse_txt_dir=sparse_txt_dir,
        output_ply=output_ply,
        verbose=True,
    )

    log("\n==============================")
    log("✅ RECONSTRUCTION TERMINÉE")
    log("==============================")
    log(f"Images copiées     : {output_images_dir}")
    log(f"Base COLMAP        : {database_path}")
    log(f"Matcher utilisé    : {used_matcher}")
    log(f"GPU pour matching  : {used_gpu_for_matching}")
    log(f"Modèle BIN         : {sparse_model_dir}")
    log(f"Modèle TXT         : {sparse_txt_dir}")
    log(f"Nuage PLY          : {output_ply}")
    log(f"Images inscrites   : {registered_images}/{n_images}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n[INTERRUPTED] Interruption par l'utilisateur.")
        sys.exit(130)
    except Exception as error:
        log(f"\n[ERROR] {error}")
        sys.exit(1)
