"""Small filesystem-backed scenario library owned by the independent web app."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


class ScenarioStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def validate_name(name: str) -> str:
        if not SAFE_NAME.fullmatch(name):
            raise ValueError("Scenario names may contain letters, numbers, _ and - only.")
        return name

    def save(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        name = self.validate_name(name)
        document = {"name": name, "saved_at": datetime.now(timezone.utc).isoformat(), "config": config}
        (self.root / f"{name}.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        return document

    def load(self, name: str) -> dict[str, Any]:
        name = self.validate_name(name)
        path = self.root / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(name)
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                entries.append({"name": entry["name"], "saved_at": entry["saved_at"], "config": entry["config"]})
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        return entries

    def delete(self, name: str) -> None:
        name = self.validate_name(name)
        path = self.root / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(name)
        path.unlink()
