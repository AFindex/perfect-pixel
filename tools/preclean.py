#!/usr/bin/env python
"""Pre-clean noisy solid backgrounds before pixel restoration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

try:
    from scipy import ndimage
except Exception:  # pragma: no cover - optional fallback
    ndimage = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean solid backgrounds and white fringe.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("build/perfect-pixel"))
    parser.add_argument("--bg-tolerance", type=float, default=8)
    parser.add_argument("--alpha-threshold", type=int, default=128)
    parser.add_argument(
        "--mask-provider",
        choices=["classic", "rmbg", "hybrid"],
        default="classic",
        help="Use classic edge-connected color mask, RMBG-2.0 alpha, or a fused hybrid mask.",
    )
    parser.add_argument(
        "--background-mode",
        choices=["edges", "corners", "midpoints"],
        default="edges",
    )
    parser.add_argument("--edge-contract", type=int, default=1)
    parser.add_argument("--outline-width", type=int, default=2)
    parser.add_argument("--kernel", type=int, default=3)
    parser.add_argument("--rmbg-bg-threshold", type=int, default=32)
    parser.add_argument("--rmbg-fg-threshold", type=int, default=224)
    parser.add_argument("--rmbg-device", default="auto")
    parser.add_argument("--rmbg-cache-dir", type=Path)
    parser.add_argument("--rmbg-local-files-only", dest="rmbg_local_files_only", action="store_true", default=True)
    parser.add_argument("--rmbg-allow-download", dest="rmbg_local_files_only", action="store_false")
    return parser.parse_args()


def save_mask(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L").save(path)


def save_alpha_mask(path: Path, mask: np.ndarray, color: tuple[int, int, int] = (255, 255, 255)) -> None:
    rgba = np.zeros((*mask.shape, 4), dtype=np.uint8)
    rgba[mask, :3] = color
    rgba[mask, 3] = 255
    Image.fromarray(rgba, mode="RGBA").save(path)


def border_pixels(rgb: np.ndarray, alpha: np.ndarray, alpha_threshold: int) -> np.ndarray:
    h, w = alpha.shape
    edge = np.zeros((h, w), dtype=bool)
    edge[0, :] = True
    edge[-1, :] = True
    edge[:, 0] = True
    edge[:, -1] = True
    valid = edge & (alpha >= alpha_threshold)
    pixels = rgb[valid]
    if len(pixels) == 0:
        pixels = rgb[edge]
    return pixels


def seed_points(shape: tuple[int, int], mode: str) -> list[tuple[int, int]]:
    h, w = shape
    if mode == "corners":
        return [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
    if mode == "midpoints":
        return [(0, w // 2), (h - 1, w // 2), (h // 2, 0), (h // 2, w - 1)]
    points: list[tuple[int, int]] = []
    points.extend((0, x) for x in range(w))
    points.extend((h - 1, x) for x in range(w))
    points.extend((y, 0) for y in range(h))
    points.extend((y, w - 1) for y in range(h))
    return points


def connected_background(near_bg: np.ndarray, mode: str) -> np.ndarray:
    _, labels = cv2.connectedComponents(near_bg.astype(np.uint8), connectivity=8)
    wanted = set()
    for y, x in seed_points(near_bg.shape, mode):
        label = int(labels[y, x])
        if label:
            wanted.add(label)
    if not wanted:
        h, w = near_bg.shape
        border_labels = np.concatenate(
            [labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]],
        )
        wanted = {int(label) for label in border_labels if label}
    if not wanted:
        return np.zeros_like(near_bg, dtype=bool)
    return np.isin(labels, list(wanted))


def make_trimap(background: np.ndarray, sure_fg: np.ndarray) -> np.ndarray:
    trimap = np.full(background.shape, 128, dtype=np.uint8)
    trimap[background] = 0
    trimap[sure_fg] = 255
    return trimap


def make_outline_mask(subject: np.ndarray, width: int) -> np.ndarray:
    width = max(0, int(width))
    if width == 0 or not subject.any():
        return np.zeros_like(subject, dtype=bool)
    kernel = np.ones((3, 3), dtype=np.uint8)
    dilated = cv2.dilate(subject.astype(np.uint8), kernel, iterations=width).astype(bool)
    return dilated & ~subject


def clamp_u8(value: int) -> int:
    return max(0, min(255, int(value)))


def clean_mask(mask: np.ndarray, kernel_size: int) -> np.ndarray:
    kernel_size = max(1, int(kernel_size))
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    cleaned = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    return cv2.morphologyEx(cleaned.astype(np.uint8), cv2.MORPH_OPEN, kernel).astype(bool)


def erode_mask(mask: np.ndarray, amount: int) -> np.ndarray:
    amount = max(0, int(amount))
    if amount == 0:
        return mask.copy()
    kernel = np.ones((amount * 2 + 1, amount * 2 + 1), dtype=np.uint8)
    return cv2.erode(mask.astype(np.uint8), kernel).astype(bool)


def rmbg_alpha_from_image(image: Image.Image, args: argparse.Namespace) -> np.ndarray:
    try:
        from tools.rmbg2 import Rmbg2Session, Rmbg2Settings, Rmbg2UnavailableError
    except Exception as exc:  # noqa: BLE001 - CLI should show a direct setup hint.
        raise RuntimeError("RMBG-2.0 wrapper is unavailable. Run `python tools/cli.py init --with-rmbg`.") from exc

    try:
        session = Rmbg2Session(
            Rmbg2Settings(
                device=args.rmbg_device,
                cache_dir=args.rmbg_cache_dir,
                local_files_only=args.rmbg_local_files_only,
            ),
        )
        return np.asarray(session.predict_alpha(image), dtype=np.uint8)
    except Rmbg2UnavailableError as exc:
        raise RuntimeError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - keep preclean failure readable.
        raise RuntimeError(
            "RMBG-2.0 inference failed. Check optional dependencies, model access, "
            "and HF_TOKEN / Hugging Face login if the model is gated.",
        ) from exc


def decontaminate_edge(rgb: np.ndarray, clean: np.ndarray, edge_band: np.ndarray, sure_fg: np.ndarray) -> None:
    if not edge_band.any() or not sure_fg.any() or ndimage is None:
        return
    _, indices = ndimage.distance_transform_edt(~sure_fg, return_indices=True)
    yy = indices[0][edge_band]
    xx = indices[1][edge_band]
    clean[..., :3][edge_band] = rgb[yy, xx]


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    image = Image.open(args.input).convert("RGBA")
    rgba = np.array(image)
    rgb = rgba[..., :3]
    alpha = rgba[..., 3]
    h, w = alpha.shape

    Image.fromarray(rgba, mode="RGBA").save(args.output_dir / "00_input_rgba.png")

    bg_rgb = np.median(border_pixels(rgb, alpha, args.alpha_threshold), axis=0).astype(np.uint8)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    bg_lab = cv2.cvtColor(bg_rgb.reshape(1, 1, 3), cv2.COLOR_RGB2LAB).astype(np.float32)[0, 0]
    lab_distance = np.linalg.norm(lab - bg_lab, axis=2)
    rgb_distance = np.linalg.norm(rgb.astype(np.float32) - bg_rgb.astype(np.float32), axis=2)

    near_bg = (
        (lab_distance <= args.bg_tolerance * 2.25)
        | (rgb_distance <= args.bg_tolerance * 2.0)
    ) & (alpha >= args.alpha_threshold)
    classic_background = connected_background(near_bg, args.background_mode)

    rmbg_alpha = None
    rmbg_background = None
    visible = alpha >= args.alpha_threshold
    rmbg_bg_threshold = clamp_u8(args.rmbg_bg_threshold)
    rmbg_fg_threshold = clamp_u8(args.rmbg_fg_threshold)
    if args.mask_provider in {"rmbg", "hybrid"}:
        try:
            rmbg_alpha = rmbg_alpha_from_image(image, args)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from None
        rmbg_background = (rmbg_alpha <= rmbg_bg_threshold) & visible

    if args.mask_provider == "classic":
        background = classic_background
    elif args.mask_provider == "rmbg":
        background = rmbg_background
    else:
        # Hybrid keeps the old edge-connected mask as the safe anchor, then lets
        # RMBG remove interior white background holes only when the color still
        # looks like the estimated canvas background.
        background = classic_background | (rmbg_background & near_bg)

    background = clean_mask(background, args.kernel)
    foreground = (~background) & visible
    sure_fg_seed = foreground
    if rmbg_alpha is not None:
        rmbg_sure = (rmbg_alpha >= rmbg_fg_threshold) & foreground
        if rmbg_sure.any():
            sure_fg_seed = rmbg_sure
    sure_fg = erode_mask(sure_fg_seed, args.edge_contract)
    if not sure_fg.any() and foreground.any():
        sure_fg = erode_mask(foreground, args.edge_contract)

    edge_band = foreground & ~sure_fg
    strict_bg = (lab_distance <= max(1.0, args.bg_tolerance * 1.45)) | (
        rgb_distance <= max(1.0, args.bg_tolerance * 1.2)
    )
    remove_fringe = edge_band & strict_bg
    keep_edge = edge_band & ~remove_fringe

    clean = rgba.copy()
    clean[..., 3] = np.where(background | remove_fringe, 0, np.where(foreground, 255, 0)).astype(np.uint8)
    decontaminate_edge(rgb, clean, keep_edge, sure_fg)
    subject_mask = clean[..., 3] >= args.alpha_threshold
    outline_mask = make_outline_mask(subject_mask, args.outline_width)

    save_mask(args.output_dir / "01_near_bg.png", near_bg)
    if rmbg_alpha is not None:
        Image.fromarray(rmbg_alpha, mode="L").save(args.output_dir / "01_rmbg_alpha.png")
        save_mask(args.output_dir / "02_classic_bg_mask.png", clean_mask(classic_background, args.kernel))
        save_mask(args.output_dir / "02_rmbg_bg_mask.png", clean_mask(rmbg_background, args.kernel))
    save_mask(args.output_dir / "02_connected_bg_mask.png", background)
    save_mask(args.output_dir / "03_sure_fg.png", sure_fg)
    save_mask(args.output_dir / "04_edge_band.png", edge_band)
    Image.fromarray(make_trimap(background, sure_fg), mode="L").save(args.output_dir / "05_trimap.png")
    save_mask(args.output_dir / "06_subject_mask.png", subject_mask)
    save_alpha_mask(args.output_dir / "06_subject_mask_rgba.png", subject_mask)
    save_mask(args.output_dir / "06_outline_mask.png", outline_mask)
    save_alpha_mask(args.output_dir / "06_outline_mask_rgba.png", outline_mask)
    Image.fromarray(clean, mode="RGBA").save(args.output_dir / "07_clean_rgba.png")

    metadata = {
        "input": str(args.input),
        "size": [w, h],
        "estimated_background_rgb": bg_rgb.tolist(),
        "mask_provider": args.mask_provider,
        "bg_tolerance": args.bg_tolerance,
        "background_mode": args.background_mode,
        "alpha_threshold": args.alpha_threshold,
        "edge_contract": args.edge_contract,
        "outline_width": args.outline_width,
        "rmbg_bg_threshold": rmbg_bg_threshold if rmbg_alpha is not None else None,
        "rmbg_fg_threshold": rmbg_fg_threshold if rmbg_alpha is not None else None,
        "rmbg_device": args.rmbg_device if rmbg_alpha is not None else None,
        "rmbg_local_files_only": args.rmbg_local_files_only if rmbg_alpha is not None else None,
        "scipy_decontaminate": ndimage is not None,
    }
    (args.output_dir / "preclean_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print(args.output_dir / "07_clean_rgba.png")


if __name__ == "__main__":
    main()
