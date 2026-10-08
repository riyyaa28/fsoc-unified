"""Single-port FSOC web application: API, WebSocket, reports and built UI."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from disturbance.sensor_model import active_labels

from .analytics_service import AnalyticsService
from .presets import PRESETS
from .report_generator import ReportGenerator
from .scenario_store import ScenarioStore
from .schemas import ControlMessage
from .state import SimulationService
from .video_benchmark import VideoBenchmark
from .websocket import ConnectionManager

ROOT = Path(__file__).resolve().parents[1]
# A PyInstaller build unpacks into a temporary folder, so keep user data next to the .exe instead.
DATA = Path(sys.executable).resolve().parent / "FSOC_data" if getattr(sys, "frozen", False) else ROOT
REPORTS, LOGS, SCENARIOS, WEB_DIST, UPLOADS = DATA / "reports", DATA / "logs", DATA / "scenarios", ROOT / "web" / "dist", DATA / "uploads"
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv"}
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")
# Telemetry is the app's main bandwidth cost. FSOC_STREAM_HZ caps how often it is
# sent (0 = every simulation tick); the simulation itself always runs at full rate.
STREAM_HZ = float(os.getenv("FSOC_STREAM_HZ", "0"))
# Large, slow-changing fields are sent at most this often (seconds); clients keep their last copy.
SLOW_FIELDS = {"history": 1.0, "trajectories": 0.5, "camera_trajectory": 0.5}


class Hub:
    def __init__(self) -> None:
        self.analytics = AnalyticsService(REPORTS, LOGS)
        self.simulation = SimulationService(self.analytics)
        self.connections = ConnectionManager()
        self.reports = ReportGenerator(REPORTS)
        self.scenarios = ScenarioStore(SCENARIOS)
        self.video = VideoBenchmark(UPLOADS, REPORTS)
        self.task: asyncio.Task[None] | None = None
        self.last_tick = time.perf_counter()
        self.next_send = 0.0
        self.slow_sent = dict.fromkeys(SLOW_FIELDS, 0.0)

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="fsoc-supplied-core-simulation")

    async def stop(self) -> None:
        self.video.stop()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        # Fixed-rate loop at the configured camera update rate (>= 20 Hz).
        # Sleep only for the time left in the period so processing time does
        # not lower the achieved rate; the actual rate is measured, not assumed.
        while True:
            now = time.perf_counter()
            state = self.simulation.step(now - self.last_tick)
            self.last_tick = now
            if self.video.active:
                state["video"] = self.video.snapshot()
            if self.connections.clients and now >= self.next_send:
                if STREAM_HZ > 0:
                    gap = 1.0 / STREAM_HZ
                    # Keep an even cadence; after a stall, restart from now instead of bursting to catch up.
                    self.next_send = self.next_send + gap if now - self.next_send < gap else now + gap
                await self.connections.broadcast(self._without_stale_slow_fields(state, now), state)
            period = 1.0 / float(self.simulation.config["camera"]["update_rate_hz"])
            await asyncio.sleep(max(0.001, period - (time.perf_counter() - now)))

    def _without_stale_slow_fields(self, state: dict[str, Any], now: float) -> dict[str, Any]:
        light = dict(state)
        for key, interval in SLOW_FIELDS.items():
            if now - self.slow_sent[key] >= interval:
                self.slow_sent[key] = now
            else:
                light.pop(key, None)
        return light

    def export_current(self) -> dict[str, Any]:
        if not self.analytics.rows:
            raise ValueError("Start the simulation before exporting a report.")
        config = self.simulation.config
        summary = self.analytics.finalize(config)
        summary["scenario"] = self.simulation.scenario.get("name")
        summary["selected_beacon"] = config["comm"]["target_id"]
        sid = self.analytics.session_id
        self.reports.generate(sid, summary, config, self.analytics.rows, self.analytics.events, self.simulation.scenario, active_labels(config["disturbances"]))
        document = {"summary": summary, "config": config, "scenario": self.simulation.scenario, "events": self.analytics.events}
        (REPORTS / f"{sid}.json").write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")
        return {"session_id": sid, "csv": f"/api/performance/{sid}/csv", "pdf": f"/api/performance/{sid}/pdf", "json": f"/api/performance/{sid}/json"}

    def video_settings(self, overrides: dict[str, Any]) -> dict[str, Any]:
        c = self.simulation.config
        return {
            "pacing": overrides.get("pacing") if overrides.get("pacing") in ("realtime", "max") else "realtime",
            "apply_disturbances": bool(overrides.get("apply_disturbances", c["video"]["apply_disturbances"])),
            "detector": overrides.get("detector", c["tracking"]["detector"]),
            "disturbances": c["disturbances"], "fov_h_deg": c["camera"]["fov_h_deg"], "fov_v_deg": c["camera"]["fov_v_deg"],
            "max_pan_dps": c["camera"]["max_pan_dps"], "max_tilt_dps": c["camera"]["max_tilt_dps"],
            "target_size_px": c["targets"]["size_px"], "confirm_frames": c["tracking"]["confirm_frames"],
            "coast_frames": c["tracking"]["coast_frames"], "link_threshold_px": c["tracking"]["link_threshold_px"],
            "scenario": self.simulation.scenario.get("name"),
        }

    def handle(self, message: ControlMessage) -> None:
        if message.action == "start": self.simulation.start()
        elif message.action == "pause": self.simulation.running = False
        elif message.action == "reset":
            if self.analytics.rows: self.export_current()
            self.simulation.reset(new_session=True)
        elif message.action == "reset_defaults": self.simulation.restore_defaults()
        elif message.action == "configure": self.simulation.configure(message.config)
        elif message.action == "set_target" and message.target_id: self.simulation.set_target(message.target_id)
        elif message.action == "set_lock_mode" and message.lock_mode: self.simulation.set_lock_mode(message.lock_mode)
        elif message.action == "toggle_beacon": self.simulation.toggle_beacon()
        elif message.action == "generate_report": self.export_current()
        elif message.action == "start_comm": self.simulation.start_comm(message.target_id)
        elif message.action == "stop_comm": self.simulation.stop_comm()
        elif message.action == "load_preset" and message.preset_id:
            if not self.simulation.load_preset(message.preset_id): raise ValueError(f"Unknown scenario preset {message.preset_id}")


hub = Hub()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await hub.start()
    yield
    await hub.stop()


app = FastAPI(title="AI-Based Virtual Camera Tracking System for Mobile FSOC", version="2.0", lifespan=lifespan)


def safe_session(session_id: str) -> str:
    if not SESSION_ID.fullmatch(session_id): raise HTTPException(400, "Invalid session identifier")
    return session_id


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "clients": len(hub.connections.clients), "session_id": hub.analytics.session_id, "core": "shared-tracking-core", "video": hub.video.status}


@app.get("/api/performance/current")
async def current_performance() -> dict[str, Any]:
    return {"session_id": hub.analytics.session_id, "summary": hub.analytics.summary(), "events": hub.analytics.events, "has_telemetry": bool(hub.analytics.rows)}


@app.get("/api/performance/sessions")
async def sessions() -> list[dict[str, Any]]:
    entries = []
    for path in sorted(REPORTS.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try: entries.append(json.loads(path.read_text(encoding="utf-8"))["summary"])
        except (OSError, KeyError, json.JSONDecodeError): continue
    return entries


@app.get("/api/presets")
async def presets() -> list[dict[str, Any]]: return PRESETS


@app.post("/api/performance/{session_id}/generate-pdf")
async def generate_pdf(session_id: str) -> dict[str, Any]:
    safe_session(session_id)
    try: return hub.export_current()
    except ValueError as error: raise HTTPException(400, str(error)) from error


@app.post("/api/video/upload")
async def video_upload(request: Request, filename: str = "upload.mp4") -> dict[str, Any]:
    name = Path(filename).name
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_EXTENSIONS: raise HTTPException(400, "Upload an .mp4 (or .avi/.mov/.mkv) video file.")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    destination = UPLOADS / f"{int(time.time())}_{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}"
    size = 0
    with destination.open("wb") as stream:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                stream.close(); destination.unlink(missing_ok=True)
                raise HTTPException(413, "Video is larger than 1 GB.")
            stream.write(chunk)
    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded file is empty.")
    try: meta = await asyncio.to_thread(hub.video.load, destination, name)
    except ValueError as error: raise HTTPException(400, str(error)) from error
    hub.analytics.add_event(hub.simulation.time_s, f"Video loaded for benchmark: {name}", "video")
    return meta


@app.post("/api/video/start")
async def video_start(options: dict[str, Any] | None = None) -> dict[str, Any]:
    try: hub.video.start(hub.video_settings(options or {}))
    except ValueError as error: raise HTTPException(400, str(error)) from error
    return {"status": hub.video.status}


@app.post("/api/video/stop")
async def video_stop() -> dict[str, Any]:
    await asyncio.to_thread(hub.video.stop)
    return {"status": hub.video.status}


@app.delete("/api/video")
async def video_clear() -> dict[str, str]:
    await asyncio.to_thread(hub.video.clear)
    return {"status": "EMPTY"}


@app.get("/api/video/state")
async def video_state() -> dict[str, Any]:
    return hub.video.snapshot()


@app.post("/api/video/report")
async def video_report() -> dict[str, Any]:
    try: return await asyncio.to_thread(hub.video.export, hub.reports, hub.simulation.scenario)
    except ValueError as error: raise HTTPException(400, str(error)) from error


@app.get("/api/performance/{session_id}/json")
async def json_export(session_id: str) -> FileResponse:
    path = REPORTS / f"{safe_session(session_id)}.json"
    if not path.exists(): raise HTTPException(404, "JSON report was not found")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.get("/api/scenarios")
async def scenarios() -> list[dict[str, Any]]: return hub.scenarios.list()


@app.post("/api/scenarios/{name}")
async def save_scenario(name: str, config: dict[str, Any]) -> dict[str, Any]:
    try: return hub.scenarios.save(name, config)
    except ValueError as error: raise HTTPException(400, str(error)) from error


@app.get("/api/scenarios/{name}")
async def load_scenario(name: str) -> dict[str, Any]:
    try: return hub.scenarios.load(name)
    except ValueError as error: raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error: raise HTTPException(404, "Scenario was not found") from error


@app.post("/api/scenarios/{name}/apply")
async def apply_scenario(name: str) -> dict[str, Any]:
    try: document = hub.scenarios.load(name)
    except ValueError as error: raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error: raise HTTPException(404, "Scenario was not found") from error
    hub.simulation.load_scenario(document["name"], document.get("config", {}), "saved", description=f"Saved {document.get('saved_at', '')}")
    return {"loaded": document["name"]}


@app.delete("/api/scenarios/{name}")
async def delete_scenario(name: str) -> dict[str, str]:
    try: hub.scenarios.delete(name)
    except ValueError as error: raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error: raise HTTPException(404, "Scenario was not found") from error
    return {"deleted": name}


@app.get("/api/performance/{session_id}/csv")
async def csv_export(session_id: str) -> FileResponse:
    path = REPORTS / f"{safe_session(session_id)}.csv"
    if not path.exists(): raise HTTPException(404, "CSV report was not found")
    return FileResponse(path, media_type="text/csv", filename=path.name)


@app.get("/api/performance/{session_id}/pdf")
async def pdf_export(session_id: str) -> FileResponse:
    path = REPORTS / f"{safe_session(session_id)}.pdf"
    if not path.exists(): raise HTTPException(404, "PDF report was not found")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.websocket("/ws")
async def telemetry_socket(websocket: WebSocket) -> None:
    await hub.connections.connect(websocket)
    try:
        while True: hub.handle(ControlMessage.model_validate(await websocket.receive_json()))
    except WebSocketDisconnect: pass
    except Exception as error: await websocket.send_json({"type": "error", "message": str(error)})
    finally: hub.connections.disconnect(websocket)


@app.get("/{asset_path:path}")
async def ui(asset_path: str) -> FileResponse:
    candidate = (WEB_DIST / asset_path).resolve()
    if asset_path and candidate.is_file() and WEB_DIST.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(WEB_DIST / "index.html")
