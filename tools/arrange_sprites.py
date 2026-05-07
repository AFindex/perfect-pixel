#!/usr/bin/env python
"""Split visible mask components and arrange them into a normalized sprite sheet."""

from __future__ import annotations

import argparse
import json
import math
import shutil
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
        "--split-mode",
        choices=["auto", "agglomerative", "hdbscan", "affinity", "clustered", "connected"],
        default="auto",
        help="auto uses OpenCV morphology grouping for sprite-like sheets; agglomerative/hdbscan/affinity expose sklearn clusterers; clustered/connected are fallbacks.",
    )
    parser.add_argument(
        "--merge-gap",
        type=int,
        default=2,
        help="Legacy connected-mode dilation gap. Also acts as the minimum clustered gap.",
    )
    parser.add_argument(
        "--cluster-gap",
        type=int,
        default=14,
        help="Preferred maximum pixel gap for grouping separate islands into one element.",
    )
    parser.add_argument(
        "--cluster-gap-ratio",
        type=float,
        default=0.5,
        help="Dynamic grouping gap as a fraction of the median component size.",
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


def raw_components(mask: np.ndarray, min_area: int) -> tuple[list[dict[str, object]], np.ndarray]:
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    components: list[dict[str, object]] = []
    min_area = max(1, int(min_area))

    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        cx, cy = centroids[label]
        components.append(
            {
                "label": int(label),
                "source_bbox": [x, y, x + w, y + h],
                "area": area,
                "center": [float(cx), float(cy)],
                "size": float(max(w, h, math.sqrt(area))),
            },
        )

    components.sort(key=lambda item: (item["source_bbox"][1], item["source_bbox"][0]))
    return components, labels


def legacy_connected_boxes(mask: np.ndarray, min_area: int, merge_gap: int) -> list[dict[str, object]]:
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
                "component_labels": [],
                "raw_component_count": 1,
            },
        )

    boxes.sort(key=lambda item: (item["source_bbox"][1], item["source_bbox"][0]))
    return boxes


