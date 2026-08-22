from __future__ import annotations

import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from services.gemini_ocr import EasyOcrCaptchaSolver


async def main() -> None:
    image_directory = Path(__file__).parent / "test-images"
    images = sorted(
        image_directory.glob("*.png"),
        key=lambda path: int(path.stem) if path.stem.isdigit() else path.stem,
    )
    if not images:
        raise RuntimeError(f"No PNG images found in {image_directory}")

    solver = EasyOcrCaptchaSolver()
    print(f"Testing EasyOCR against {len(images)} image(s) in {image_directory}")

    setup_started = time.perf_counter()
    await solver.verify_ready()
    print(f"EasyOCR ready in {time.perf_counter() - setup_started:.2f}s\n")

    failures = 0
    successes = 0
    durations: list[float] = []
    for image in images:
        started = time.perf_counter()
        try:
            code = await solver.solve_image(image.read_bytes(), expected_length=6)
            duration = time.perf_counter() - started
            durations.append(duration)
            expected = image.stem
            outcome = "CORRECT" if code == expected else "WRONG"
            if outcome == "CORRECT":
                successes += 1
            else:
                failures += 1
            print(
                f"{image.name:>10}  expected={expected:<8}  read={code:<8}  "
                f"{outcome:<7}  {duration:.2f}s"
            )
        except Exception as error:
            failures += 1
            duration = time.perf_counter() - started
            durations.append(duration)
            print(f"{image.name:>10}  ERROR: {error}  {duration:.2f}s")

    print(
        "\nResults: "
        f"success={successes}, error={failures}, total={len(images)}, "
        f"accuracy={successes / len(images):.1%}"
    )
    print(
        "Timing: "
        f"avg={statistics.mean(durations):.2f}s, "
        f"median={statistics.median(durations):.2f}s, "
        f"min={min(durations):.2f}s, max={max(durations):.2f}s"
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
