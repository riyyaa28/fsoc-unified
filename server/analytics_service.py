"""Session analytics: measured metrics, bounded live history, events, summary.

All tracking metrics come from ``vision.tracking_core.PerformanceMeter`` - the
same meter the video benchmark uses - so simulation and video results are
directly comparable.  Nothing here is hard-coded.
"""

from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vision.tracking_core import PerformanceMeter, sih_compliance

from .performance_logger import PerformanceLogger

HISTORY_KEYS = (
    "timestamp", "target_azimuth", "target_elevation", "camera_pan", "camera_tilt", "pixel_error",
    "angular_error", "pan_error", "tilt_error", "confidence", "fps", "processing_time_ms",
    "disturbance_level", "fsoc_link", "beacon_hidden", "locked", "detected", "pan_rate", "tilt_rate",
)


class AnalyticsService:
    def __init__(self, reports_dir: Path, logs_dir: Path) -> None:
        self.reports_dir, self.logs_dir = reports_dir, logs_dir
        self.start(config={})

    def start(self, config: dict[str, Any]) -> None:
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6]
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.logger = PerformanceLogger(self.reports_dir, self.logs_dir, self.session_id)
        self.meter = PerformanceMeter()
        self.rows: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self._previous: dict[str, bool] = {}
        self.config = config

    def add_event(self, timestamp: float, message: str, category: str = "system") -> None:
        event = {"timestamp": round(timestamp, 2), "message": message, "category": category}
        self.events.append(event)
        self.events = self.events[-150:]
        self.logger.event(event)

    def begin_acquisition(self, timestamp: float) -> None:
        self.meter.begin_acquisition(timestamp)

    def _transition(self, name: str, state: bool, timestamp: float, on: str, off: str, category: str) -> None:
        previous = self._previous.get(name)
        if previous is not None and previous != state:
            self.add_event(timestamp, on if state else off, category)
        self._previous[name] = state

    def record(self, row: dict[str, Any]) -> None:
        t = float(row["timestamp"])
        for message in self.meter.record(
            t, float(row.get("dt", 0.0)), float(row.get("processing_time_ms", 0.0)),
            bool(row["locked"]), bool(row["detected"]),
            row.get("pixel_error") if row.get("error_valid", True) else None,
            available=bool(row.get("target_observable", True)),
            centred=bool(row.get("centred", row["locked"])),
        ):
            self.add_event(t, f"{row.get('target_id', 'Target')}: {message}", "tracking")
        self._transition("fov_ok", bool(row["fov_ok"]), t, "Target inside camera FOV", "Target outside camera FOV", "geometry")
        self._transition("los_clear", bool(row["los_clear"]), t, "LOS clear", "LOS blocked", "geometry")
        self.rows.append(row)
        self.logger.record(row)
        self.history.append({key: row.get(key) for key in HISTORY_KEYS})
        self.history = self.history[-200:]

    def summary(self) -> dict[str, Any]:
        rows = self.rows
        base: dict[str, Any] = {"session_id": self.session_id, "started_at": self.started_at, "source": "3d_simulation"}
        meter = self.meter.summary()
        base.update(meter)
        if not rows:
            base["duration"] = 0.0
            return base
        n = len(rows)
        confidence = [float(row.get("confidence", 0.0)) for row in rows]
        base.update({
            "duration": meter["duration_s"],
            "total_frames_processed": n,
            "average_fps": meter["loop_fps"],
            "fov_compliance": round(100 * sum(bool(r["fov_ok"]) for r in rows) / n, 2),
            "los_availability": round(100 * sum(bool(r["los_clear"]) for r in rows) / n, 2),
            "fsoc_link_retention_rate": round(100 * sum(bool(r["fsoc_link"]) for r in rows) / n, 2),
            "average_detection_confidence": round(statistics.fmean(confidence), 3),
            "target_loss_count": sum(1 for e in self.events if e["message"].endswith("Lock lost")),
            # Legacy keys kept for existing consumers (reports list, CSV readers).
            "average_tracking_error": meter["error_mean_px"],
            "maximum_tracking_error": meter["error_max_px"],
            "rms_tracking_error": meter["error_rmse_px"],
            "acquisition_time": meter["acquisition_time_s"],
            "lock_retention_rate": meter["lock_retention_pct"],
            "average_processing_time": meter["processing_ms_mean"],
        })
        base["sih"] = sih_compliance(meter)
        return base

    def finalize(self, config: dict[str, Any]) -> dict[str, Any]:
        summary = self.summary()
        self.logger.save(summary, config)
        return summary
