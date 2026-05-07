#!/usr/bin/env python
"""Split visible mask components and arrange them into a normalized sprite sheet."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arrange mask-separated sprites into a uniform sheet.")
    parser.add_argument("input", type=Path, help="RGBA sprite image to split and arrange.")
    parser.add_argument("--mask", type=Path, help="Optional mask image. Defaults to the input alpha channel.")
    parser.add_argument("--output-dir", type=Path, default=Path("build/perfect-pixel"))
    parser.add_argument("--columns", type=int, default=0, help="Column count. 0 means auto square layout.")
    parser.add_argument("--padding", type=int, default=2, help="Padding inside each normalized cell.")
    parser.add_argument("--min-area", type=int, default=16, help="Ignore mask components smaller than this.")
    parser.add_argument(
        "--merge-gap",
        type=int,
        default=2,
        help="Merge visible islands within this pixel gap before splitting.",
    )
    parser.add_argument("--cell-width", type=int, default=0, help="Override normalized cell width.")
    parser.add_argument("--cell-height", type=int, default=0, help="Override normalized cell height.")
    parser.add_argument("--alpha-threshold", type=int, default=1)
    parser.add_argument("--preview-scale", type=int, default=16)
    parser.add_argument("--max-preview-side", type=int, default=4096)
    return parser.parse_args()


def mask_from_image(image: Image.Image, threshold: int) -> np.ndarray:
    if "A" in image.getbands():
        channel = image.convert("RGBA").getchannel("A")
    else:
        channel = image.convert("L")
    return np.array(channel) >= threshold


def load_mask(mask_path: Path | None, image: Image.Image, threshold: int) -> np.ndarray:
    source = Image.open(mask_path) if mask_path else image
    if source.size != image.size:
        source = source.resize(image.size, Image.Resampling.NEAREST)
    return mask_from_image(source, threshold)


def component_boxes(mask: np.ndarray, min_area: int, merge_gap: int) -> list[dict[str, object]]:
    detect_mask = mask.astype(np.uint8)
    if merge_gap > 0 and detect_mask.any():
        kernel_size = merge_gap * 2 + 1
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        detect_mask = cv2.dilate(detect_mask, kernel, iterations=1)

    count, labels, _, _ = cv2.connectedComponentsWithStats(detect_mask, connectivity=8)
    boxes: list[dict[str, object]] = []
    for label in range(1, count):
        grouped = labels == label
        original = grouped & mask
        area = int(original.sum())
        if area < max(1, min_area):
            continue
        yy, xx = np.where(original)
        x0 = int(xx.min())
        y0 = int(yy.min())
        x1 = int(xx.max()) + 1
        y1 = int(yy.max()) + 1
        boxes.append(
            {
                "source_bbox": [x0, y0, x1, y1],
                "area": area,
                "center": [float(xx.mean()), float(yy.mean())],
            },
        )

    boxes.sort(key=lambda item: (item["source_bbox"][1], item["source_bbox"][0]))
    return boxes


def masked_crop(image: Image.Image, mask: np.ndarray, bbox: list[int]) -> tuple[Image.Image, Image.Image]:
    x0, y0, x1, y1 = bbox
    crop = image.crop((x0, y0, x1, y1)).convert("RGBA")
    crop_mask = Image.fromarray(np.where(mask[y0:y1, x0:x1], 255, 0).astype(np.uint8), mode="L")
    alpha = Image.fromarray(
        np.minimum(np.array(crop.getchannel("A")), np.array(crop_mask)).astype(np.uint8),
        mode="L",
    )
    crop.putalpha(alpha)
    return crop, crop_mask


def save_alpha_mask(path: Path, mask: Image.Image) -> None:
    rgba = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    rgba.putalpha(mask)
    rgba.save(path)


def arrange(
    image: Image.Image,
    mask: np.ndarray,
    boxes: list[dict[str, object]],
    columns: int,
    padding: int,
    cell_width: int,
    cell_height: int,
) -> tuple[Image.Image, Image.Image, dict[str, object]]:
    padding = max(0, int(padding))
    if not boxes:
        empty = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        empty_mask = Image.new("L", (1, 1), 0)
        return empty, empty_mask, {
            "component_count": 0,
            "columns": 0,
            "rows": 0,
            "cell_size": [1, 1],
            "sheet_size": [1, 1],
            "components": [],
        }

    prepared = []
    max_width = 1
    max_height = 1
    for index, item in enumerate(boxes):
        bbox = item["source_bbox"]
        crop, crop_mask = masked_crop(image, mask, bbox)
        max_width = max(max_width, crop.width)
        max_height = max(max_height, crop.height)
        prepared.append((index, item, crop, crop_mask))

    cell_width = max(int(cell_width), max_width + padding * 2)
    cell_height = max(int(cell_height), max_height + padding * 2)
    columns = max(1, int(columns) if columns > 0 else math.ceil(math.sqrt(len(prepared))))
    rows = math.ceil(len(prepared) / columns)

    sheet = Image.new("RGBA", (columns * cell_width, rows * cell_height), (0, 0, 0, 0))
    sheet_mask = Image.new("L", sheet.size, 0)
    components = []

    for index, item, crop, crop_mask in prepared:
        col = index % columns
        row = index // columns
        x = col * cell_width + (cell_width - crop.width) // 2
        y = row * cell_height + (cell_height - crop.height) // 2
        sheet.alpha_composite(crop, (x, y))
        sheet_mask.paste(crop_mask, (x, y), crop_mask)
        components.append(
            {
                "index": index,
                "source_bbox": item["source_bbox"],
                "target_bbox": [x, y, x + crop.width, y + crop.height],
                "area": item["area"],
            },
        )

    return sheet, sheet_mask, {
        "component_count": len(prepared),
        "columns": columns,
        "rows": rows,
        "cell_size": [cell_width, cell_height],
        "sheet_size": [sheet.width, sheet.height],
        "components": components,
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
    mask = load_mask(args.mask, image, args.alpha_threshold)
    boxes = component_boxes(mask, args.min_area, args.merge_gap)
    sheet, sheet_mask, report = arrange(
        image,
        mask,
        boxes,
        args.columns,
        args.padding,
        args.cell_width,
        args.cell_height,
    )

    sprite_path = args.output_dir / "10_arranged_sprite.png"
    mask_path = args.output_dir / "10_arranged_mask.png"
    mask_rgba_path = args.output_dir / "10_arranged_mask_rgba.png"
    preview_scale = effective_preview_scale(sheet, args.preview_scale, args.max_preview_side)
    preview_path = args.output_dir / f"10_arranged_sprite_x{preview_scale}.png"
    report_path = args.output_dir / "10_arrange_report.json"

    sheet.save(sprite_path)
    sheet_mask.save(mask_path)
    save_alpha_mask(mask_rgba_path, sheet_mask)
    sheet.resize((sheet.width * preview_scale, sheet.height * preview_scale), Image.Resampling.NEAREST).save(
        preview_path,
    )

    payload = {
        "input": str(args.input),
        "mask": str(args.mask) if args.mask else "input alpha",
        "padding": max(0, int(args.padding)),
        "min_area": max(1, int(args.min_area)),
        "merge_gap": max(0, int(args.merge_gap)),
        "preview_scale": preview_scale,
        "sprite": str(sprite_path),
        "mask_output": str(mask_path),
        "mask_rgba": str(mask_rgba_path),
        "preview": str(preview_path),
        **report,
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(sprite_path)


if __name__ == "__main__":
    main()
