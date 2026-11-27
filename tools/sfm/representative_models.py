"""Sélectionne les modèles SfM représentatifs (sparse/N) d'une reconstruction multi-modèles.

Un modèle est retenu s'il contient au moins MIN_IMAGES images et au moins
MIN_FRACTION du total d'images du jeu. Le résultat est écrit dans un manifeste JSON.
"""
import argparse
import json
import struct
import sys
from pathlib import Path


def count_registered_images(model_dir: Path) -> int:
    # Le premier champ de images.bin est le nombre d'images (uint64 little-endian).
    with (model_dir / "images.bin").open("rb") as handle:
        data = handle.read(8)
    if len(data) != 8:
        raise ValueError(f"images.bin illisible : {model_dir}")
    return struct.unpack("<Q", data)[0]


def select_models(sparse_root: Path, total_images: int, min_fraction: float, min_images: int) -> dict:
    if total_images <= 0:
        raise ValueError("total_images doit être strictement positif")
    if not 0 < min_fraction <= 1:
        raise ValueError("min_fraction doit être dans ]0, 1]")
    if min_images < 1:
        raise ValueError("min_images doit être strictement positif")

    models = []
    for model_dir in sorted(sparse_root.iterdir(), key=lambda p: p.name):
        if not model_dir.name.isdigit() or not model_dir.is_dir():
            continue
        if not (model_dir / "images.bin").is_file():
            continue
        count = count_registered_images(model_dir)
        fraction = count / total_images
        eligible = count >= min_images and fraction >= min_fraction
        models.append({
            "index": int(model_dir.name),
            "images": count,
            "fraction": round(fraction, 6),
            "eligible": eligible,
        })
    if not models:
        raise ValueError(f"Aucun modèle sparse/N dans {sparse_root}")
    return {
        "total_images": total_images,
        "min_fraction": min_fraction,
        "min_images": min_images,
        "models": models,
        "selected": [m["index"] for m in models if m["eligible"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sparse-root", required=True, type=Path)
    parser.add_argument("--total-images", required=True, type=int)
    parser.add_argument("--min-fraction", required=True, type=float)
    parser.add_argument("--min-images", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--largest-only", action="store_true",
                        help="ne garder que le modèle éligible ayant le plus d'images")
    args = parser.parse_args()
    manifest = select_models(args.sparse_root, args.total_images, args.min_fraction, args.min_images)
    if args.largest_only and manifest["selected"]:
        largest = max((m for m in manifest["models"] if m["eligible"]), key=lambda m: m["images"])
        manifest["selected"] = [largest["index"]]
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(",".join(str(index) for index in manifest["selected"]))
    for model in manifest["models"]:
        status = "retenu" if model["eligible"] else "écarté"
        print(f"[modèles] sparse/{model['index']} : {model['images']} images ({model['fraction']:.1%}) {status}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
