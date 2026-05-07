"""Shared Perfect Pixel pipeline helpers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "perfect-pixel"


@dataclass(slots=True)
class PipelineSettings:
    bg_tolerance: int = 14
    alpha_threshold: int = 128
    edge_contract: int = 1
    outline_width: int = 2
    background_mode: str = "edges"
    process_mode: str = "clean"
    detect: str = "auto"
    method: str = "nearest"
    auto_colors: bool = False
    colors: int = 256
    transparent_background: bool = True
    cleanup: str = ""
    preview_scale: int = 16
    max_preview_side: int = 4096
    arrange_sprites: bool = False
    arrange_columns: int = 0
    arrange_padding: int = 2
    arrange_min_area: int = 16
    arrange_merge_gap: int = 2
    arrange_split_mode: str = "auto"
    arrange_cluster_gap: int = 14
    arrange_cluster_gap_ratio: float = 0.5


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_input_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def resolve_output_dir(value: str) -> Path:
    output = Path(value)
    if not output.is_absolute():
        output = ROOT / output
    output.mkdir(parents=True, exist_ok=True)
    return output.resolve()


def sanitize_subdir(value: str) -> str:
    name = (value or "0001").strip()
    name = re.sub(r'[<>:"/\\\\|?*\x00-\x1f]', "-", name)
    if not name or set(name) == {"."}:
        return "0001"
    return name


def split_index_name(value: str) -> tuple[str, str, int, str]:
    name = sanitize_subdir(value)
    match = re.match(r"^(.*?)(\d+)(\D*)$", name)
    if not match:
        return "", "0001", 1, ""
    number_text = match.group(2)
    return match.group(1), number_text, int(number_text), match.group(3)


def format_index_name(prefix: str, width: int, number: int, suffix: str) -> str:
    return f"{prefix}{max(0, number):0{max(1, width)}d}{suffix}"


def next_available_subdir(base: str | Path, current: str = "0001") -> str:
    base_path = Path(base)
    if not base_path.is_absolute():
        base_path = ROOT / base_path
    if not base_path.exists() or not base_path.is_dir():
        return sanitize_subdir(current)

    prefix, number_text, number, suffix = split_index_name(current)
    width = len(number_text)
    matcher = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(suffix)}$")
    used = set()

    for child in base_path.iterdir():
        if not child.is_dir():
            continue
        match = matcher.match(child.name)
        if match:
            used.add(int(match.group(1)))

    candidate = number
    while candidate in used:
        candidate += 1
    return format_index_name(prefix, width, candidate, suffix)


def artifact(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    rel = resolved.relative_to(ROOT).as_posix() if is_inside(resolved, ROOT) else resolved.as_posix()
    return {
        "name": resolved.name,
        "path": str(resolved),
        "url": f"/api/file?path={quote(str(resolved))}",
        "displayPath": rel,
    }


def run_command(command: list[str], cwd: Path) -> dict[str, object]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=180,
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "elapsedMs": round((time.perf_counter() - started) * 1000),
    }


def cleanup_stale_outputs(output_dir: Path) -> None:
    stale_paths = [
        output_dir / "07_clean_rgba.png",
        output_dir / "08_unfake_pixel_raw.png",
        output_dir / "sprite.png",
        output_dir / "report.json",
        output_dir / "06_subject_mask.png",
        output_dir / "06_subject_mask_rgba.png",
        output_dir / "06_outline_mask.png",
        output_dir / "06_outline_mask_rgba.png",
        output_dir / "10_arranged_sprite.png",
        output_dir / "10_arranged_sprite_x16.png",
        output_dir / "10_arranged_mask.png",
        output_dir / "10_arranged_mask_rgba.png",
        output_dir / "10_components_debug.png",
        output_dir / "10_clusters_debug.png",
        output_dir / "10_arrange_report.json",
    ]
    stale_paths.extend(output_dir.glob("sprite_x*.png"))
    stale_paths.extend(output_dir.glob("10_arranged_sprite_x*.png"))
    for stale in stale_paths:
        if stale.exists() and stale.is_file():
            stale.unlink()
    elements_dir = output_dir / "10_arranged_elements"
    if elements_dir.exists() and elements_dir.is_dir():
        shutil.rmtree(elements_dir)


def build_pipeline_commands(
    input_path: Path,
    output_dir: Path,
    settings: PipelineSettings,
) -> list[list[str]]:
    clean_output = output_dir / "07_clean_rgba.png"
    unfake_output = output_dir / "08_unfake_pixel_raw.png"
    commands = [
        [
            sys.executable,
            str(ROOT / "tools" / "preclean.py"),
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--bg-tolerance",
            str(settings.bg_tolerance),
            "--background-mode",
            settings.background_mode,
            "--alpha-threshold",
            str(settings.alpha_threshold),
            "--edge-contract",
            str(settings.edge_contract),
            "--outline-width",
            str(settings.outline_width),
        ],
    ]

    if settings.process_mode != "clean":
        unfake_command = [
            "unfake",
            str(clean_output),
            "-o",
            str(unfake_output),
            "--method",
            settings.method,
            "--background-mode",
            settings.background_mode,
            "--background-tolerance",
            str(settings.bg_tolerance),
            "--alpha-threshold",
            str(settings.alpha_threshold),
        ]

        if settings.process_mode == "safe":
            unfake_command.extend(["--scale", "1", "--no-snap"])
        else:
            unfake_command.extend(["--detect", settings.detect])

        if settings.transparent_background:
            unfake_command.append("--transparent-background")
        if settings.auto_colors:
            unfake_command.append("--auto-colors")
        else:
            unfake_command.extend(["--colors", str(settings.colors)])
        if settings.cleanup:
            unfake_command.extend(["--cleanup", settings.cleanup])
        commands.append(unfake_command)

    post_input = unfake_output if settings.process_mode != "clean" else clean_output
    post_command = [
        sys.executable,
        str(ROOT / "tools" / "postcheck.py"),
        str(post_input),
        "--output-dir",
        str(output_dir),
        "--preview-scale",
        str(settings.preview_scale),
        "--max-preview-side",
        str(settings.max_preview_side),
    ]
    if settings.process_mode != "clean" and not settings.auto_colors:
        post_command.extend(["--colors", str(settings.colors)])
    commands.append(post_command)

    if settings.arrange_sprites:
        commands.append(
            [
                sys.executable,
                str(ROOT / "tools" / "arrange_sprites.py"),
                str(output_dir / "sprite.png"),
                "--output-dir",
                str(output_dir),
                "--columns",
                str(settings.arrange_columns),
                "--padding",
                str(settings.arrange_padding),
                "--min-area",
                str(settings.arrange_min_area),
                "--split-mode",
                settings.arrange_split_mode,
                "--merge-gap",
                str(settings.arrange_merge_gap),
                "--cluster-gap",
                str(settings.arrange_cluster_gap),
                "--cluster-gap-ratio",
                str(settings.arrange_cluster_gap_ratio),
                "--preview-scale",
                str(settings.preview_scale),
                "--max-preview-side",
                str(settings.max_preview_side),
            ],
        )
    return commands


def artifact_map(
    output_dir: Path,
    report: dict[str, object] | None = None,
    arrange_report: dict[str, object] | None = None,
    preview_scale: int = 16,
) -> dict[str, Path]:
    unfake_output = output_dir / "08_unfake_pixel_raw.png"
    clean_output = output_dir / "07_clean_rgba.png"
    scale = max(1, int(preview_scale))
    preview_path = Path(str((report or {}).get("preview") or output_dir / f"sprite_x{scale}.png"))
    arranged_preview_path = Path(
        str((arrange_report or {}).get("preview") or output_dir / f"10_arranged_sprite_x{scale}.png"),
    )
    return {
        "clean": clean_output,
        "pixelRaw": unfake_output,
        "sprite": output_dir / "sprite.png",
        "preview": preview_path,
        "mask": output_dir / "02_connected_bg_mask.png",
        "trimap": output_dir / "05_trimap.png",
        "subjectMask": output_dir / "06_subject_mask.png",
        "subjectMaskRgba": output_dir / "06_subject_mask_rgba.png",
        "outlineMask": output_dir / "06_outline_mask.png",
        "outlineMaskRgba": output_dir / "06_outline_mask_rgba.png",
        "arrangedSprite": output_dir / "10_arranged_sprite.png",
        "arrangedPreview": arranged_preview_path,
        "arrangedMask": output_dir / "10_arranged_mask.png",
        "arrangedMaskRgba": output_dir / "10_arranged_mask_rgba.png",
        "componentsDebug": output_dir / "10_components_debug.png",
        "clustersDebug": output_dir / "10_clusters_debug.png",
        "arrangeReport": output_dir / "10_arrange_report.json",
        "report": output_dir / "report.json",
    }


def load_report(output_dir: Path) -> dict[str, object]:
    report_path = output_dir / "report.json"
    if not report_path.exists():
        return {}
    return json.loads(report_path.read_text(encoding="utf-8"))


def run_pipeline(
    input_path: str | Path,
    output_dir: str | Path,
    settings: PipelineSettings,
) -> dict[str, object]:
    input_path = resolve_input_path(str(input_path))
    output_dir = resolve_output_dir(str(output_dir))

    if not input_path.exists():
        return {
            "ok": False,
            "error": f"Input not found: {input_path}",
            "outputDir": str(output_dir),
        }

    cleanup_stale_outputs(output_dir)

    logs = []
    for command in build_pipeline_commands(input_path, output_dir, settings):
        result = run_command(command, ROOT)
        logs.append(result)
        if result["returncode"] != 0:
            return {
                "ok": False,
                "error": "Pipeline command failed.",
                "failedCommand": command,
                "logs": logs,
                "outputDir": str(output_dir),
            }

    report = load_report(output_dir)
    arrange_report_path = output_dir / "10_arrange_report.json"
    arrange_report = {}
    if arrange_report_path.exists():
        arrange_report = json.loads(arrange_report_path.read_text(encoding="utf-8"))
    paths = artifact_map(output_dir, report, arrange_report, settings.preview_scale)
    paths["input"] = input_path
    artifacts = {
        key: artifact(path)
        for key, path in paths.items()
        if path.exists()
    }

    return {
        "ok": True,
        "processMode": settings.process_mode,
        "input": str(input_path),
        "outputDir": str(output_dir),
        "artifacts": artifacts,
        "report": report,
        "arrangeReport": arrange_report,
        "logs": logs,
    }