def bbox_distance(a: list[int], b: list[int]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    dx = max(ax0 - bx1, bx0 - ax1, 0)
    dy = max(ay0 - by1, by0 - ay1, 0)
    return math.hypot(dx, dy)


def axis_overlaps(a: list[int], b: list[int]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return min(ax1, bx1) > max(ax0, bx0) or min(ay1, by1) > max(ay0, by0)


def range_overlap_ratio(a0: int, a1: int, b0: int, b1: int) -> float:
    overlap = max(0, min(a1, b1) - max(a0, b0))
    return overlap / max(1, min(a1 - a0, b1 - b0))


def bbox_intersects(a: list[int], b: list[int]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return min(ax1, bx1) > max(ax0, bx0) and min(ay1, by1) > max(ay0, by0)


def attach_component_labels(
    boxes: list[dict[str, object]],
    components: list[dict[str, object]],
) -> list[dict[str, object]]:
    for box in boxes:
        labels = [
            int(component["label"])
            for component in components
            if bbox_intersects(box["source_bbox"], component["source_bbox"])
        ]
        box["component_labels"] = labels
        box["raw_component_count"] = len(labels) or box.get("raw_component_count", 1)
    return boxes


class UnionFind:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, a: int, b: int) -> None:
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a


def clustered_boxes(
    components: list[dict[str, object]],
    merge_gap: int,
    cluster_gap: int,
    cluster_gap_ratio: float,
) -> list[dict[str, object]]:
    if not components:
        return []

    sizes = np.array([float(item["size"]) for item in components], dtype=np.float32)
    areas = np.array([int(item["area"]) for item in components], dtype=np.float32)
    median_size = float(np.median(sizes)) if len(sizes) else 1.0
    median_area = float(np.median(areas)) if len(areas) else 1.0
    base_gap = max(float(merge_gap), float(cluster_gap), median_size * max(0.0, cluster_gap_ratio))

    union_find = UnionFind(len(components))
    satellite_links: dict[int, tuple[float, int]] = {}
    for i, first in enumerate(components):
        for j in range(i + 1, len(components)):
            second = components[j]
            distance = bbox_distance(first["source_bbox"], second["source_bbox"])
            gap = base_gap
            first_area = float(first["area"])
            second_area = float(second["area"])
            small_index, large_index = (i, j) if first_area <= second_area else (j, i)
            small_area = min(first_area, second_area)
            large_area = max(first_area, second_area)

            if large_area and (small_area / large_area <= 0.22 or small_area <= median_area * 0.45):
                satellite_gap = base_gap * 1.4
                if axis_overlaps(first["source_bbox"], second["source_bbox"]):
                    satellite_gap *= 1.15
                if distance <= satellite_gap:
                    current = satellite_links.get(small_index)
                    if current is None or distance < current[0]:
                        satellite_links[small_index] = (distance, large_index)
                continue

            if axis_overlaps(first["source_bbox"], second["source_bbox"]):
                gap *= 1.15

            if distance <= gap:
                union_find.union(i, j)

    for small_index, (_, large_index) in satellite_links.items():
        union_find.union(small_index, large_index)

    groups: dict[int, list[dict[str, object]]] = {}
    for index, component in enumerate(components):
        groups.setdefault(union_find.find(index), []).append(component)

    boxes = []
    for group in groups.values():
        x0 = min(int(item["source_bbox"][0]) for item in group)
        y0 = min(int(item["source_bbox"][1]) for item in group)
        x1 = max(int(item["source_bbox"][2]) for item in group)
        y1 = max(int(item["source_bbox"][3]) for item in group)
        area = int(sum(int(item["area"]) for item in group))
        labels = [int(item["label"]) for item in group]
        center_x = sum(float(item["center"][0]) * int(item["area"]) for item in group) / max(1, area)
        center_y = sum(float(item["center"][1]) * int(item["area"]) for item in group) / max(1, area)
        boxes.append(
            {
                "source_bbox": [x0, y0, x1, y1],
                "area": area,
                "center": [center_x, center_y],
                "component_labels": labels,
                "raw_component_count": len(group),
            },
        )

    boxes.sort(key=lambda item: (item["source_bbox"][1], item["source_bbox"][0]))
    return boxes


def distance_matrix(components: list[dict[str, object]]) -> np.ndarray:
    count = len(components)
    distances = np.zeros((count, count), dtype=np.float64)
    for i, first in enumerate(components):
        for j in range(i + 1, count):
            distance = bbox_distance(first["source_bbox"], components[j]["source_bbox"])
            distances[i, j] = distance
            distances[j, i] = distance
    return distances


def component_distance_matrix(
    components: list[dict[str, object]],
    cluster_gap: int,
    cluster_gap_ratio: float,
) -> tuple[np.ndarray, dict[str, object]]:
    count = len(components)
    distances = np.zeros((count, count), dtype=np.float64)
    if count <= 1:
        return distances, {"effective_gap": 1.0}

    sizes = np.array([float(item["size"]) for item in components], dtype=np.float64)
    areas = np.array([float(item["area"]) for item in components], dtype=np.float64)
    median_size = float(np.median(sizes)) if len(sizes) else 1.0
    effective_gap = max(1.0, float(cluster_gap), median_size * max(0.0, float(cluster_gap_ratio)) * 0.9)

    for i, first in enumerate(components):
        ax0, ay0, ax1, ay1 = first["source_bbox"]
        acx, acy = first["center"]
        for j in range(i + 1, count):
            second = components[j]
            bx0, by0, bx1, by1 = second["source_bbox"]
            bcx, bcy = second["center"]
            gap_score = bbox_distance(first["source_bbox"], second["source_bbox"]) / effective_gap
            center_scale = max(effective_gap * 2.2, (float(first["size"]) + float(second["size"])) * 0.75)
            center_score = math.hypot(float(acx) - float(bcx), float(acy) - float(bcy)) / max(1.0, center_scale)
            align = max(
                range_overlap_ratio(ax0, ax1, bx0, bx1),
                range_overlap_ratio(ay0, ay1, by0, by1),
            )
            small_area = min(areas[i], areas[j])
            large_area = max(areas[i], areas[j])
            distance = gap_score * 0.9 + center_score * 0.1
            distance *= 1.0 - 0.18 * align
            if large_area > 0 and small_area / large_area <= 0.18 and align > 0.05:
                distance *= 0.7
            distances[i, j] = distance
            distances[j, i] = distance

    return distances, {
        "effective_gap": round(effective_gap, 3),
        "median_component_size": round(median_size, 3),
    }


def report_distance_matrix(distances: np.ndarray) -> list[list[float]] | str:
    if distances.shape[0] > 80:
        return f"omitted ({distances.shape[0]} components)"
    return np.round(distances, 3).tolist()


def boxes_from_labels(
    components: list[dict[str, object]],
    labels: np.ndarray,
) -> list[dict[str, object]]:
    groups: dict[int, list[dict[str, object]]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(int(label), []).append(components[index])

    boxes = []
    for group in groups.values():
        x0 = min(int(item["source_bbox"][0]) for item in group)
        y0 = min(int(item["source_bbox"][1]) for item in group)
        x1 = max(int(item["source_bbox"][2]) for item in group)
        y1 = max(int(item["source_bbox"][3]) for item in group)
        area = int(sum(int(item["area"]) for item in group))
        labels_in_group = [int(item["label"]) for item in group]
        center_x = sum(float(item["center"][0]) * int(item["area"]) for item in group) / max(1, area)
        center_y = sum(float(item["center"][1]) * int(item["area"]) for item in group) / max(1, area)
        boxes.append(
            {
                "source_bbox": [x0, y0, x1, y1],
                "area": area,
                "center": [center_x, center_y],
                "component_labels": labels_in_group,
                "raw_component_count": len(group),
            },
        )

    boxes.sort(key=lambda item: (item["source_bbox"][1], item["source_bbox"][0]))
    return boxes


def agglomerative_boxes(
    components: list[dict[str, object]],
    cluster_gap: int,
    cluster_gap_ratio: float,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not components:
        return [], {"algorithm": "sklearn.cluster.AgglomerativeClustering", "labels": []}
    if len(components) == 1:
        return boxes_from_labels(components, np.array([0])), {
            "algorithm": "sklearn.cluster.AgglomerativeClustering",
            "labels": [0],
            "fallback": "single-component",
        }

    try:
        from sklearn.cluster import AgglomerativeClustering
    except Exception as exc:  # pragma: no cover - environment error
        raise RuntimeError("scikit-learn is required for --split-mode auto/agglomerative") from exc

    distances, distance_info = component_distance_matrix(components, cluster_gap, cluster_gap_ratio)
    threshold = max(0.25, min(1.5, 0.25 + max(0.0, float(cluster_gap_ratio))))
    model = AgglomerativeClustering(
        n_clusters=None,
        metric="precomputed",
        linkage="average",
        distance_threshold=threshold,
        compute_distances=True,
    )
    labels = model.fit_predict(distances)
    return boxes_from_labels(components, labels), {
        "algorithm": "sklearn.cluster.AgglomerativeClustering",
        "linkage": "average",
        "distance_threshold": round(threshold, 3),
        "labels": labels.astype(int).tolist(),
        "distance_matrix": report_distance_matrix(distances),
        **distance_info,
    }


def normalize_noise_labels(labels: np.ndarray) -> np.ndarray:
    normalized = labels.astype(int).copy()
    next_label = max([int(label) for label in normalized if label >= 0], default=-1) + 1
    for index, label in enumerate(normalized):
        if label < 0:
            normalized[index] = next_label
            next_label += 1
    return normalized


def hdbscan_boxes(
    components: list[dict[str, object]],
    cluster_gap: int,
    cluster_gap_ratio: float,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not components:
        return [], {"algorithm": "sklearn.cluster.HDBSCAN", "labels": []}
    if len(components) == 1:
        return boxes_from_labels(components, np.array([0])), {
            "algorithm": "sklearn.cluster.HDBSCAN",
            "labels": [0],
            "fallback": "single-component",
        }

    try:
        from sklearn.cluster import HDBSCAN
    except Exception as exc:  # pragma: no cover - environment error
        raise RuntimeError("scikit-learn>=1.3 is required for --split-mode hdbscan") from exc

    distances, distance_info = component_distance_matrix(components, cluster_gap, cluster_gap_ratio)
    labels = HDBSCAN(
        min_cluster_size=2,
        min_samples=1,
        metric="precomputed",
        cluster_selection_epsilon=0.25,
        allow_single_cluster=False,
    ).fit_predict(distances)
    labels = normalize_noise_labels(labels)
    return boxes_from_labels(components, labels), {
        "algorithm": "sklearn.cluster.HDBSCAN",
        "labels": labels.astype(int).tolist(),
        "distance_matrix": report_distance_matrix(distances),
        **distance_info,
    }


def affinity_boxes(
    components: list[dict[str, object]],
    cluster_gap: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    if not components:
        return [], {"algorithm": "sklearn.cluster.AffinityPropagation", "labels": []}
    if len(components) == 1:
        return boxes_from_labels(components, np.array([0])), {
            "algorithm": "sklearn.cluster.AffinityPropagation",
            "labels": [0],
            "fallback": "single-component",
        }
    if len(components) == 2:
        distance = bbox_distance(components[0]["source_bbox"], components[1]["source_bbox"])
        labels = np.array([0, 0]) if distance <= max(1, int(cluster_gap)) else np.array([0, 1])
        return boxes_from_labels(components, labels), {
            "algorithm": "sklearn.cluster.AffinityPropagation",
            "labels": labels.astype(int).tolist(),
            "fallback": "two-component-distance",
            "distance": float(distance),
        }

    try:
        from sklearn.cluster import AffinityPropagation
    except Exception as exc:  # pragma: no cover - environment error
        raise RuntimeError("scikit-learn is required for --split-mode auto") from exc

    distances = distance_matrix(components)
    similarities = -distances
    model = AffinityPropagation(
        affinity="precomputed",
        damping=0.75,
        max_iter=500,
        convergence_iter=20,
        random_state=0,
    )
    labels = model.fit_predict(similarities)
    return boxes_from_labels(components, labels), {
        "algorithm": "sklearn.cluster.AffinityPropagation",
        "labels": labels.astype(int).tolist(),
        "cluster_centers": model.cluster_centers_indices_.astype(int).tolist()
        if model.cluster_centers_indices_ is not None
        else [],
        "distance_matrix": report_distance_matrix(distances),
    }


def split_boxes(
    mask: np.ndarray,
    min_area: int,
    merge_gap: int,
    split_mode: str,
    cluster_gap: int,
    cluster_gap_ratio: float,
) -> tuple[list[dict[str, object]], np.ndarray, list[dict[str, object]], dict[str, object]]:
    components, label_image = raw_components(mask, min_area)
    if split_mode == "connected":
        boxes = legacy_connected_boxes(mask, min_area, merge_gap)
        return attach_component_labels(boxes, components), label_image, components, {
            "mode": "connected",
            "effective_cluster_gap": merge_gap,
        }
    if split_mode == "auto":
        effective_gap = max(0, int(cluster_gap))
        boxes = attach_component_labels(legacy_connected_boxes(mask, min_area, effective_gap), components)
        return boxes, label_image, components, {
            "mode": "auto",
            "algorithm": "OpenCV morphology dilation + connectedComponentsWithStats",
            "effective_cluster_gap": effective_gap,
        }
    if split_mode == "agglomerative":
        boxes, auto_info = agglomerative_boxes(components, cluster_gap, cluster_gap_ratio)
        return boxes, label_image, components, {
            "mode": split_mode,
            **auto_info,
        }
    if split_mode == "hdbscan":
        boxes, hdbscan_info = hdbscan_boxes(components, cluster_gap, cluster_gap_ratio)
        return boxes, label_image, components, {
            "mode": "hdbscan",
            **hdbscan_info,
        }
    if split_mode == "affinity":
        boxes, affinity_info = affinity_boxes(components, cluster_gap)
        return boxes, label_image, components, {
            "mode": "affinity",
            **affinity_info,
        }
    return clustered_boxes(components, merge_gap, cluster_gap, cluster_gap_ratio), label_image, components, {
        "mode": "clustered",
        "effective_cluster_gap": cluster_gap,
    }


def crop_mask_for_box(
    mask: np.ndarray,
    label_image: np.ndarray | None,
    bbox: list[int],
    component_labels: list[int] | None,
) -> np.ndarray:
    x0, y0, x1, y1 = bbox
    if label_image is not None and component_labels:
        return np.isin(label_image[y0:y1, x0:x1], component_labels)
    return mask[y0:y1, x0:x1]


def masked_crop(
    image: Image.Image,
    mask: np.ndarray,
    bbox: list[int],
    label_image: np.ndarray | None = None,
    component_labels: list[int] | None = None,
) -> tuple[Image.Image, Image.Image]:
    x0, y0, x1, y1 = bbox
    crop = image.crop((x0, y0, x1, y1)).convert("RGBA")
    crop_bool = crop_mask_for_box(mask, label_image, bbox, component_labels)
    crop_mask = Image.fromarray(np.where(crop_bool, 255, 0).astype(np.uint8), mode="L")
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


def export_clustered_elements(
    image: Image.Image,
    mask: np.ndarray,
    boxes: list[dict[str, object]],
    label_image: np.ndarray | None,
    output_dir: Path,
) -> tuple[Path, list[dict[str, object]]]:
    elements_dir = output_dir / "10_arranged_elements"
    if elements_dir.exists():
        shutil.rmtree(elements_dir)
    elements_dir.mkdir(parents=True, exist_ok=True)

    exports: list[dict[str, object]] = []
    for index, item in enumerate(boxes, start=1):
        bbox = item["source_bbox"]
        crop, crop_mask = masked_crop(image, mask, bbox, label_image, item.get("component_labels"))
        sprite_path = elements_dir / f"element_{index:04d}.png"
        mask_rgba_path = elements_dir / f"element_{index:04d}_mask_rgba.png"
        crop.save(sprite_path)
        save_alpha_mask(mask_rgba_path, crop_mask)
        exports.append(
            {
                "index": index - 1,
                "sprite": str(sprite_path),
                "mask_rgba": str(mask_rgba_path),
                "source_bbox": bbox,
                "area": item["area"],
                "raw_component_count": item.get("raw_component_count", 1),
                "component_labels": item.get("component_labels", []),
            },
        )
    return elements_dir, exports


def color_for_index(index: int) -> tuple[int, int, int, int]:
    hue = (index * 0.61803398875) % 1.0
    segment = int(hue * 6)
    fraction = hue * 6 - segment
    q = int(255 * (1 - fraction))
    t = int(255 * fraction)
    palette = [
        (255, t, 0),
        (q, 255, 0),
        (0, 255, t),
        (0, q, 255),
        (t, 0, 255),
        (255, 0, q),
    ]
    r, g, b = palette[segment % 6]
    return r, g, b, 190


def save_label_debug(path: Path, label_image: np.ndarray, groups: list[list[int]]) -> None:
    rgba = np.zeros((*label_image.shape, 4), dtype=np.uint8)
    for index, labels in enumerate(groups):
        if not labels:
            continue
        rgba[np.isin(label_image, labels)] = color_for_index(index)
    Image.fromarray(rgba, mode="RGBA").save(path)


def arrange(
    image: Image.Image,
    mask: np.ndarray,
    boxes: list[dict[str, object]],
    columns: int,
    padding: int,
    cell_width: int,
    cell_height: int,
    label_image: np.ndarray | None = None,
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
        crop, crop_mask = masked_crop(image, mask, bbox, label_image, item.get("component_labels"))
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
                "raw_component_count": item.get("raw_component_count", 1),
                "component_labels": item.get("component_labels", []),
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
    boxes, label_image, raw_items, split_info = split_boxes(
        mask,
        args.min_area,
        args.merge_gap,
        args.split_mode,
        args.cluster_gap,
        args.cluster_gap_ratio,
    )
    sheet, sheet_mask, report = arrange(
        image,
        mask,
        boxes,
        args.columns,
        args.padding,
        args.cell_width,
        args.cell_height,
        label_image,
    )
    elements_dir, element_exports = export_clustered_elements(image, mask, boxes, label_image, args.output_dir)

    sprite_path = args.output_dir / "10_arranged_sprite.png"
    mask_path = args.output_dir / "10_arranged_mask.png"
    mask_rgba_path = args.output_dir / "10_arranged_mask_rgba.png"
    components_debug_path = args.output_dir / "10_components_debug.png"
    clusters_debug_path = args.output_dir / "10_clusters_debug.png"
    preview_scale = effective_preview_scale(sheet, args.preview_scale, args.max_preview_side)
    preview_path = args.output_dir / f"10_arranged_sprite_x{preview_scale}.png"
    report_path = args.output_dir / "10_arrange_report.json"

    sheet.save(sprite_path)
    sheet_mask.save(mask_path)
    save_alpha_mask(mask_rgba_path, sheet_mask)
    save_label_debug(components_debug_path, label_image, [[int(item["label"])] for item in raw_items])
    save_label_debug(clusters_debug_path, label_image, [item.get("component_labels", []) for item in boxes])
    sheet.resize((sheet.width * preview_scale, sheet.height * preview_scale), Image.Resampling.NEAREST).save(
        preview_path,
    )

    payload = {
        "input": str(args.input),
        "mask": str(args.mask) if args.mask else "input alpha",
        "split_mode": args.split_mode,
        "split_info": split_info,
        "padding": max(0, int(args.padding)),
        "min_area": max(1, int(args.min_area)),
        "merge_gap": max(0, int(args.merge_gap)),
        "cluster_gap": max(0, int(args.cluster_gap)),
        "cluster_gap_ratio": max(0.0, float(args.cluster_gap_ratio)),
        "raw_component_count": len(raw_items),
        "preview_scale": preview_scale,
        "sprite": str(sprite_path),
        "mask_output": str(mask_path),
        "mask_rgba": str(mask_rgba_path),
        "elements_dir": str(elements_dir),
        "element_exports": element_exports,
        "components_debug": str(components_debug_path),
        "clusters_debug": str(clusters_debug_path),
        "preview": str(preview_path),
        **report,
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(sprite_path)


if __name__ == "__main__":
    main()
