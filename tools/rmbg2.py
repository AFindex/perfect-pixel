#!/usr/bin/env python
"""Optional RMBG-2.0 background-removal wrapper.

This module is intentionally independent from the main preclean pipeline so the
RMBG runtime can be installed, warmed up, and tested before pipeline wiring is
finalized.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


MODEL_ID = "briaai/RMBG-2.0"
INPUT_SIZE = (1024, 1024)
NORMALIZE_MEAN = (0.485, 0.456, 0.406)
NORMALIZE_STD = (0.229, 0.224, 0.225)
OPTIONAL_PACKAGES = ("torch", "torchvision", "transformers", "kornia", "huggingface_hub")


class Rmbg2UnavailableError(RuntimeError):
    """Raised when the optional RMBG-2.0 runtime is not available."""


@dataclass(slots=True)
class Rmbg2Settings:
    model_id: str = MODEL_ID
    device: str = "auto"
    input_size: tuple[int, int] = INPUT_SIZE
    cache_dir: Path | None = None
    local_files_only: bool = False
    trust_remote_code: bool = True


def _package_version(package: str) -> str | None:
    distribution_name = "huggingface-hub" if package == "huggingface_hub" else package
    try:
        return importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def dependency_status() -> dict[str, dict[str, object]]:
    return {
        package: {
            "installed": importlib.util.find_spec(package) is not None,
            "version": _package_version(package),
        }
        for package in OPTIONAL_PACKAGES
    }


def missing_dependencies() -> list[str]:
    return [
        package
        for package, status in dependency_status().items()
        if not status["installed"]
    ]


def _import_runtime():
    missing = missing_dependencies()
    if missing:
        joined = ", ".join(missing)
        raise Rmbg2UnavailableError(
            f"RMBG-2.0 optional dependencies are missing: {joined}. "
            "Run `python tools/cli.py init --with-rmbg` first.",
        )

    import torch
    from transformers import AutoModelForImageSegmentation

    return torch, AutoModelForImageSegmentation


def resolve_device(torch: Any, device: str) -> str:
    requested = (device or "auto").lower()
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def preprocess_image(image: Image.Image, torch: Any, input_size: tuple[int, int]):
    rgb = image.convert("RGB").resize(input_size, Image.Resampling.BILINEAR)
    array = np.asarray(rgb, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)
    mean = torch.tensor(NORMALIZE_MEAN, dtype=tensor.dtype).view(1, 3, 1, 1)
    std = torch.tensor(NORMALIZE_STD, dtype=tensor.dtype).view(1, 3, 1, 1)
    return (tensor - mean) / std


def prediction_to_mask(prediction: Any, torch: Any, size: tuple[int, int]) -> Image.Image:
    if isinstance(prediction, (list, tuple)):
        prediction = prediction[-1]
    with torch.no_grad():
        alpha = prediction.sigmoid().detach().float().cpu()
    if alpha.ndim == 4:
        alpha = alpha[0, 0]
    elif alpha.ndim == 3:
        alpha = alpha[0]
    mask_array = np.clip(alpha.numpy() * 255.0, 0, 255).astype(np.uint8)
    mask = Image.fromarray(mask_array, mode="L")
    return mask.resize(size, Image.Resampling.BILINEAR)


def apply_alpha(image: Image.Image, alpha: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    rgba.putalpha(alpha.convert("L"))
    return rgba


class Rmbg2Session:
    def __init__(self, settings: Rmbg2Settings | None = None):
        self.settings = settings or Rmbg2Settings()
        self.torch, self.model_class = _import_runtime()
        if hasattr(self.torch, "set_float32_matmul_precision"):
            self.torch.set_float32_matmul_precision("high")
        self.device = resolve_device(self.torch, self.settings.device)
        self.model = self.model_class.from_pretrained(
            self.settings.model_id,
            cache_dir=str(self.settings.cache_dir) if self.settings.cache_dir else None,
            local_files_only=self.settings.local_files_only,
            trust_remote_code=self.settings.trust_remote_code,
        )
        self.model.to(self.device)
        self.model.eval()

    def predict_alpha(self, image: Image.Image) -> Image.Image:
        tensor = preprocess_image(image, self.torch, self.settings.input_size).to(self.device)
        with self.torch.no_grad():
            prediction = self.model(tensor)
        return prediction_to_mask(prediction, self.torch, image.size)

    def remove_background(self, image: Image.Image) -> Image.Image:
        return apply_alpha(image, self.predict_alpha(image))


def rmbg2_status(load_model: bool = False, settings: Rmbg2Settings | None = None) -> dict[str, object]:
    settings = settings or Rmbg2Settings()
    deps = dependency_status()
    payload: dict[str, object] = {
        "modelId": settings.model_id,
        "inputSize": list(settings.input_size),
        "dependencies": deps,
        "available": all(item["installed"] for item in deps.values()),
        "cacheDir": str(settings.cache_dir) if settings.cache_dir else None,
        "localFilesOnly": settings.local_files_only,
        "trustRemoteCode": settings.trust_remote_code,
        "hfTokenConfigured": bool(
            os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
            or os.environ.get("HUGGINGFACE_HUB_TOKEN")
        ),
    }

    if not payload["available"]:
        payload["missing"] = [package for package, item in deps.items() if not item["installed"]]
        return payload

    try:
        torch, _ = _import_runtime()
        payload["device"] = resolve_device(torch, settings.device)
        payload["cudaAvailable"] = bool(torch.cuda.is_available())
        if load_model:
            session = Rmbg2Session(settings)
            payload["modelLoaded"] = True
            payload["device"] = session.device
    except Exception as exc:  # noqa: BLE001 - status must carry setup failures.
        payload["available"] = False
        payload["error"] = str(exc)
        if load_model:
            payload["modelLoaded"] = False
    return payload


def warmup(settings: Rmbg2Settings | None = None) -> dict[str, object]:
    return rmbg2_status(load_model=True, settings=settings)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RMBG-2.0 optional runtime helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Check optional RMBG-2.0 dependencies.")
    doctor_parser.add_argument("--json", action="store_true")

    warmup_parser = subparsers.add_parser("warmup", help="Download and load RMBG-2.0 once.")
    warmup_parser.add_argument("--json", action="store_true")
    warmup_parser.add_argument("--device", default="auto")
    warmup_parser.add_argument("--cache-dir", type=Path)
    warmup_parser.add_argument("--local-files-only", action="store_true")

    run_parser = subparsers.add_parser("run", help="Run RMBG-2.0 on one image.")
    run_parser.add_argument("input", type=Path)
    run_parser.add_argument("-o", "--output", type=Path, default=Path("build/rmbg2-output.png"))
    run_parser.add_argument("--mask-output", type=Path)
    run_parser.add_argument("--device", default="auto")
    run_parser.add_argument("--cache-dir", type=Path)
    run_parser.add_argument("--local-files-only", action="store_true")
    return parser.parse_args()


def print_payload(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(f"RMBG-2.0 model: {payload.get('modelId', MODEL_ID)}")
    print(f"Available: {'yes' if payload.get('available') else 'no'}")
    if payload.get("device"):
        print(f"Device: {payload['device']}")
    if payload.get("missing"):
        print(f"Missing: {', '.join(str(item) for item in payload['missing'])}")
    if payload.get("error"):
        print(f"Error: {payload['error']}")


def main() -> int:
    args = parse_args()
    if args.command == "doctor":
        print_payload(rmbg2_status(), args.json)
        return 0

    if args.command == "warmup":
        settings = Rmbg2Settings(
            device=args.device,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
        )
        payload = warmup(settings)
        print_payload(payload, args.json)
        return 0 if payload.get("modelLoaded") else 1

    if args.command == "run":
        settings = Rmbg2Settings(
            device=args.device,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
        )
        session = Rmbg2Session(settings)
        image = Image.open(args.input)
        mask = session.predict_alpha(image)
        output = apply_alpha(image, mask)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output.save(args.output)
        if args.mask_output:
            args.mask_output.parent.mkdir(parents=True, exist_ok=True)
            mask.save(args.mask_output)
        print(args.output)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
