"""Crée un sous-workspace models/N par modèle SfM retenu.

Chaque sous-workspace voit son modèle comme sfm/sparse/0 et partage images,
inputs.json et masques avec le workspace parent. Sa config est la config parent
suivie de OUTPUT_DIR, ce qui redirige toutes les sorties vers models/N.
"""
import argparse
import os
from pathlib import Path

SHARED_ENTRIES = ("images", "inputs.json", "feature_masks", "train_masks")


def create_workspace(parent: Path, config: Path, index: int) -> Path:
    source = parent / "sfm" / "sparse" / str(index)
    if not (source / "images.bin").is_file():
        raise ValueError(f"Modèle absent ou incomplet : {source}")

    workspace = parent / "models" / str(index)
    model_link = workspace / "sfm" / "sparse" / "0"
    model_link.parent.mkdir(parents=True, exist_ok=True)
    if model_link.is_symlink():
        model_link.unlink()
    if model_link.exists():
        raise ValueError(f"Entrée existante non liée : {model_link}")
    os.symlink(os.path.relpath(source, model_link.parent), model_link)

    for name in SHARED_ENTRIES:
        target = parent / name
        link = workspace / name
        if target.exists() and not link.exists() and not link.is_symlink():
            os.symlink(os.path.relpath(target, workspace), link)

    config_copy = workspace / "config.sh"
    config_copy.write_text(
        config.read_text()
        + f"\n# Généré : sous-workspace du modèle sparse/{index}.\n"
        + f'OUTPUT_DIR="{workspace}"\n'
    )
    return config_copy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--indices", required=True, help="indices séparés par des virgules")
    args = parser.parse_args()
    for raw in args.indices.split(","):
        print(create_workspace(args.parent, args.config, int(raw)))


if __name__ == "__main__":
    main()
