"""Strongly typed request models at the web application's public boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ControlMessage(BaseModel):
    action: Literal[
        "start", "pause", "reset", "reset_defaults", "configure", "set_target", "set_lock_mode", "toggle_beacon", "generate_report",
        "start_comm", "stop_comm", "load_preset",
    ]
    config: dict[str, Any] = Field(default_factory=dict)
    target_id: str | None = None
    lock_mode: Literal["auto", "manual"] | None = None
    preset_id: str | None = None


class SessionSummary(BaseModel):
    session_id: str
    started_at: str
    duration: float
    motion: str
    average_error: float
    maximum_error: float
    average_fps: float
    lock_retention: float
    fsoc_link_retention: float

