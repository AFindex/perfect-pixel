#!/usr/bin/env python
"""Local web server that can execute the Perfect Pixel pipeline."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "build" / "perfect-pixel"
ALLOWED_FILE_ROOTS = {ROOT.resolve()}

app = Flask(__name__, static_folder=None)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve and run the Perfect Pixel web tool.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def bool_form(name: str, default: bool = False) -> bool:
    value = request.form.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def int_form(name: str, default: int) -> int:
    try:
        return int(request.form.get(name, default))
    except (TypeError, ValueError):
        return default


def float_form(name: str, default: float) -> float:
    try:
        return float(request.form.get(name, default))
    except (TypeError, ValueError):
        return default


def text_form(name: str, default: str) -> str:
    value = request.form.get(name, default)
    return value.strip() or default


def resolve_output_dir(value: str) -> Path:
    output = Path(value)
    if not output.is_absolute():
        output = ROOT / output
    output.mkdir(parents=True, exist_ok=True)
    resolved = output.resolve()
    ALLOWED_FILE_ROOTS.add(resolved)
    return resolved


def resolve_plain_dir(value: str) -> Path:
    directory = Path(value)
    if not directory.is_absolute():
        directory = ROOT / directory
    return directory.resolve()


def sanitize_subdir(value: str) -> str:
    name = (value or "0001").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
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
    return f"{prefix}{max(0, number):0{width}d}{suffix}"


def is_allowed_file(path: Path) -> bool:
    resolved = path.resolve()
    return any(is_inside(resolved, root) for root in ALLOWED_FILE_ROOTS)


def is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


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


def save_upload(output_dir: Path) -> Path:
    upload = request.files.get("image")
    if upload and upload.filename:
        filename = secure_filename(Path(upload.filename).name) or "input.png"
        upload_dir = output_dir / "inputs"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / filename
        upload.save(target)
        return target.resolve()

    input_path = Path(text_form("input_path", "input.png"))
    if not input_path.is_absolute():
        input_path = ROOT / input_path
    return input_path.resolve()


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "root": str(ROOT), "python": sys.executable})


@app.get("/api/pick-output-dir")
def pick_output_dir():
    initial = request.args.get("initial", str(DEFAULT_OUTPUT))
    initial_path = Path(initial)
    if not initial_path.is_absolute():
        initial_path = ROOT / initial_path
    initial_path.mkdir(parents=True, exist_ok=True)

    try:
        import tkinter as tk
        from tkinter import filedialog

        dialog = tk.Tk()
        dialog.withdraw()
        dialog.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=str(initial_path), title="选择输出目录")
        dialog.destroy()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    if not selected:
        return jsonify({"ok": True, "path": ""})

    path = Path(selected).resolve()
    ALLOWED_FILE_ROOTS.add(path)
    return jsonify({"ok": True, "path": str(path)})


@app.get("/api/next-output-subdir")
def next_output_subdir():
    base = resolve_plain_dir(request.args.get("base", str(DEFAULT_OUTPUT)))
    current = sanitize_subdir(request.args.get("current", "0001"))
    prefix, number_text, number, suffix = split_index_name(current)
    width = len(number_text)

    if not base.exists() or not base.is_dir():
        return jsonify({"ok": True, "subdir": current, "base": str(base)})

    matcher = re.compile(rf"^{re.escape(prefix)}(\d+){re.escape(suffix)}$")
    used = set()
    for child in base.iterdir():
        if not child.is_dir():
            continue
        match = matcher.match(child.name)
        if match:
            used.add(int(match.group(1)))

    candidate = number
    if candidate in used:
        candidate += 1
    while candidate in used:
        candidate += 1

    subdir = format_index_name(prefix, width, candidate, suffix)
    return jsonify({"ok": True, "subdir": subdir, "base": str(base)})


@app.route("/api/run", methods=["POST", "OPTIONS"])
def run_pipeline():
    if request.method == "OPTIONS":
        return ("", 204)

    output_dir = resolve_output_dir(text_form("output_dir", str(DEFAULT_OUTPUT.relative_to(ROOT))))
    input_path = save_upload(output_dir)
    if not input_path.exists():
        return jsonify({"ok": False, "error": f"Input not found: {input_path}"}), 400

    bg_tolerance = int_form("bg_tolerance", 14)
    alpha_threshold = int_form("alpha_threshold", 128)
    edge_contract = int_form("edge_contract", 1)
    outline_width = int_form("outline_width", 2)
    background_mode = text_form("background_mode", "edges")
    process_mode = text_form("process_mode", "clean")
    detect = text_form("detect", "auto")
    method = text_form("method", "nearest")
    auto_colors = bool_form("auto_colors", False)
    colors = int_form("colors", 256)
    transparent = bool_form("transparent_background", True)
    cleanup = text_form("cleanup", "")
    max_preview_side = int_form("max_preview_side", 4096)
    arrange_sprites = bool_form("arrange_sprites", False)
    arrange_columns = int_form("arrange_columns", 0)
    arrange_padding = int_form("arrange_padding", 2)
    arrange_min_area = int_form("arrange_min_area", 16)
    arrange_merge_gap = int_form("arrange_merge_gap", 2)
    arrange_split_mode = text_form("arrange_split_mode", "clustered")
    arrange_cluster_gap = int_form("arrange_cluster_gap", 18)
    arrange_cluster_gap_ratio = float_form("arrange_cluster_gap_ratio", 0.5)

    unfake_output = output_dir / "08_unfake_pixel_raw.png"
    clean_output = output_dir / "07_clean_rgba.png"
    stale_paths = [
        clean_output,
        unfake_output,
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

    commands = [
        [
            sys.executable,
            str(ROOT / "tools" / "preclean.py"),
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--bg-tolerance",
            str(bg_tolerance),
            "--background-mode",
            background_mode,
            "--alpha-threshold",
            str(alpha_threshold),
            "--edge-contract",
            str(edge_contract),
            "--outline-width",
            str(outline_width),
        ],
    ]

    if process_mode != "clean":
        unfake_command = [
            "unfake",
            str(clean_output),
            "-o",
            str(unfake_output),
            "--method",
            method,
            "--background-mode",
            background_mode,
            "--background-tolerance",
            str(bg_tolerance),
            "--alpha-threshold",
            str(alpha_threshold),
        ]

        if process_mode == "safe":
            unfake_command.extend(["--scale", "1", "--no-snap"])
        else:
            unfake_command.extend(["--detect", detect])

        if transparent:
            unfake_command.append("--transparent-background")
        if auto_colors:
            unfake_command.append("--auto-colors")
        else:
            unfake_command.extend(["--colors", str(colors)])
        if cleanup:
            unfake_command.extend(["--cleanup", cleanup])
        commands.append(unfake_command)

    post_input = unfake_output if process_mode != "clean" else clean_output
    post_command = [
        sys.executable,
        str(ROOT / "tools" / "postcheck.py"),
        str(post_input),
        "--output-dir",
        str(output_dir),
        "--preview-scale",
        "16",
        "--max-preview-side",
        str(max_preview_side),
    ]
    if process_mode != "clean" and not auto_colors:
        post_command.extend(["--colors", str(colors)])
    commands.append(post_command)

    if arrange_sprites:
        commands.append(
            [
                sys.executable,
                str(ROOT / "tools" / "arrange_sprites.py"),
                str(output_dir / "sprite.png"),
                "--output-dir",
                str(output_dir),
                "--columns",
                str(arrange_columns),
                "--padding",
                str(arrange_padding),
                "--min-area",
                str(arrange_min_area),
                "--split-mode",
                arrange_split_mode,
                "--merge-gap",
                str(arrange_merge_gap),
                "--cluster-gap",
                str(arrange_cluster_gap),
                "--cluster-gap-ratio",
                str(arrange_cluster_gap_ratio),
                "--preview-scale",
                "16",
                "--max-preview-side",
                str(max_preview_side),
            ],
        )

    logs = []
    for command in commands:
        result = run_command(command, ROOT)
        logs.append(result)
        if result["returncode"] != 0:
            return jsonify(
                {
                    "ok": False,
                    "error": "Pipeline command failed.",
                    "failedCommand": command,
                    "logs": logs,
                    "outputDir": str(output_dir),
                },
            ), 500

    report = {}
    report_path = output_dir / "report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    arrange_report = {}
    arrange_report_path = output_dir / "10_arrange_report.json"
    if arrange_report_path.exists():
        arrange_report = json.loads(arrange_report_path.read_text(encoding="utf-8"))

    preview_path = Path(str(report.get("preview") or output_dir / "sprite_x16.png"))
    arranged_preview_path = Path(str(arrange_report.get("preview") or output_dir / "10_arranged_sprite_x16.png"))
    artifact_paths = {
        "input": input_path,
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
    artifacts = {
        key: artifact(path)
        for key, path in artifact_paths.items()
        if path.exists()
    }

    return jsonify(
        {
            "ok": True,
            "processMode": process_mode,
            "input": str(input_path),
            "outputDir": str(output_dir),
            "artifacts": artifacts,
            "report": report,
            "arrangeReport": arrange_report,
            "logs": logs,
        },
    )


@app.get("/api/file")
def file_by_path():
    raw_path = request.args.get("path", "")
    path = Path(raw_path).resolve()
    if not path.exists() or not path.is_file() or not is_allowed_file(path):
        return jsonify({"ok": False, "error": "File is unavailable."}), 404
    return send_file(path)


@app.get("/<path:asset_path>")
def static_asset(asset_path: str):
    path = (ROOT / asset_path).resolve()
    if not is_inside(path, ROOT) or not path.is_file():
        return jsonify({"ok": False, "error": "Not found."}), 404
    return send_from_directory(ROOT, asset_path)


if __name__ == "__main__":
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=False)
