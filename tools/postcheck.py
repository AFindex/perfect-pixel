#!/usr/bin/env python
"""Post-process pixel output and create QA artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quantize and preview pixel art output.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("build/perfect-pixel"))
    parser.add_argument("--colors", type=int, default=0)
    parser.add_argument("--preview-scale", type=int, default=16)
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
    rgba = np.array(image.convert("RGBA"))
    pixels = [tuple(pixel) for pixel in rgba.reshape(-1, 4) if pixel[3] > 0]
    counts = Counter(pixels)
    top = [
        {"rgba": [int(channel) for channel in color], "count": int(count)}
        for color, count in counts.most_common(16)
    ]
    return {
        "size": [int(value) for value in image.size],
        "visible_color_count": int(len(counts)),
        "top_colors": top,
    }


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    image = Image.open(args.input).convert("RGBA")
    sprite = quantize_rgba(image, args.colors)
    sprite_path = args.output_dir / "sprite.png"
    sprite.save(sprite_path)

    scale = max(1, int(args.preview_scale))
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
