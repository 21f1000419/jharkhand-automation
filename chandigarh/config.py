from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ChandigarhConfig:
    chrome_executable: str = ""
    last_state: str = ""
    last_article: str = ""
    last_csv_path: str = ""
    last_browser_path: str = ""
    last_time: str = "14:00"
    last_collection_mode: str = "SELF"
    last_sro_location: str = ""
    courier_address_line1: str = ""
    courier_address_line2: str = ""
    courier_landmark: str = ""
    courier_city: str = ""
    courier_pin: str = ""
    capture_references: bool = True


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_directory() / "settings.json"

    def load(self) -> ChandigarhConfig:
        config = ChandigarhConfig()
        if not self.path.is_file():
            return config
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = set(asdict(config))
            return ChandigarhConfig(**{key: value for key, value in values.items() if key in allowed})
        except (OSError, ValueError, TypeError):
            return config

    def save(self, config: ChandigarhConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
        os.replace(temporary, self.path)


def app_data_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "Compitcom" / "ChandigarhEStampAutomation"
