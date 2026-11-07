#!/usr/bin/env python3
"""Conservative multiview sky cleanup adapted from CleanGS."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.postprocess.filter_splats import SPLAT_DTYPE, write_records


def rejection_mask(sky_votes, foreground_votes, min_views):
    """Keep unknown/unseen splats; foreground evidence overrides sky votes."""
    return (sky_votes >= min_views) & (foreground_votes == 0)


def run(args):
    if args.min_views < 1:
        raise ValueError("--min-views doit être >= 1")
    if not 0.5 < args.sky_confidence <= 1:
        raise ValueError("--sky-confidence doit être dans ]0.5, 1]")
    if not 0 <= args.max_removal_fraction < 1:
        raise ValueError("--max-removal-fraction doit être dans [0,1[")
    source, output = args.input.resolve(), args.output.resolve()
    rejected_path = output.with_suffix(".rejected.splat")
    report_path = output.with_suffix(".filter.json")
    outputs = (output, rejected_path, report_path)
    if len(set(outputs)) != 3 or any(
        p == source or (p.exists() and os.path.samefile(p, source)) for p in outputs
    ):
        raise ValueError("Les sorties doivent être distinctes de l'entrée")
    if any(p.exists() for p in outputs) and not args.overwrite:
        raise ValueError("Sortie existante : utiliser --overwrite")
    size = source.stat().st_size
    if not size or size % SPLAT_DTYPE.itemsize:
        raise ValueError("SPLAT vide ou tronqué")

    import cv2
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation
    from cleangs._vendor.gsplat_colmap.colmap import Parser
    from tools.preprocess.colmap_split_blocks import read_cameras_binary

    if not torch.cuda.is_available():
        raise ValueError("Clean-GS requiert un GPU NVIDIA CUDA disponible")
    device = torch.device("cuda")
    scene = args.scene.resolve()
    cameras = read_cameras_binary(scene / "sparse/0/cameras.bin")
    if not cameras or any(c.model_id not in (0, 1, 2, 3, 4) for c in cameras.values()):
        raise ValueError("Caméras non supportées : utiliser PINHOLE, SIMPLE_PINHOLE, RADIAL ou OPENCV")
    parser = Parser(str(scene), factor=1, normalize=False)
    if len(parser.image_names) < args.min_views:
        raise ValueError("Pas assez de vues enregistrées pour le nombre de votes demandé")
    processor = AutoImageProcessor.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForSemanticSegmentation.from_pretrained(
        args.model, local_files_only=True
    ).to(device).eval()
    sky_classes = [int(k) for k, v in model.config.id2label.items()
                   if v.strip().lower() == "sky"]
    if len(sky_classes) != 1:
        raise ValueError("Le modèle de segmentation doit avoir exactement une classe 'sky'")
    sky_id = sky_classes[0]
    records = np.memmap(source, dtype=SPLAT_DTYPE, mode="r")
    xyz = np.asarray(records["position"], dtype=np.float32)
    scales = np.asarray(records["scale"], dtype=np.float32)
    quats = (np.asarray(records["rotation"], dtype=np.float32) - 128) / 128
    if not np.isfinite(xyz).all() or not np.isfinite(scales).all() or np.any(scales <= 0):
        raise ValueError("Positions ou échelles SPLAT invalides")
    norms = np.linalg.norm(quats, axis=1)
    if np.any(norms == 0):
        raise ValueError("Quaternion SPLAT nul")
    quats /= norms[:, None]
    extent = float(np.quantile(np.linalg.norm(xyz - np.median(xyz, axis=0), axis=1), 0.95))
    if not np.isfinite(extent) or extent <= 0:
        raise ValueError("Étendue du SPLAT indéterminable")
    means_t = torch.from_numpy(xyz).to(device)
    scales_t = torch.from_numpy(scales).to(device)
    quats_t = torch.from_numpy(quats).to(device)
    opacities_t = torch.from_numpy(records["rgba"][:, 3].astype(np.float32) / 255).to(device)
    colors_t = torch.zeros((len(records), 3), device=device)
    sky_votes = np.zeros(len(records), dtype=np.uint32)
    foreground_votes = np.zeros(len(records), dtype=np.uint32)
    visible_votes = np.zeros(len(records), dtype=np.uint32)
    output.parent.mkdir(parents=True, exist_ok=True)
    masks_dir = Path(tempfile.mkdtemp(prefix=output.stem + ".sky-masks-", dir=output.parent))
    for i, image_path in enumerate(parser.image_paths):
        camera_id = parser.camera_ids[i]
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Image illisible : {image_path}")
        camera = cameras[camera_id]
        if image.shape[:2] != (camera.height, camera.width):
            raise ValueError(f"Dimensions image incompatibles avec la caméra : {image_path}")
        K = parser.Ks_dict[camera_id].copy()
        if camera_id in parser.mapx_dict:
            image = cv2.remap(image, parser.mapx_dict[camera_id],
                              parser.mapy_dict[camera_id], cv2.INTER_LINEAR)
            x, y, w, h = parser.roi_undist_dict[camera_id]
            image = image[y:y+h, x:x+w]
            K[0, 2] -= x
            K[1, 2] -= y
        h, w = image.shape[:2]
        image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        inputs = processor(images=image, return_tensors="pt").to(device)
        with torch.no_grad():
            logits = model(**inputs).logits
            sky_probability = torch.softmax(logits, dim=1)[:, sky_id:sky_id + 1]
            probs = F.interpolate(
                sky_probability, size=(h, w), mode="bilinear", align_corners=False,
            )[0, 0]
            view = torch.from_numpy(np.linalg.inv(parser.camtoworlds[i]).astype(np.float32)).to(device)
            intrinsics = torch.from_numpy(K.astype(np.float32)).to(device)
            _, _, info = rasterization(
                means=means_t, quats=quats_t, scales=scales_t, opacities=opacities_t,
                colors=colors_t, viewmats=view[None], Ks=intrinsics[None],
                width=w, height=h, packed=False,
                near_plane=extent * 1e-5, far_plane=extent * 1e5,
            )
            coords = info["means2d"][0]
            radii = info["radii"][0]
            if radii.ndim == 2:
                radii = radii.amax(dim=-1)
            u, v = coords[:, 0].floor().long(), coords[:, 1].floor().long()
            visible = (radii > 0) & (u >= 0) & (u < w) & (v >= 0) & (v < h)
            indices = torch.nonzero(visible).flatten()
            probabilities = probs[v[indices], u[indices]]
            index_np = indices.cpu().numpy()
            visible_votes[index_np] += 1
            sky_votes[index_np] += (probabilities >= args.sky_confidence).cpu().numpy()
            foreground_votes[index_np] += (probabilities <= 1 - args.sky_confidence).cpu().numpy()
            sky_mask = (probs >= args.sky_confidence).cpu().numpy().astype(np.uint8) * 255
        mask_path = masks_dir / f"{i:06d}_{Path(parser.image_names[i]).name}.png"
        if not cv2.imwrite(str(mask_path), sky_mask):
            raise OSError(f"Écriture du masque impossible : {mask_path}")
        print(f"[clean-gs] Vue {i + 1}/{len(parser.image_paths)} : {image_path}", flush=True)

    rejected = rejection_mask(sky_votes, foreground_votes, args.min_views)
    fraction = float(rejected.mean())
    report = dict(algorithm="clean-gs-sky-adapted", mode=args.mode, total=len(records),
                  proposed_rejections=int(rejected.sum()), proposed_fraction=fraction,
                  applied_rejections=int(rejected.sum()) if args.mode == "apply" else 0,
                  unseen=int((visible_votes == 0).sum()),
                  uncertain_only=int(((visible_votes > 0) & (sky_votes == 0) &
                                      (foreground_votes == 0)).sum()),
                  sky_model=args.model, sky_confidence=args.sky_confidence,
                  min_views=args.min_views, views=len(parser.image_paths), masks=str(masks_dir))
    print("[clean-gs] " + json.dumps(report, ensure_ascii=False), flush=True)
    if args.mode == "apply" and fraction > args.max_removal_fraction:
        raise ValueError(f"Rejet {fraction:.1%} supérieur à la limite ; aucune sortie remplacée")
    write_records(rejected_path, records, rejected)
    write_records(output, records, ~rejected if args.mode == "apply"
                  else np.ones(len(records), dtype=bool))
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--model", required=True, help="Modèle de segmentation déjà téléchargé localement")
    parser.add_argument("--mode", choices=("diagnostic", "apply"), default="diagnostic")
    parser.add_argument("--sky-confidence", type=float, default=0.9)
    parser.add_argument("--min-views", type=int, default=2)
    parser.add_argument("--max-removal-fraction", type=float, default=0.2)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(1, f"[ERREUR] Clean-GS : {exc}\n")


if __name__ == "__main__":
    main()
