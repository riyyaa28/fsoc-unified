"""Persistent session telemetry storage; generated files stay below web_app/."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


class PerformanceLogger:
    def __init__(self, reports_dir: Path, logs_dir: Path, session_id: str) -> None:
        self.reports_dir, self.logs_dir, self.session_id = reports_dir, logs_dir, session_id
        reports_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = reports_dir / f"{session_id}.csv"
        self.json_path = reports_dir / f"{session_id}.json"
        self._rows: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []

    def record(self, row: dict[str, Any]) -> None:
        self._rows.append(row)

    def event(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self._rows

    @property
    def events(self) -> list[dict[str, Any]]:
        return self._events

    def save(self, summary: dict[str, Any], config: dict[str, Any]) -> None:
        fields = sorted({key for row in self._rows for key in row})
        with self.csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self._rows)
        self.json_path.write_text(json.dumps({"summary": summary, "config": config, "events": self._events}, indent=2), encoding="utf-8")
