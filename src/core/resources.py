from __future__ import annotations

import sys
from pathlib import Path


def bundled_path(relative_path: str) -> Path:
    """Return a project asset in source runs and in the packaged executable."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative_path
