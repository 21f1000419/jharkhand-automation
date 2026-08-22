from __future__ import annotations

import argparse
import re
import statistics
import time
from pathlib import Path

import ddddocr

DEFAULT_IMAGES = Path(__file__).resolve().parent / "test-images"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the installed ddddocr model against labeled CAPTCHA images."
    )
    parser.add_argument(
        "--images",
        type=Path,
        default=DEFAULT_IMAGES,
        help=(
            "Directory containing images named with their expected CAPTCHA value "
            f"(default: {DEFAULT_IMAGES})"
        ),
    )
    return parser.parse_args()


def normalize(value: object) -> str:
    candidates = re.findall(r"[A-Za-z0-9]+", str(value))
    return candidates[-1] if candidates else ""


def benchmark(image_paths: list[Path]) -> None:
    started = time.perf_counter()
    ocr = ddddocr.DdddOcr(show_ad=False)
    setup_seconds = time.perf_counter() - started

    print(f"\nddddocr: initialized in {setup_seconds:.2f}s")
    successes = 0
    durations: list[float] = []
    for image_path in image_paths:
        expected = image_path.stem
        started = time.perf_counter()
        try:
            actual = normalize(ocr.classification(image_path.read_bytes()))
            elapsed = time.perf_counter() - started
            durations.append(elapsed)
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
        f"ddddocr results: correct={successes}/{len(image_paths)}, accuracy={accuracy:.1%}, "
        f"avg={statistics.mean(durations):.3f}s, median={statistics.median(durations):.3f}s"
    )


def main() -> None:
    args = parse_args()
    image_paths = sorted(
        [*args.images.glob("*.png"), *args.images.glob("*.jpg"), *args.images.glob("*.jpeg")]
    )
    if not image_paths:
        raise SystemExit(f"No PNG or JPEG images found in {args.images}")

    print(f"Benchmarking {len(image_paths)} labeled image(s) from {args.images}")
    benchmark(image_paths)


if __name__ == "__main__":
    main()
