#!/usr/bin/env python
"""Post-process pixel output and create QA artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quantize and preview pixel art output.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("build/perfect-pixel"))
    parser.add_argument("--colors", type=int, default=0)
    parser.add_argument("--preview-scale", type=int, default=16)
    parser.add_argument(
        "--max-preview-side",
        type=int,
        default=4096,
        help="Cap the generated preview's longest side. 0 disables the cap.",
    )
    return parser.parse_args()


def quantize_rgba(image: Image.Image, colors: int) -> Image.Image:
    if colors <= 0:
        return image.copy()
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    quantized = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = quantized.convert("RGBA")
    out.putalpha(alpha.point(lambda value: 255 if value >= 128 else 0))
    return out


def color_report(image: Image.Image) -> dict[str, object]:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    pixels = rgba.reshape(-1, 4)
    visible = pixels[:, 3] > 0
    visible_pixels = pixels[visible]
    if visible_pixels.size == 0:
        return {
            "size": [int(value) for value in image.size],
            "visible_color_count": 0,
            "top_colors": [],
        }

    expanded = visible_pixels.astype(np.uint32)
    packed = (
        (expanded[:, 0] << 24)
        | (expanded[:, 1] << 16)
        | (expanded[:, 2] << 8)
        | expanded[:, 3]
    )
    colors, counts = np.unique(packed, return_counts=True)
    order = np.argsort(counts)[-16:][::-1]
    top = [
        {
            "rgba": [
                int((int(color) >> 24) & 0xFF),
                int((int(color) >> 16) & 0xFF),
                int((int(color) >> 8) & 0xFF),
                int(int(color) & 0xFF),
            ],
            "count": int(counts[index]),
        }
        for index, color in zip(order, colors[order])
    ]
    return {
        "size": [int(value) for value in image.size],
        "visible_color_count": int(len(colors)),
        "top_colors": top,
    }


def effective_preview_scale(image: Image.Image, requested_scale: int, max_preview_side: int) -> int:
    scale = max(1, int(requested_scale))
    max_side = max(0, int(max_preview_side))
    if max_side <= 0:
        return scale
    longest_side = max(image.size)
    if longest_side <= 0:
        return scale
    return max(1, min(scale, max_preview_side // longest_side))


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    image = Image.open(args.input).convert("RGBA")
    sprite = quantize_rgba(image, args.colors)
    sprite_path = args.output_dir / "sprite.png"
    sprite.save(sprite_path)

    scale = effective_preview_scale(sprite, args.preview_scale, args.max_preview_side)
    preview = sprite.resize((sprite.width * scale, sprite.height * scale), Image.Resampling.NEAREST)
    preview_path = args.output_dir / f"sprite_x{scale}.png"
    preview.save(preview_path)

    report = {
        "input": str(args.input),
        "sprite": str(sprite_path),
        "preview": str(preview_path),
        "preview_scale": scale,
        "quantize_colors": args.colors or None,
        **color_report(sprite),
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    print(sprite_path)


if __name__ == "__main__":
    main()
