"""Authoritative FSOC simulation for the web application.

One Python state drives every view.  Per update:

  trajectories -> line of sight (PTZ aim + platform motion + jitter)
  -> camera render (resolution / FOV / target size) -> atmosphere + noise
  -> shared tracking core (detect -> gate -> Kalman lock state machine)
  -> rate-limited PTZ -> ground-truth pointing error -> PerformanceMeter

Reused from the supplied project: ``sim.sky`` (background), ``sim.overlay``
(crosshair), ``vision.classical_detector`` ring-signature test and
``vision.kalman_tracker`` (both via ``vision.tracking_core``) and the optional
``vision.yolo_detector``.
"""

from __future__ import annotations

import base64
import copy
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from disturbance.sensor_model import DEFAULT_DISTURBANCES, DisturbanceModel, active_labels, sanitize
from sim.camera_renderer import WORLD_H, WORLD_W, in_frame, render_camera, world_to_camera
from sim.overlay import draw_crosshair
from sim.trajectories import PATTERNS, Trajectory
from vision.tracking_core import SIH_LIMITS, Candidate, RateLimitedPTZ, TrackCore, detect_candidates

from .analytics_service import AnalyticsService
from .presets import get_preset

FIELD_DEG = (12.0, 9.0)                       # field of regard covered by the 640x480 world
DEG_PER_WORLD_PX = (FIELD_DEG[0] / WORLD_W, FIELD_DEG[1] / WORLD_H)
MAX_BEACONS = 6
RESOLUTIONS = ([320, 240], [640, 480], [960, 720], [1280, 960])

# Default layout: each beacon owns its own path, centre and size (world px).
BEACON_DEFAULTS = [
    {"pattern": "circular", "speed": 1.0, "center_pct": [50, 50], "size": 100},
    {"pattern": "figure8", "speed": 1.0, "center_pct": [42, 34], "size": 110},
    {"pattern": "straight", "speed": 1.0, "center_pct": [50, 82], "size": 170},
    {"pattern": "random", "speed": 1.0, "center_pct": [74, 30], "size": 60},
    {"pattern": "spiral", "speed": 1.0, "center_pct": [24, 62], "size": 70},
    {"pattern": "sinusoidal", "speed": 1.0, "center_pct": [70, 66], "size": 80},
]

DEFAULT_CONFIG: dict[str, Any] = {
    "camera": {
        "resolution": [640, 480], "fov_h_deg": 4.0, "fov_v_deg": 3.0, "update_rate_hz": 30,
        "initial_position": {"mode": "center", "x_pct": 50.0, "y_pct": 50.0},
        "max_pan_dps": 8.0, "max_tilt_dps": 8.0,
    },
    "targets": {
        "count": 3, "size_px": 10, "decoy_count": 4,
        "beacons": [{"id": f"BEACON-{i + 1}", **copy.deepcopy(d)} for i, d in enumerate(BEACON_DEFAULTS)],
    },
    "tracking": {
        "detector": "classical", "acquisition": "cue", "cue_error_deg": 0.3,
        "confirm_frames": 3, "coast_frames": 12, "link_threshold_px": 10.0,
    },
    "comm": {"target_id": "BEACON-1", "mode": "auto"},
    "simulation": {"speed_multiplier": 1.0, "beacon_hidden": False},
    "environment": {"obstacle_enabled": False, "obstacle_center": [28.0, 6.0, 0.0], "obstacle_size": [8.0, 18.0, 16.0]},
    "disturbances": copy.deepcopy(DEFAULT_DISTURBANCES),
    "video": {"apply_disturbances": False},
}


