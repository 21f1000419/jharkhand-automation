from __future__ import annotations

import argparse
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

DEFAULT_IMAGES = Path(__file__).resolve().parent / "test-images"
MODEL_OPTIONS = {
    "v6-small": ("PP-OCRv6_small_rec", "Fastest benchmark option; not bundled in the app."),
    "v6-medium": ("PP-OCRv6_medium_rec", "Highest tested accuracy; bundled application model."),
    "v5-server": ("PP-OCRv5_server_rec", "Largest tested model; slowest."),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark PaddleOCR's text recognition model against labeled CAPTCHA images."
    )
    parser.add_argument("--images", type=Path, default=DEFAULT_IMAGES)
    parser.add_argument(
        "--model",
        choices=MODEL_OPTIONS,
        help="PaddleOCR version to benchmark. Omitting this displays an interactive menu.",
    )
    parser.add_argument("--list-models", action="store_true", help="Print model choices and exit.")
    return parser.parse_args()


def print_model_options() -> None:
    print("Available PaddleOCR models:")
    for index, (key, (model_name, description)) in enumerate(MODEL_OPTIONS.items(), start=1):
        print(f"  {index}. {key:<10} {model_name:<28} {description}")


def select_model(selected: str | None) -> str:
    if selected is not None:
        return MODEL_OPTIONS[selected][0]

    print_model_options()
    choices = tuple(MODEL_OPTIONS)
    while True:
        try:
            choice = input("Select a model (1-3): ").strip().lower()
        except EOFError:
            raise SystemExit("Use --model with one of: " + ", ".join(choices))
        if choice in MODEL_OPTIONS:
            return MODEL_OPTIONS[choice][0]
        if choice.isdigit() and 1 <= int(choice) <= len(choices):
            return MODEL_OPTIONS[choices[int(choice) - 1]][0]
        print(f"Invalid choice. Enter 1-{len(choices)} or one of: {', '.join(choices)}")


def normalize(value: object) -> str:
    candidates = re.findall(r"[A-Za-z0-9]+", str(value))
    return candidates[-1] if candidates else ""


def result_text(result: Any) -> str:
    payload = result.json if not callable(getattr(result, "json", None)) else result.json()
    return str(payload["res"]["rec_text"])


def benchmark(image_paths: list[Path], model_name: str) -> None:
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    from paddleocr import TextRecognition

    started = time.perf_counter()
    model = TextRecognition(model_name=model_name, device="cpu", engine="paddle_static")
    setup_seconds = time.perf_counter() - started
    print(f"\nPaddleOCR ({model_name}): initialized in {setup_seconds:.2f}s")

    successes = 0
    durations: list[float] = []
    for image_path in image_paths:
        started = time.perf_counter()
        try:
            output = model.predict(input=str(image_path), batch_size=1)
            actual = normalize(result_text(next(iter(output))))
            elapsed = time.perf_counter() - started
            durations.append(elapsed)
            expected = image_path.stem
            correct = actual == expected
            successes += int(correct)
            print(
                f"{image_path.name:>12}  expected={expected:<8}  read={actual:<8}  "
                f"{'CORRECT' if correct else 'WRONG':<7}  {elapsed:.3f}s"
            )
        except Exception as error:
            elapsed = time.perf_counter() - started
            durations.append(elapsed)
            print(f"{image_path.name:>12}  ERROR: {error}  {elapsed:.3f}s")

    accuracy = successes / len(image_paths)
    print(
        f"PaddleOCR results: correct={successes}/{len(image_paths)}, accuracy={accuracy:.1%}, "
        f"avg={statistics.mean(durations):.3f}s, median={statistics.median(durations):.3f}s"
    )


def main() -> None:
    args = parse_args()
    if args.list_models:
        print_model_options()
        return
    raw_paths = [
        *args.images.glob("*.png"),
        *args.images.glob("*.jpg"),
        *args.images.glob("*.jpeg"),
    ]
    image_paths = sorted(
        raw_paths,
        key=lambda path: getattr(path.stat(), "st_birthtime", path.stat().st_ctime),
        reverse=True,
    )
    if not image_paths:
        raise SystemExit(f"No PNG or JPEG images found in {args.images}")
    benchmark(image_paths, select_model(args.model))


if __name__ == "__main__":
    main()
