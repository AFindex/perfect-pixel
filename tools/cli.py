"""Command line interface for Perfect Pixel."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

if __package__ in {None, ""}:
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

from tools.pipeline import (  # noqa: E402
    DEFAULT_OUTPUT,
    PipelineSettings,
    next_available_subdir,
    resolve_output_dir,
    run_pipeline as execute_pipeline,
)


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_FLAG = "PERFECT_PIXEL_BOOTSTRAPPED"


def venv_dir() -> Path:
    return ROOT / ".venv"


def venv_python() -> Path:
    if os.name == "nt":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def in_project_venv() -> bool:
    try:
        return Path(sys.executable).resolve().is_relative_to(venv_dir().resolve())
    except AttributeError:
        return str(Path(sys.executable).resolve()).startswith(str(venv_dir().resolve()))
    except ValueError:
        return False


def run_checked(command: list[str], *, cwd: Path = ROOT) -> None:
    completed = subprocess.run(command, cwd=str(cwd))
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def ensure_venv_exists() -> None:
    if venv_python().exists():
        return
    builder = venv.EnvBuilder(with_pip=True, clear=False)
    builder.create(str(venv_dir()))


def install_environment() -> None:
    ensure_venv_exists()
    py = str(venv_python())
    run_checked([py, "-m", "pip", "install", "--upgrade", "pip"])
    run_checked([py, "-m", "pip", "install", "-e", str(ROOT)])


def venv_healthy() -> bool:
    py = venv_python()
    if not py.exists():
        return False
    probe = subprocess.run(
        [
            str(py),
            "-c",
            "import flask, numpy, cv2, PIL, scipy, sklearn",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def ensure_runtime(allow_reexec: bool = True) -> None:
    if os.environ.get(BOOTSTRAP_FLAG) == "1":
        return
    if in_project_venv():
        if not venv_healthy():
            install_environment()
        return
    if not venv_healthy():
        install_environment()
    if allow_reexec:
        os.environ[BOOTSTRAP_FLAG] = "1"
        os.execv(str(venv_python()), [str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]])


def check_unfake() -> tuple[bool, str]:
    path = shutil.which("unfake")
    return (path is not None, path or "unfake not found on PATH")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perfect-pixel",
        description="CLI for the Perfect Pixel workflow.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Bootstrap the local Python environment.")
    init_parser.add_argument("--force", action="store_true", help="Reinstall dependencies even if the venv exists.")

    doctor_parser = subparsers.add_parser("doctor", help="Check local environment and tool availability.")
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    run_parser = subparsers.add_parser("run", help="Run the preprocessing pipeline from the command line.")
    run_parser.add_argument("input", help="Input image path.")
    run_parser.add_argument(
        "-o",
        "--output-root",
        default=str(DEFAULT_OUTPUT),
        help="Base output directory that will contain subfolders.",
    )
    run_parser.add_argument(
        "--output-dir",
        help="Use a full output path directly and skip subfolder composition.",
    )
    run_parser.add_argument(
        "-s",
        "--subdir",
        default="0001",
        help="Batch subfolder name under the output root.",
    )
    run_parser.add_argument(
        "--next-subdir",
        action="store_true",
        help="Pick the next available subfolder index under the output root.",
    )
    run_parser.add_argument(
        "--process-mode",
        choices=["clean", "safe", "pixel"],
        default="clean",
        help="Pipeline mode.",
    )
    run_parser.add_argument("--bg-tolerance", type=int, default=14)
    run_parser.add_argument(
        "--background-mode",
        choices=["edges", "corners", "midpoints"],
        default="edges",
    )
    run_parser.add_argument("--alpha-threshold", type=int, default=128)
    run_parser.add_argument("--edge-contract", type=int, default=1)
    run_parser.add_argument("--outline-width", type=int, default=2)
    run_parser.add_argument(
        "--method",
        choices=["nearest", "content-adaptive", "dominant", "median", "mode", "mean"],
        default="nearest",
    )
    run_parser.add_argument("--detect", choices=["auto", "runs", "edge"], default="auto")
    run_parser.add_argument("--auto-colors", action="store_true")
    run_parser.add_argument("--colors", type=int, default=256)
    run_parser.add_argument("--transparent-background", action="store_true", default=True)
    run_parser.add_argument("--opaque-background", dest="transparent_background", action="store_false")
    run_parser.add_argument("--cleanup", default="", help="Comma separated unfake cleanup flags.")
    run_parser.add_argument("--preview-scale", type=int, default=16)
    run_parser.add_argument("--max-preview-side", type=int, default=4096, help="Cap preview image longest side.")
    run_parser.add_argument(
        "--arrange-sprites",
        action="store_true",
        help="Split visible mask components and arrange them into a uniform sprite sheet.",
    )
    run_parser.add_argument(
        "--arrange-columns",
        type=int,
        default=0,
        help="Column count for arranged sprites. 0 means auto square layout.",
    )
    run_parser.add_argument("--arrange-padding", type=int, default=2, help="Cell padding for arranged sprites.")
    run_parser.add_argument("--arrange-min-area", type=int, default=16, help="Ignore smaller mask islands.")
    run_parser.add_argument("--arrange-merge-gap", type=int, default=2, help="Merge nearby mask islands before splitting.")
    run_parser.add_argument(
        "--arrange-split-mode",
        choices=["auto", "clustered", "connected"],
        default="auto",
        help="Infer grouping automatically, use explicit clustered grouping, or legacy connected splitting.",
    )
    run_parser.add_argument("--arrange-cluster-gap", type=int, default=18, help="Preferred clustered grouping gap.")
    run_parser.add_argument(
        "--arrange-cluster-gap-ratio",
        type=float,
        default=0.5,
        help="Dynamic clustered gap as a fraction of median component size.",
    )
    run_parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")

    web_parser = subparsers.add_parser("web", help="Start the local web interface.")
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument("--port", type=int, default=8765)

    return parser


def handle_init(force: bool) -> int:
    if force or not venv_healthy():
        install_environment()
    ok, unfake_message = check_unfake()
    payload = {
        "ok": True,
        "venv": str(venv_dir()),
        "python": str(venv_python()),
        "unfake": unfake_message if ok else unfake_message,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def handle_doctor(as_json: bool) -> int:
    ok, unfake_message = check_unfake()
    payload = {
        "repoRoot": str(ROOT),
        "python": sys.executable,
        "inProjectVenv": in_project_venv(),
        "venvExists": venv_python().exists(),
        "venvHealthy": venv_healthy(),
        "unfakeAvailable": ok,
        "unfake": unfake_message,
        "outputRoot": str(DEFAULT_OUTPUT),
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"Repo root: {payload['repoRoot']}")
    print(f"Python: {payload['python']}")
    print(f"Project venv: {'yes' if payload['inProjectVenv'] else 'no'}")
    print(f".venv exists: {'yes' if payload['venvExists'] else 'no'}")
    print(f".venv healthy: {'yes' if payload['venvHealthy'] else 'no'}")
    print(f"unfake: {payload['unfake']}")
    print(f"Default output: {payload['outputRoot']}")
    return 0


def handle_run(args: argparse.Namespace) -> int:
    if args.output_dir:
        effective_output = resolve_output_dir(args.output_dir)
    else:
        output_root = resolve_output_dir(args.output_root)
        subdir = next_available_subdir(output_root, args.subdir) if args.next_subdir else args.subdir
        effective_output = resolve_output_dir(str(output_root / subdir))

    ensure_runtime(allow_reexec=True)

    if args.process_mode != "clean" and not check_unfake()[0]:
        raise SystemExit("unfake was not found on PATH. Install it before using safe/pixel modes.")

    result = execute_pipeline(
        args.input,
        effective_output,
        PipelineSettings(
            bg_tolerance=args.bg_tolerance,
            alpha_threshold=args.alpha_threshold,
            edge_contract=args.edge_contract,
            outline_width=args.outline_width,
            background_mode=args.background_mode,
            process_mode=args.process_mode,
            detect=args.detect,
            method=args.method,
            auto_colors=args.auto_colors,
            colors=args.colors,
            transparent_background=args.transparent_background,
            cleanup=args.cleanup,
            preview_scale=args.preview_scale,
            max_preview_side=args.max_preview_side,
            arrange_sprites=args.arrange_sprites,
            arrange_columns=args.arrange_columns,
            arrange_padding=args.arrange_padding,
            arrange_min_area=args.arrange_min_area,
            arrange_merge_gap=args.arrange_merge_gap,
            arrange_split_mode=args.arrange_split_mode,
            arrange_cluster_gap=args.arrange_cluster_gap,
            arrange_cluster_gap_ratio=args.arrange_cluster_gap_ratio,
        ),
    )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        print(result.get("error", "Pipeline failed."))
        if result.get("outputDir"):
            print(f"Output: {result['outputDir']}")
        return 1

    print(f"Output: {result['outputDir']}")
    artifacts = result.get("artifacts", {})
    if artifacts:
        print("Artifacts:")
        for key in [
            "clean",
            "sprite",
            "preview",
            "pixelRaw",
            "arrangedSprite",
            "arrangedPreview",
            "arrangedMaskRgba",
            "componentsDebug",
            "clustersDebug",
            "subjectMaskRgba",
            "outlineMaskRgba",
            "arrangeReport",
            "report",
        ]:
            item = artifacts.get(key)
            if item:
                print(f"  - {key}: {item['displayPath']}")
        arrange_report = result.get("arrangeReport") or {}
        elements_dir = arrange_report.get("elements_dir")
        if elements_dir:
            print(f"  - arrangedElements: {elements_dir}")
    return 0


def handle_web(args: argparse.Namespace) -> int:
    ensure_runtime(allow_reexec=True)
    command = [
        sys.executable,
        str(ROOT / "tools" / "server.py"),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    completed = subprocess.run(command, cwd=str(ROOT))
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return handle_init(args.force)
    if args.command == "doctor":
        return handle_doctor(args.json)
    if args.command == "run":
        return handle_run(args)
    if args.command == "web":
        return handle_web(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