def clamp(value: Any, low: float, high: float, default: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default if default is not None else low
    if not math.isfinite(number):
        return default if default is not None else low
    return max(low, min(high, number))


def deep_merge(destination: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge known keys only; lists of dicts (beacons) merge element-wise."""
    for key, value in source.items():
        if key not in destination:
            continue
        current = destination[key]
        if isinstance(current, dict) and isinstance(value, dict):
            deep_merge(current, value)
        elif isinstance(current, list) and current and isinstance(current[0], dict) and isinstance(value, list):
            for index, item in enumerate(value[: len(current)]):
                if isinstance(item, dict):
                    deep_merge(current[index], item)
        else:
            destination[key] = value


def _world3(point: tuple[float, float] | None) -> list[float] | None:
    """Plan-view mapping of the field of regard into the Three.js scene (unchanged scale)."""
    if point is None:
        return None
    return [round((float(point[0]) - WORLD_W / 2) * .26, 3), 5.0, round((WORLD_H / 2 - float(point[1])) * .22, 3)]


class SimulationService:
    """Runs the shared-core simulation and publishes the web state."""

    def __init__(self, analytics: AnalyticsService) -> None:
        self.analytics = analytics
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.optional_yolo: Any | None = None
        self.scenario: dict[str, Any] = {"id": "default", "name": "Default configuration", "source": "default", "loaded_at": None, "modified": False, "description": "Factory defaults"}
        self._sanitize()
        self.reset(new_session=False)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def _sanitize(self) -> None:
        c = self.config
        cam = c["camera"]
        res = cam.get("resolution", [640, 480])
        cam["resolution"] = list(res) if list(res) in RESOLUTIONS else [640, 480]
        cam["fov_h_deg"] = clamp(cam["fov_h_deg"], 1.0, 8.0, 4.0)
        cam["fov_v_deg"] = clamp(cam["fov_v_deg"], 0.75, 6.0, 3.0)
        cam["update_rate_hz"] = int(clamp(cam["update_rate_hz"], 20, 60, 30))
        cam["max_pan_dps"] = clamp(cam["max_pan_dps"], 1.0, 20.0, 8.0)
        cam["max_tilt_dps"] = clamp(cam["max_tilt_dps"], 1.0, 20.0, 8.0)
        ip = cam["initial_position"]
        ip["mode"] = ip.get("mode") if ip.get("mode") in ("center", "custom") else "center"
        ip["x_pct"], ip["y_pct"] = clamp(ip["x_pct"], 0, 100, 50), clamp(ip["y_pct"], 0, 100, 50)
        t = c["targets"]
        t["count"] = int(clamp(t["count"], 1, MAX_BEACONS, 3))
        t["size_px"] = clamp(t["size_px"], 5, 20, 10)
        t["decoy_count"] = int(clamp(t["decoy_count"], 0, 12, 4))
        for index, beacon in enumerate(t["beacons"]):
            beacon["id"] = f"BEACON-{index + 1}"
            beacon["pattern"] = beacon["pattern"] if beacon.get("pattern") in PATTERNS else BEACON_DEFAULTS[index]["pattern"]
            beacon["speed"] = clamp(beacon["speed"], 0.2, 3.0, 1.0)
            beacon["size"] = clamp(beacon["size"], 20, 200, BEACON_DEFAULTS[index]["size"])
            beacon["center_pct"] = [clamp(beacon["center_pct"][0], 5, 95, 50), clamp(beacon["center_pct"][1], 5, 95, 50)]
        tr = c["tracking"]
        tr["detector"] = tr["detector"] if tr.get("detector") in ("classical", "yolo") else "classical"
        tr["acquisition"] = tr["acquisition"] if tr.get("acquisition") in ("cue", "search") else "cue"
        tr["cue_error_deg"] = clamp(tr["cue_error_deg"], 0.0, 1.5, 0.3)
        tr["confirm_frames"] = int(clamp(tr["confirm_frames"], 1, 10, 3))
        tr["coast_frames"] = int(clamp(tr["coast_frames"], 0, 60, 12))
        tr["link_threshold_px"] = clamp(tr["link_threshold_px"], 2, 40, 10)
        comm = c["comm"]
        comm["mode"] = comm["mode"] if comm.get("mode") in ("auto", "manual") else "auto"
        valid = [b["id"] for b in t["beacons"][: t["count"]]]
        if comm.get("target_id") not in valid:
            comm["target_id"] = valid[0]
        c["simulation"]["speed_multiplier"] = clamp(c["simulation"]["speed_multiplier"], 0.25, 4.0, 1.0)
        c["disturbances"] = sanitize(c["disturbances"])
        c["video"]["apply_disturbances"] = bool(c["video"].get("apply_disturbances", False))

    @property
    def resolution(self) -> tuple[int, int]:
        return int(self.config["camera"]["resolution"][0]), int(self.config["camera"]["resolution"][1])

    @property
    def fov_world(self) -> tuple[float, float]:
        cam = self.config["camera"]
        return cam["fov_h_deg"] / DEG_PER_WORLD_PX[0], cam["fov_v_deg"] / DEG_PER_WORLD_PX[1]

    @property
    def scale(self) -> tuple[float, float]:
        (w, h), (fw, fh) = self.resolution, self.fov_world
        return w / fw, h / fh

    @property
    def selected_id(self) -> str:
        return self.config["comm"]["target_id"]

    def _build_world(self) -> None:
        t = self.config["targets"]
        self.trajectories: dict[str, Trajectory] = {}
        for index, beacon in enumerate(t["beacons"][: t["count"]]):
            self.trajectories[beacon["id"]] = Trajectory(
                pattern=beacon["pattern"], center=(beacon["center_pct"][0] * WORLD_W / 100, beacon["center_pct"][1] * WORLD_H / 100),
                size=beacon["size"], speed=beacon["speed"], phase=index * 1.3, seed=index + 7,
            )
        self.positions = {bid: traj.position() for bid, traj in self.trajectories.items()}
        self._build_decoys()

    def _build_decoys(self) -> None:
        rng = random.Random(42)
        self.decoys = [{
            "x": rng.uniform(30, WORLD_W - 30), "y": rng.uniform(30, WORLD_H - 30),
            "vx": rng.uniform(-15, 15), "vy": rng.uniform(-15, 15),
            "brightness": rng.randint(120, 255), "radius_px": rng.choice([2, 3, 4, 5, 7]),
        } for _ in range(int(self.config["targets"]["decoy_count"]))]

    def _build_pipeline(self) -> None:
        cam, tr = self.config["camera"], self.config["tracking"]
        fw, fh = self.fov_world
        bounds = (fw / 2, fh / 2, WORLD_W - fw / 2, WORLD_H - fh / 2)
        old = (self.ptz.x, self.ptz.y) if getattr(self, "ptz", None) else None
        self.ptz = RateLimitedPTZ(bounds, DEG_PER_WORLD_PX, cam["max_pan_dps"], cam["max_tilt_dps"])
        if old:
            self.ptz.set_position(*old)
        # Gate is expressed in world px; ~12 camera px at the default scale.
        self.track = TrackCore(confirm_frames=tr["confirm_frames"], coast_frames=tr["coast_frames"], gate=max(4.0, 12.0 / self.scale[0]) + 4.0, accept=("ring",))
        self.disturbance = getattr(self, "disturbance", None) or DisturbanceModel()
        self.disturbance.configure(self.config["disturbances"])

    def _initial_aim(self) -> tuple[float, float]:
        ip = self.config["camera"]["initial_position"]
        if ip["mode"] == "custom":
            return ip["x_pct"] * WORLD_W / 100, ip["y_pct"] * WORLD_H / 100
        return WORLD_W / 2, WORLD_H / 2

    def reset(self, new_session: bool = True) -> None:
        self.running = False
        self.time_s = 0.0
        self.frame = 0
        self.comm_active = False
        self.comm_state = "IDLE"
        self.aligned_since: float | None = None
        self.hidden_id: str | None = None
        self.config["simulation"]["beacon_hidden"] = False
        self.cue_bias = (0.0, 0.0)
        self.cue_refresh = 0.0
        self.cue_mismatch = 0
        self.search_origin: tuple[float, float] | None = None
        self.search_t = 0.0
        self.ptz = None
        self._build_world()
        self._build_pipeline()
        self.ptz.set_position(*self._initial_aim())
        self.los = (self.ptz.x, self.ptz.y)
        self.last = {"state": "SEARCHING", "detected": False, "locked": False, "measurement": None, "estimate": None, "prediction": None, "score": 0.0}
        self.last_candidates: list[Candidate] = []
        self.last_error: float | None = None
        self.last_truth_cam: tuple[float, float] | None = None
        self.los_offset_px = (0.0, 0.0)
        self.trails: dict[str, list[list[float]]] = {}
        self.camera_trajectory: list[list[float]] = []
        self.yolo_failed = False
        self.last_image = self._encode(self._render()[0], None)
        if new_session:
            self.analytics.start(self.config)

    def restore_defaults(self) -> None:
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self._sanitize()
        self.optional_yolo = None
        self.scenario = {"id": "default", "name": "Default configuration", "source": "default", "loaded_at": datetime.now(timezone.utc).isoformat(), "modified": False, "description": "Factory defaults"}
        self.reset(new_session=True)
        self.analytics.add_event(0.0, "Defaults restored", "scenario")

    def configure(self, patch: dict[str, Any], *, mark_modified: bool = True) -> None:
        before = copy.deepcopy(self.config)
        deep_merge(self.config, patch)
        self._sanitize()
        c = self.config
        # World changes: rebuild paths only for what changed, keep time continuity.
        if c["targets"] != before["targets"]:
            self._build_world()
            for traj in self.trajectories.values():
                traj.t = self.time_s
            self.analytics.add_event(self.time_s, f"Targets updated: {c['targets']['count']} beacon(s), {c['targets']['size_px']:.0f}px, {c['targets']['decoy_count']} decoy(s)", "scenario")
        if c["camera"] != before["camera"] or c["tracking"] != before["tracking"]:
            self._build_pipeline()
            if not self.running and c["camera"]["initial_position"] != before["camera"]["initial_position"]:
                self.ptz.set_position(*self._initial_aim())
            self.analytics.add_event(self.time_s, f"Camera {c['camera']['resolution'][0]}x{c['camera']['resolution'][1]}, FOV {c['camera']['fov_h_deg']:.1f}°x{c['camera']['fov_v_deg']:.1f}°, {c['camera']['update_rate_hz']} Hz, pan/tilt ≤{c['camera']['max_pan_dps']:.0f}/{c['camera']['max_tilt_dps']:.0f}°/s", "scenario")
        if c["disturbances"] != before["disturbances"]:
            self.disturbance.configure(c["disturbances"])
            labels = active_labels(c["disturbances"])
            self.analytics.add_event(self.time_s, "Disturbances: " + (", ".join(labels) if labels else "none"), "scenario")
        if c["comm"]["target_id"] != before["comm"]["target_id"]:
            self._switch_target(before["comm"]["target_id"])
        if c["tracking"]["detector"] == "yolo" and self.optional_yolo is None:
            self._load_yolo()
        if mark_modified and self.scenario.get("source") != "default" and c != before:
            self.scenario["modified"] = True

    def _load_yolo(self) -> None:
        try:
            from vision.yolo_detector import YoloBeaconDetector
            self.optional_yolo = YoloBeaconDetector(str(Path(__file__).resolve().parents[1] / "beacon_yolo.pt"))
            self.analytics.add_event(self.time_s, "YOLO detector loaded", "tracking")
        except Exception as error:  # ultralytics/torch not installed
            self.config["tracking"]["detector"] = "classical"
            self.analytics.add_event(self.time_s, f"YOLO unavailable ({error.__class__.__name__}); using classical ring-signature detector", "system")

    def load_scenario(self, name: str, config: dict[str, Any], source: str, scenario_id: str | None = None, description: str = "") -> None:
        """Apply a scenario from defaults so no stale value survives, then reset the run."""
        was_running = self.running
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        deep_merge(self.config, config)
        self._sanitize()
        self.optional_yolo = None
        self.reset(new_session=True)
        if self.config["tracking"]["detector"] == "yolo":
            self._load_yolo()
        self.scenario = {"id": scenario_id or name, "name": name, "source": source, "loaded_at": datetime.now(timezone.utc).isoformat(), "modified": False, "description": description}
        self.analytics.add_event(0.0, f"Scenario loaded: {name}", "scenario")
        labels = active_labels(self.config["disturbances"])
        self.analytics.add_event(0.0, "Applied disturbances: " + (", ".join(labels) if labels else "none"), "scenario")
        if was_running:
            self.start()

    def load_preset(self, preset_id: str) -> bool:
        preset = get_preset(preset_id)
        if preset is None:
            return False
        self.load_scenario(preset["name"], preset["config"], "preset", preset["id"], preset["description"])
        return True

    # ------------------------------------------------------------------
    # Mission / communication commands
    # ------------------------------------------------------------------
    def start(self) -> None:
        self.running = True
        if self.config["comm"]["mode"] == "auto" and not self.comm_active:
            self.start_comm()

    def start_comm(self, target_id: str | None = None) -> None:
        if target_id and target_id in self.trajectories and target_id != self.selected_id:
            previous = self.selected_id
            self.config["comm"]["target_id"] = target_id
            self._switch_target(previous)
        self.comm_active = True
        self.comm_state = "ACQUIRING"
        self.aligned_since = None
        self.track.reset()
        self.analytics.begin_acquisition(self.time_s)
        self.analytics.add_event(self.time_s, f"Communication started with {self.selected_id}", "link")

    def stop_comm(self) -> None:
        if self.comm_active:
            self.analytics.add_event(self.time_s, f"Communication with {self.selected_id} stopped", "link")
        self.comm_active = False
        self.comm_state = "IDLE"
        self.aligned_since = None

    def _switch_target(self, previous: str) -> None:
        if self.hidden_id and self.hidden_id != self.selected_id:
            self.hidden_id = None
            self.config["simulation"]["beacon_hidden"] = False
        self.track.reset()
        self.aligned_since = None
        self.search_origin = None
        if self.comm_active:
            self.comm_state = "ACQUIRING"
            self.analytics.begin_acquisition(self.time_s)
        self.analytics.add_event(self.time_s, f"PTZ target switched {previous} → {self.selected_id}", "tracking")

    def set_target(self, target_id: str) -> None:
        if target_id not in self.trajectories:
            self.analytics.add_event(self.time_s, f"{target_id} is not an active beacon", "tracking")
            return
        if target_id != self.selected_id:
            previous = self.selected_id
            self.config["comm"]["target_id"] = target_id
            self._switch_target(previous)

    def set_lock_mode(self, mode: str) -> None:
        if mode in ("auto", "manual"):
            self.config["comm"]["mode"] = mode
            self.analytics.add_event(self.time_s, f"Communication start mode: {mode}", "tracking")

    def toggle_beacon(self) -> None:
        if self.hidden_id is None:
            self.hidden_id = self.selected_id
            self.config["simulation"]["beacon_hidden"] = True
            self.analytics.add_event(self.time_s, f"{self.hidden_id} optical signal hidden (keeps moving on its path)", "tracking")
        else:
            self.analytics.add_event(self.time_s, f"{self.hidden_id} optical signal restored", "tracking")
            self.hidden_id = None
            self.config["simulation"]["beacon_hidden"] = False

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------
    def _segment_hits_obstacle(self, end: list[float]) -> bool:
        env = self.config["environment"]
        if not env["obstacle_enabled"]:
            return False
        center, size = env["obstacle_center"], env["obstacle_size"]
        for fraction in np.linspace(.05, 1.0, 40):
            point = [end[0] * fraction, 5.0, end[2] * fraction]
            if all(abs(point[i] - center[i]) <= size[i] / 2 for i in range(3)):
                return True
        return False

    def _render(self) -> tuple[np.ndarray, dict[str, bool]]:
        visible: dict[str, bool] = {}
        beacons = []
        for bid, pos in self.positions.items():
            blocked = self._segment_hits_obstacle(_world3(pos))
            shown = bid != self.hidden_id and not blocked
            visible[bid] = shown
            if shown:
                gain, wander, blur = self.disturbance.beacon_signal()
                beacons.append({"x": pos[0], "y": pos[1], "gain": gain, "wander": wander, "blur": blur})
        image = render_camera(self.los, self.fov_world, self.resolution, beacons, self.decoys, self.config["targets"]["size_px"])
        return self.disturbance.apply_frame(image), visible

    def _encode(self, image: np.ndarray, overlay: dict[str, Any] | None) -> str:
        view = image.copy()
        w, h = self.resolution
        k = w / 640
        if overlay:
            for cand in overlay.get("candidates", []):
                s = int(10 * k)
                cv2.rectangle(view, (int(cand.x) - s, int(cand.y) - s), (int(cand.x) + s, int(cand.y) + s), (140, 140, 140), 1)
            if overlay.get("measurement") is not None:
                mx, my = map(int, overlay["measurement"])
                s = int(self.config["targets"]["size_px"] * 1.4 + 6)
                cv2.rectangle(view, (mx - s, my - s), (mx + s, my + s), (80, 255, 80), 2)
            if overlay.get("estimate") is not None:
                ex, ey = map(int, overlay["estimate"])
                color = (255, 190, 90) if overlay.get("coasting") else (255, 255, 0)
                cv2.circle(view, (ex, ey), int(16 * k), color, 2, cv2.LINE_AA)
                cv2.line(view, (w // 2, h // 2), (ex, ey), color, 1, cv2.LINE_AA)
        draw_crosshair(view, (w // 2, h // 2), size=int(18 * k), gap=int(6 * k), color=(0, 255, 255), thickness=1)
        if max(w, h) > 800:
            view = cv2.resize(view, (w * 640 // max(w, 1), h * 640 // max(w, 1)), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 82])
        return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii") if ok else ""

    def _cue(self, dt: float) -> tuple[float, float] | None:
        """External coarse position cue (e.g. GPS/telemetry exchange) with bias error."""
        target = self.positions.get(self.selected_id)
        if target is None:
            return None
        self.cue_refresh -= dt
        if self.cue_refresh <= 0:
            sigma = self.config["tracking"]["cue_error_deg"] / DEG_PER_WORLD_PX[0]
            self.cue_bias = (random.gauss(0, sigma), random.gauss(0, sigma))
            self.cue_refresh = 1.0
        return target[0] + self.cue_bias[0], target[1] + self.cue_bias[1]

    def _search_point(self, dt: float) -> tuple[float, float]:
        """Archimedean spiral raster around where the target was last seen."""
        if self.search_origin is None:
            self.search_origin = self.last["estimate"] or (self.ptz.x, self.ptz.y)
            self.search_t = 0.0
        self.search_t += dt
        fw = self.fov_world[0]
        theta = 2.2 * self.search_t
        r = (0.55 * fw) * theta / (2 * math.pi)
        if r > max(WORLD_W, WORLD_H):
            self.search_t = 0.0
        return self.search_origin[0] + r * math.cos(theta), self.search_origin[1] + r * math.sin(theta)

    def step(self, dt: float) -> dict[str, Any]:
        started = time.perf_counter()
        dt = clamp(dt, .005, .1)
        if not self.running:
            return self._payload(dt, 0.0)
        sdt = dt * float(self.config["simulation"]["speed_multiplier"])
        self.time_s += sdt
        self.frame += 1

        # 1. Target and clutter motion
        for bid, traj in self.trajectories.items():
            self.positions[bid] = traj.step(sdt)
        for d in self.decoys:
            d["x"] += d["vx"] * sdt
            d["y"] += d["vy"] * sdt
            if not 15 < d["x"] < WORLD_W - 15:
                d["vx"] *= -1
            if not 15 < d["y"] < WORLD_H - 15:
                d["vy"] *= -1

        # 2. Actual line of sight = commanded aim + platform motion + jitter
        sx, sy = self.scale
        px, py = self.disturbance.platform_offset(self.time_s, sdt)
        jx, jy = self.disturbance.jitter_offset()
        self.los_offset_px = (px + jx, py + jy)
        self.los = (self.ptz.x + self.los_offset_px[0] / sx, self.ptz.y + self.los_offset_px[1] / sy)

        # 3. Camera frame (resolution, FOV, target size, atmosphere, noise)
        image, visible = self._render()
        w, h = self.resolution

        # 4. Detection (optional YOLO, otherwise ring-signature classical)
        candidates: list[Candidate] = []
        if self.config["tracking"]["detector"] == "yolo" and self.optional_yolo is not None:
            try:
                point, conf = self.optional_yolo.detect(image)
                if point is not None:
                    candidates = [Candidate(float(point[0]), float(point[1]), float(conf), "ring", 0.0, 0.0)]
            except Exception:
                candidates = []
        else:
            candidates, _ = detect_candidates(image, self.config["targets"]["size_px"])
        self.last_candidates = candidates
        # The controller only knows its commanded aim (no IMU), so platform
        # motion and jitter appear as target motion - exactly as on hardware.
        measurements = [(self.ptz.x + (c.x - w / 2) / sx, self.ptz.y + (c.y - h / 2) / sy, c.score, c.kind) for c in candidates]

        # 5. Association + lock state machine
        cue = self._cue(sdt) if self.config["tracking"]["acquisition"] == "cue" else None
        hint = cue if cue is not None else (self.ptz.x, self.ptz.y)
        cue_px = self.config["tracking"]["cue_error_deg"] / DEG_PER_WORLD_PX[0]
        acquire_gate = 2.5 * cue_px + 15 if cue is not None else max(self.fov_world) / 2
        result = self.track.update(measurements, hint, acquire_gate)
        # Identity check against the coarse cue (observable data only): a lock
        # that stays far from where the selected terminal reports itself is a
        # different object (another beacon / decoy) and is rejected.
        if cue is not None and result["locked"] and result["estimate"] is not None:
            if math.hypot(result["estimate"][0] - cue[0], result["estimate"][1] - cue[1]) > 3 * cue_px + 25:
                self.cue_mismatch += 1
                if self.cue_mismatch > 10:
                    self.track.reset()
                    self.cue_mismatch = 0
                    self.analytics.add_event(self.time_s, f"Rejected lock on non-selected object (inconsistent with {self.selected_id} position cue)", "tracking")
                    result = {**result, "state": "SEARCHING", "locked": False, "detected": False, "estimate": None, "measurement": None, "lost": True}
            else:
                self.cue_mismatch = 0
        if result["detected"] or result["locked"]:
            self.search_origin = None

        # 6. PTZ command (rate limited)
        if self.comm_active:
            feedforward = (0.0, 0.0)
            if result["locked"] and result["estimate"] is not None:
                aim = result["estimate"]
                vx, vy = self.track.velocity()           # px per update
                feedforward = (vx / sdt, vy / sdt)
            elif result["measurement"] is not None:
                aim = result["measurement"]
            elif cue is not None:
                aim = cue
            else:
                aim = self._search_point(sdt)
            self.ptz.step(aim, sdt, feedforward)
        else:
            self.ptz.step(None, sdt)

        # 7. Ground truth for the selected beacon in this frame
        truth_world = self.positions.get(self.selected_id)
        truth_cam = world_to_camera(truth_world, self.los, self.fov_world, self.resolution) if truth_world else None
        observable = bool(truth_world is not None and visible.get(self.selected_id, False))
        fov_ok = bool(truth_cam is not None and in_frame(truth_cam, self.resolution))
        error = math.hypot(truth_cam[0] - w / 2, truth_cam[1] - h / 2) if truth_cam else float("nan")
        self.last_truth_cam = truth_cam
        self.last_error = error
        wrong_target = bool(result["locked"] and result["estimate"] and truth_world and math.hypot(result["estimate"][0] - truth_world[0], result["estimate"][1] - truth_world[1]) > 40)

        # 8. Communication state (uses only observable quantities, never truth)
        los_clear = not self._segment_hits_obstacle(_world3(truth_world)) if truth_world else True
        measured_error = None
        if result["measurement"] is not None:
            mx = (result["measurement"][0] - self.ptz.x) * sx
            my = (result["measurement"][1] - self.ptz.y) * sy
            measured_error = math.hypot(mx, my)
        link_ok = bool(self.comm_active and result["locked"] and result["detected"] and los_clear and measured_error is not None and measured_error <= self.config["tracking"]["link_threshold_px"])
        if link_ok:
            self.aligned_since = self.aligned_since if self.aligned_since is not None else self.time_s
        elif not (self.comm_active and result["state"] == "COASTING" and self.comm_state == "CONNECTED"):
            self.aligned_since = None
        previous_comm = self.comm_state
        if not self.comm_active:
            self.comm_state = "IDLE"
        elif self.aligned_since is not None and self.time_s - self.aligned_since >= 0.3:
            self.comm_state = "CONNECTED"
        elif result["locked"]:
            self.comm_state = "TRACKING"
        else:
            self.comm_state = "ACQUIRING"
        if previous_comm != self.comm_state and self.comm_active:
            self.analytics.add_event(self.time_s, f"{self.selected_id}: communication {self.comm_state}", "link")
        self.last = result

        # 9. Overlay + record
        to_cam = lambda p: ((p[0] - self.ptz.x) * sx + w / 2, (p[1] - self.ptz.y) * sy + h / 2) if p else None
        self.last_image = self._encode(image, {
            "candidates": candidates, "measurement": to_cam(result["measurement"]),
            "estimate": to_cam(result["estimate"]), "coasting": result["state"] == "COASTING",
        })
        processing_ms = (time.perf_counter() - started) * 1000
        if self.comm_active:
            self._record(dt, processing_ms, result, error, fov_ok, los_clear, observable, wrong_target, measured_error)
        return self._payload(dt, processing_ms)

    def _angles(self, point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] - WORLD_W / 2) * DEG_PER_WORLD_PX[0], (WORLD_H / 2 - point[1]) * DEG_PER_WORLD_PX[1]

    def _record(self, dt: float, processing_ms: float, result: dict[str, Any], error: float, fov_ok: bool, los_clear: bool, observable: bool, wrong_target: bool, measured_error: float | None) -> None:
        truth = self.positions.get(self.selected_id) or (0.0, 0.0)
        taz, tel = self._angles(truth)
        pan, tilt = self._angles(self.los)
        d = self.config["disturbances"]
        dist_level = sum(d["noise"].values()) + d["jitter"]["amplitude_px"] / 20 + (d["platform"]["amplitude_px"] / 20 if d["platform"]["pattern"] != "none" else 0) + (d["atmosphere"]["intensity"] if d["atmosphere"]["condition"] != "clear" else 0) + d["turbulence"]["strength"]
        row = {
            "timestamp": round(self.time_s, 4), "dt": dt, "target_id": self.selected_id, "track_state": result["state"],
            "detected": bool(result["detected"]), "locked": bool(result["locked"] and not wrong_target), "wrong_target": wrong_target,
            "fov_ok": fov_ok, "los_clear": los_clear, "fsoc_link": self.comm_state == "CONNECTED", "comm_state": self.comm_state,
            "target_observable": observable, "error_valid": math.isfinite(error),
            "centred": bool(measured_error is not None and measured_error <= self.config["tracking"]["link_threshold_px"]),
            "measured_error": round(measured_error, 3) if measured_error is not None else None,
            "target_azimuth": round(taz, 4), "target_elevation": round(tel, 4), "camera_pan": round(pan, 4), "camera_tilt": round(tilt, 4),
            "pan_error": round(taz - pan, 4), "tilt_error": round(tel - tilt, 4),
            "angular_error": round(math.hypot(taz - pan, tel - tilt), 4),
            "pixel_error": round(error, 3) if math.isfinite(error) else None,
            "pan_rate": round(self.ptz.rate_dps[0], 3), "tilt_rate": round(-self.ptz.rate_dps[1], 3),
            "confidence": round(float(result["score"]), 3), "fps": round(1 / dt, 2) if dt else 0.0,
            "processing_time_ms": round(processing_ms, 3), "disturbance_level": round(dist_level, 3),
            "beacon_hidden": self.hidden_id == self.selected_id, "los_offset_x": round(self.los_offset_px[0], 2), "los_offset_y": round(self.los_offset_px[1], 2),
        }
        self.analytics.record(row)

    # ------------------------------------------------------------------
    # Web payload
    # ------------------------------------------------------------------
    def _payload(self, dt: float, processing_ms: float) -> dict[str, Any]:
        w, h = self.resolution
        sx, sy = self.scale
        fw, fh = self.fov_world
        result = self.last
        targets = []
        for index, (bid, pos) in enumerate(self.positions.items()):
            p3 = _world3(pos)
            trail = self.trails.setdefault(bid, [])
            if self.running:
                trail.append(p3)
                self.trails[bid] = trail[-160:]
            selected = bid == self.selected_id
            targets.append({
                "id": bid, "label": f"Beacon {index + 1}", "role": "beacon", "selected": selected,
                "pattern": self.trajectories[bid].pattern, "position": p3,
                "optical_visible": bid != self.hidden_id and not self._segment_hits_obstacle(p3),
                "hidden": bid == self.hidden_id, "angles": list(map(lambda v: round(v, 3), self._angles(pos))),
                "confidence": float(result["score"]) if selected else 0.0,
            })
        for index, d in enumerate(self.decoys, 1):
            targets.append({"id": f"DECOY-{index:02d}", "label": f"Decoy {index}", "role": "decoy", "selected": False, "pattern": "drift", "position": _world3((d["x"], d["y"])), "optical_visible": True, "hidden": False, "angles": list(self._angles((d["x"], d["y"]))), "confidence": 0.0})
        aim3 = _world3((self.ptz.x, self.ptz.y))
        footprint = [_world3((self.los[0] + ox * fw / 2, self.los[1] + oy * fh / 2)) for ox, oy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        if self.running:
            self.camera_trajectory.append(aim3)
            self.camera_trajectory = self.camera_trajectory[-160:]
        pan, tilt = self._angles(self.los)
        cmd_pan, cmd_tilt = self._angles((self.ptz.x, self.ptz.y))
        to_cam = lambda p: [round((p[0] - self.ptz.x) * sx + w / 2, 2), round((p[1] - self.ptz.y) * sy + h / 2, 2)] if p else None
        truth = self.positions.get(self.selected_id)
        selected_index = list(self.positions).index(self.selected_id) + 1 if self.selected_id in self.positions else 1
        summary = self.analytics.summary()
        error = self.last_error if self.last_error is not None and math.isfinite(self.last_error) else None
        return {
            "type": "telemetry", "timestamp": round(self.time_s, 3), "running": self.running,
            "comm": {
                "active": self.comm_active, "state": self.comm_state, "target_id": self.selected_id,
                "target_label": f"Beacon {selected_index}", "mode": self.config["comm"]["mode"],
                "path": self.trajectories[self.selected_id].pattern if self.selected_id in self.trajectories else None,
                "link_threshold_px": self.config["tracking"]["link_threshold_px"],
            },
            "active_target_id": self.selected_id, "primary_target_id": self.selected_id, "lock_mode": self.config["comm"]["mode"],
            "uav1": {"id": "FSOC-TERMINAL", "position": [0.0, 5.0, 0.0]},
            "targets": targets,
            "camera": {
                "pan": round(pan, 4), "tilt": round(tilt, 4), "commanded_pan": round(cmd_pan, 4), "commanded_tilt": round(cmd_tilt, 4),
                "pan_rate": round(self.ptz.rate_dps[0], 3), "tilt_rate": round(-self.ptz.rate_dps[1], 3), "rate_limited": self.ptz.saturated,
                "fov_horizontal": self.config["camera"]["fov_h_deg"], "fov_vertical": self.config["camera"]["fov_v_deg"],
                "resolution": [w, h], "update_rate_hz": self.config["camera"]["update_rate_hz"],
                "max_pan_dps": self.config["camera"]["max_pan_dps"], "max_tilt_dps": self.config["camera"]["max_tilt_dps"],
                "aim_world": aim3, "footprint": footprint, "los_offset_px": [round(v, 2) for v in self.los_offset_px],
            },
            "field": {"width_deg": FIELD_DEG[0], "height_deg": FIELD_DEG[1]},
            "target_angles": {"azimuth": round(self._angles(truth)[0], 4) if truth else 0.0, "elevation": round(self._angles(truth)[1], 4) if truth else 0.0, "distance": 0.0},
            "tracking": {
                "state": result["state"], "pixel_error": error, "detected": bool(result["detected"]), "locked": bool(result["locked"]),
                "confidence": float(result["score"]), "detector": "YOLO" if self.config["tracking"]["detector"] == "yolo" and self.optional_yolo else "CLASSICAL (ring signature)",
                "measured": to_cam(result["measurement"]), "predicted": to_cam(result["estimate"]), "truth": [round(v, 2) for v in self.last_truth_cam] if self.last_truth_cam else None,
                "predicted_world": _world3(result["estimate"]) if result["state"] == "COASTING" else None,
                "prediction_active": result["state"] == "COASTING", "candidates": len(self.last_candidates),
                "angular_error": round(error * DEG_PER_WORLD_PX[0] / sx, 4) if error is not None else 0.0,
            },
            "system": {
                "fps": round(1 / dt, 2) if dt else 0.0, "processing_time_ms": round(processing_ms, 2),
                "fov_ok": bool(self.last_truth_cam and in_frame(self.last_truth_cam, self.resolution)),
                "los_clear": not self._segment_hits_obstacle(_world3(truth)) if truth else True,
                "fsoc_link": self.comm_state == "CONNECTED",
                "link_reason": self._link_reason(),
            },
            "disturbances": self.config["disturbances"], "disturbance_labels": active_labels(self.config["disturbances"]),
            "environment": self.config["environment"],
            "simulation": {"speed_multiplier": self.config["simulation"]["speed_multiplier"], "beacon_hidden": self.hidden_id is not None, "hidden_id": self.hidden_id, "beacon_count": len(self.trajectories), "decoy_count": len(self.decoys)},
            "scenario": {**self.scenario, "status": ("ACTIVE" if self.running else "LOADED"), "applies_to": {"simulation": True, "video": bool(self.config["video"]["apply_disturbances"])}},
            "camera_view": {"width": w, "height": h, "image": self.last_image},
            "trajectories": self.trails, "camera_trajectory": self.camera_trajectory,
            "history": self.analytics.history, "events": self.analytics.events[-60:],
            "performance": summary, "sih_limits": SIH_LIMITS, "config": self.config,
        }

    def _link_reason(self) -> str:
        if not self.comm_active:
            return "Communication idle — select a beacon and press Connect"
        if self.hidden_id == self.selected_id:
            return f"{self.selected_id} optical signal hidden — Kalman coasting, then search/cue re-acquisition"
        return {
            "ACQUIRING": f"Acquiring {self.selected_id} ({'coarse position cue' if self.config['tracking']['acquisition'] == 'cue' else 'spiral search'})",
            "TRACKING": f"Tracking {self.selected_id}; waiting for pointing error ≤ {self.config['tracking']['link_threshold_px']:.0f}px",
            "CONNECTED": f"FSOC link connected to {self.selected_id}",
        }.get(self.comm_state, self.comm_state)
