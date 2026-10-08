"""Shared tracking core for BOTH the 3D simulation and the MP4 video benchmark.

Pipeline (identical for both input sources)::

    camera frame -> detect_candidates() -> TrackCore (gating + Kalman + lock
    state machine) -> RateLimitedPTZ (pan/tilt speed limits) -> PerformanceMeter

The simulation feeds rendered camera frames and has ground truth; the video
benchmark feeds uploaded MP4 frames and drives a virtual PTZ boresight inside
the video frame.  Nothing here knows which source it is serving.

Reuses the supplied ``vision.classical_detector`` ring-signature test and the
supplied world-coordinate ``BeaconKalmanTracker``.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from vision.classical_detector import _count_secondary_bumps
from vision.kalman_tracker import BeaconKalmanTracker

SIH_LIMITS = {
    "acquisition_time_s": 2.0,     # <= 2 s
    "tracking_error_px": 10.0,     # <= 10 px
    "target_loss_pct": 5.0,        # < 5 %
    "reacquisition_time_s": 1.0,   # <= 1 s
    "processing_fps": 20.0,        # >= 20 FPS
}


# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------
@dataclass
class Candidate:
    x: float
    y: float
    score: float          # 0..1
    kind: str             # "ring" (beacon optical signature) or "blob"
    area: float
    contrast: float


def _profile(gray: np.ndarray, cx: float, cy: float, max_r: int) -> np.ndarray:
    padded = cv2.copyMakeBorder(gray, max_r, max_r, max_r, max_r, cv2.BORDER_REPLICATE)
    polar = cv2.warpPolar(padded, (max_r, 180), (float(cx) + max_r, float(cy) + max_r), max_r, cv2.WARP_POLAR_LINEAR)
    return polar.mean(axis=0)


def preprocess(frame: np.ndarray) -> np.ndarray:
    """Noise mitigation used for every source.

    A 3x3 median is applied only when impulse (salt & pepper) noise is
    detected, because it also erodes the beacon's thin diffraction rings;
    a light Gaussian then suppresses Gaussian/shot noise.
    """
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    median = cv2.medianBlur(gray, 3)
    impulse = float(np.mean(cv2.absdiff(gray[::2, ::2], median[::2, ::2]) > 90))
    if impulse > 0.002:
        gray = median
    return cv2.GaussianBlur(gray, (5, 5), 0.9)


def detect_candidates(frame: np.ndarray, expected_size_px: float = 10.0, max_candidates: int = 8, min_contrast: float = 22.0) -> tuple[list[Candidate], np.ndarray]:
    """Find bright point-like beacon candidates, scored by the ring signature.

    Background is removed with a large box filter so a uniform fog/haze veil
    does not by itself trigger or hide detections; what remains decisive is
    the beacon's contrast against noise, which the disturbances reduce.
    """
    gray = preprocess(frame)
    bg = cv2.blur(gray, (41, 41))
    tophat = cv2.subtract(gray, bg)
    sample = tophat[::4, ::4].astype(np.float32)
    med = float(np.median(sample))
    mad = float(np.median(np.abs(sample - med))) * 1.4826 + 1e-3
    threshold = max(min_contrast, med + 7.0 * mad)
    _, mask = cv2.threshold(tophat, threshold, 255, cv2.THRESH_BINARY)
    # Opening removes thin structures (rain streaks, impulse noise) but keeps
    # the beacon core; 3x3 for normal targets, 2x2 for the smallest (5 px).
    k = 3 if expected_size_px >= 6 else 2
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return [], gray
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:max_candidates]
    core_r = max(1.5, expected_size_px / 2.0)
    max_r = int(max(18, min(60, core_r * 4.5)))
    out: list[Candidate] = []
    h, w = gray.shape
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        pad = 3
        x0, y0, x1, y1 = max(0, x - pad), max(0, y - pad), min(w, x + bw + pad), min(h, y + bh + pad)
        patch = tophat[y0:y1, x0:x1].astype(np.float32)
        weight = np.clip(patch - threshold * 0.5, 0, None)
        total = float(weight.sum())
        if total <= 0:
            continue
        ys, xs = np.mgrid[y0:y1, x0:x1]
        cx, cy = float((xs * weight).sum() / total), float((ys * weight).sum() / total)
        contrast = float(patch.max())
        profile = _profile(gray, cx, cy, max_r)
        bumps = _count_secondary_bumps(profile, min_prominence=6)
        ring_score = min(bumps / 2.0, 1.0)
        area = float(cv2.contourArea(contour)) + 1.0
        size_fit = math.exp(-abs(math.log(max(area, 1.0) / (math.pi * core_r * core_r))) / 1.5)
        snr = min(1.0, (contrast - threshold) / 60.0 + 0.3)
        if ring_score >= 0.5:
            out.append(Candidate(cx, cy, float(min(1.0, 0.55 + 0.3 * ring_score + 0.15 * snr)), "ring", area, contrast))
        else:
            out.append(Candidate(cx, cy, float(max(0.05, 0.45 * size_fit * snr)), "blob", area, contrast))
    # Non-maximum suppression: fragments of a beacon's own halo rings form
    # separate contours near it; keep only the strongest candidate there.
    out.sort(key=lambda c: (c.kind == "ring", c.area * c.score), reverse=True)
    kept: list[Candidate] = []
    for cand in out:
        if all(math.hypot(cand.x - k.x, cand.y - k.y) > 3.8 * core_r for k in kept if k.kind == "ring"):
            kept.append(cand)
    kept.sort(key=lambda c: c.score, reverse=True)
    return kept, gray


# ----------------------------------------------------------------------
# Track management (association + Kalman + lock state machine)
# ----------------------------------------------------------------------
class TrackCore:
    """Gated single-target tracker with an explicit lock state machine.

    States: SEARCHING -> ACQUIRING -> LOCKED <-> COASTING -> SEARCHING.
    ``locked`` is true in LOCKED and COASTING (Kalman bridges short misses).
    """

    def __init__(self, confirm_frames: int = 3, coast_frames: int = 12, gate: float = 28.0, accept: tuple[str, ...] = ("ring",)) -> None:
        self.confirm_frames, self.coast_frames, self.gate, self.accept = confirm_frames, coast_frames, gate, accept
        self.kalman = BeaconKalmanTracker(init_offset=(0, 0), max_coast_frames=None)
        self.reset()

    def reset(self) -> None:
        self.kalman.reset()
        self.state = "SEARCHING"
        self.hits = 0
        self.misses = 0
        self.estimate: tuple[float, float] | None = None
        self.innovation_rms = 0.0

    @property
    def locked(self) -> bool:
        return self.state in ("LOCKED", "COASTING")

    def velocity(self) -> tuple[float, float]:
        """Kalman velocity estimate in tracking-frame px per update."""
        if not self.kalman.initialized:
            return 0.0, 0.0
        s = self.kalman.kf.statePost
        return float(s[2, 0]), float(s[3, 0])

    def prediction(self) -> tuple[float, float] | None:
        if not self.kalman.initialized:
            return None
        s = self.kalman.kf.statePost
        return float(s[0, 0] + s[2, 0]), float(s[1, 0] + s[3, 0])

    def update(self, measurements: list[tuple[float, float, float, str]], hint: tuple[float, float] | None, acquire_gate: float) -> dict[str, Any]:
        """measurements: (x, y, score, kind) in the tracking frame (world px)."""
        usable = [m for m in measurements if m[3] in self.accept]
        pred = self.prediction()
        chosen = None
        if pred is not None:
            # Adaptive gate: grows with measured innovation (e.g. platform
            # jitter) and with consecutive misses.
            gate = max(self.gate, 3.5 * self.innovation_rms) * (1.0 + 0.35 * self.misses)
            near = [(math.hypot(m[0] - pred[0], m[1] - pred[1]), m) for m in usable]
            near = [item for item in near if item[0] <= gate]
            if near:
                chosen = min(near, key=lambda item: item[0] - 8.0 * item[1][2])[1]
        elif usable:
            if hint is not None:
                near = [(math.hypot(m[0] - hint[0], m[1] - hint[1]), m) for m in usable]
                near = [item for item in near if item[0] <= acquire_gate]
                chosen = min(near, key=lambda item: item[0])[1] if near else None
            else:
                chosen = usable[0]

        previous = self.state
        lost = False
        if chosen is not None:
            if pred is not None:
                innovation = math.hypot(chosen[0] - pred[0], chosen[1] - pred[1])
                self.innovation_rms = math.sqrt(0.9 * self.innovation_rms ** 2 + 0.1 * innovation ** 2)
            estimate, _ = self.kalman.update((chosen[0], chosen[1]))
            self.estimate = (float(estimate[0]), float(estimate[1])) if estimate else None
            self.misses = 0
            self.hits += 1
            if self.state in ("SEARCHING", "ACQUIRING"):
                self.state = "LOCKED" if self.hits >= self.confirm_frames else "ACQUIRING"
            else:
                self.state = "LOCKED"
        else:
            self.misses += 1
            if self.state in ("LOCKED", "COASTING") and self.misses <= self.coast_frames:
                estimate, _ = self.kalman.update(None)
                self.estimate = (float(estimate[0]), float(estimate[1])) if estimate else None
                self.state = "COASTING"
            else:
                lost = self.state in ("LOCKED", "COASTING")
                self.reset()
        return {
            "state": self.state, "previous": previous, "detected": chosen is not None, "locked": self.locked,
            "lost": lost, "measurement": (chosen[0], chosen[1]) if chosen else None, "estimate": self.estimate,
            "prediction": pred, "score": chosen[2] if chosen else 0.0, "kind": chosen[3] if chosen else None,
        }


# ----------------------------------------------------------------------
# PTZ with real pan/tilt speed limits
# ----------------------------------------------------------------------
class RateLimitedPTZ:
    """Virtual pan/tilt head. Positions are in tracking-frame pixels.

    ``deg_per_px`` converts pixels to angle, so the configured maximum pan and
    tilt speeds (deg/s) become hard per-update pixel limits.
    """

    def __init__(self, bounds: tuple[float, float, float, float], deg_per_px: tuple[float, float], max_pan_dps: float, max_tilt_dps: float, bandwidth_hz: float = 2.2) -> None:
        self.bounds = bounds                      # xmin, ymin, xmax, ymax for the aim point
        self.deg_per_px = deg_per_px
        self.max_pan_dps, self.max_tilt_dps = max_pan_dps, max_tilt_dps
        self.bandwidth_hz = bandwidth_hz
        self.x = (bounds[0] + bounds[2]) / 2
        self.y = (bounds[1] + bounds[3]) / 2
        self.rate_dps = (0.0, 0.0)
        self.saturated = False

    def set_position(self, x: float, y: float) -> None:
        self.x = min(max(x, self.bounds[0]), self.bounds[2])
        self.y = min(max(y, self.bounds[1]), self.bounds[3])

    def step(self, target: tuple[float, float] | None, dt: float, feedforward: tuple[float, float] = (0.0, 0.0)) -> None:
        """Move toward ``target``; ``feedforward`` is the target velocity (px/s).

        Proportional loop + velocity feed-forward (type-2 tracking) so a
        constant-velocity target is followed without steady-state lag. The
        commanded rate is then clamped to the configured pan/tilt limits.
        """
        if target is None or dt <= 0:
            self.rate_dps, self.saturated = (0.0, 0.0), False
            return
        k = 1.0 - math.exp(-2 * math.pi * self.bandwidth_hz * dt)   # first-order loop
        vx = feedforward[0] + (target[0] - self.x) * k / dt
        vy = feedforward[1] + (target[1] - self.y) * k / dt
        vmax_x = self.max_pan_dps / self.deg_per_px[0]
        vmax_y = self.max_tilt_dps / self.deg_per_px[1]
        cx, cy = max(-vmax_x, min(vmax_x, vx)), max(-vmax_y, min(vmax_y, vy))
        self.saturated = cx != vx or cy != vy
        self.set_position(self.x + cx * dt, self.y + cy * dt)
        self.rate_dps = (cx * self.deg_per_px[0], cy * self.deg_per_px[1])


# ----------------------------------------------------------------------
# Performance measurement against the SIH reference limits
# ----------------------------------------------------------------------
class PerformanceMeter:
    """Measures acquisition, re-acquisition, error, loss, retention and speed.

    Definitions (also written into every report):
      * acquisition time: start of an acquisition attempt (communication
        start or beacon switch) -> first frame that is locked AND centred
        (observable pointing error within the link tolerance, 10 px default);
      * re-acquisition time: lock lost -> lock regained, counted from the later
        of the loss moment and the moment the target became observable again
        (so time the beacon was deliberately hidden is not billed);
      * tracking error: pointing error in camera pixels on post-acquisition
        locked frames (simulation: vs ground truth; video: vs detected centroid);
      * target loss %: post-acquisition observable frames with no optical
        detection of the selected target;
      * lock retention %: post-acquisition observable frames with a held lock
        (detected, or Kalman coasting within the coast limit);
      * processing FPS: 1000 / mean per-frame processing time.
    """

    def __init__(self) -> None:
        self.reset(0.0)

    def reset(self, t: float) -> None:
        self.frames = 0
        self.proc_ms: list[float] = []
        self.dts: list[float] = []
        self.errors: list[float] = []
        self.acq_started = t
        self.acquisitions: list[float] = []
        self.current_acquired = False
        self.loss_at: float | None = None
        self.available_since: float | None = t
        self.reacquisitions: list[float] = []
        self.post_frames = 0
        self.post_detected = 0
        self.post_locked = 0
        self.hidden_time = 0.0
        self.first_t: float | None = None
        self.last_t = t
        self._was_locked = False
        self._was_available = True

    def begin_acquisition(self, t: float) -> None:
        """A new acquisition attempt (e.g. communication started / target switched)."""
        self.acq_started = t
        self.current_acquired = False
        self.loss_at = None
        self._was_locked = False

    def record(self, t: float, dt: float, processing_ms: float, locked: bool, detected: bool, error_px: float | None, available: bool = True, centred: bool | None = None) -> list[str]:
        """``centred``: lock held AND observable pointing error within tolerance.
        Acquisition completes on the first centred frame (defaults to ``locked``)."""
        events: list[str] = []
        self.frames += 1
        self.first_t = t if self.first_t is None else self.first_t
        self.last_t = t
        self.proc_ms.append(processing_ms)
        self.dts.append(dt)
        if available and not self._was_available:
            self.available_since = t
        if not available:
            self.hidden_time += dt
        self._was_available = available
        centred = locked if centred is None else (centred and locked)
        if centred and not self.current_acquired:
            self.current_acquired = True
            self.acquisitions.append(max(0.0, t - self.acq_started))
            events.append(f"Acquired lock in {t - self.acq_started:.2f}s")
        elif locked and not self._was_locked and self.loss_at is not None:
            start = max(self.loss_at, self.available_since or self.loss_at)
            self.reacquisitions.append(max(0.0, t - start))
            events.append(f"Re-acquired lock in {t - start:.2f}s")
            self.loss_at = None
        if not locked and self._was_locked:
            self.loss_at = t
            events.append("Lock lost")
        self._was_locked = locked
        if self.current_acquired and available:
            self.post_frames += 1
            self.post_detected += int(detected)
            self.post_locked += int(locked)
            if locked and error_px is not None and math.isfinite(error_px):
                self.errors.append(float(error_px))
        return events

    def summary(self) -> dict[str, Any]:
        duration = (self.last_t - self.first_t) if self.first_t is not None else 0.0
        mean_proc = statistics.fmean(self.proc_ms) if self.proc_ms else 0.0
        loop_fps = (len(self.dts) / sum(self.dts)) if self.dts and sum(self.dts) > 0 else 0.0
        e = self.errors
        s: dict[str, Any] = {
            "frames": self.frames,
            "duration_s": round(duration, 2),
            "loop_fps": round(loop_fps, 2),
            "processing_ms_mean": round(mean_proc, 3),
            "processing_ms_max": round(max(self.proc_ms), 3) if self.proc_ms else 0.0,
            "processing_fps": round(1000.0 / mean_proc, 1) if mean_proc > 0 else 0.0,
            "acquisition_time_s": round(self.acquisitions[0], 3) if self.acquisitions else None,
            "acquisition_times_s": [round(v, 3) for v in self.acquisitions[-10:]],
            "reacquisition_time_mean_s": round(statistics.fmean(self.reacquisitions), 3) if self.reacquisitions else None,
            "reacquisition_time_max_s": round(max(self.reacquisitions), 3) if self.reacquisitions else None,
            "reacquisition_count": len(self.reacquisitions),
            "error_mean_px": round(statistics.fmean(e), 3) if e else None,
            "error_max_px": round(max(e), 3) if e else None,
            "error_rmse_px": round(math.sqrt(statistics.fmean(v * v for v in e)), 3) if e else None,
            "target_loss_pct": round(100.0 * (1 - self.post_detected / self.post_frames), 2) if self.post_frames else None,
            "lock_retention_pct": round(100.0 * self.post_locked / self.post_frames, 2) if self.post_frames else None,
            "hidden_time_s": round(self.hidden_time, 2),
        }
        s["sih"] = sih_compliance(s)
        return s


def sih_compliance(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare measured values with SIH limits. ``None`` = not yet measured."""
    L = SIH_LIMITS

    def item(key: str, label: str, value: Any, limit: float, op: str, unit: str) -> dict[str, Any]:
        if value is None:
            ok = None
        else:
            ok = {"<=": value <= limit, "<": value < limit, ">=": value >= limit}[op]
        return {"key": key, "label": label, "value": value, "limit": limit, "op": op, "unit": unit, "pass": ok}

    return [
        item("acquisition", "Acquisition time", s.get("acquisition_time_s"), L["acquisition_time_s"], "<=", "s"),
        item("tracking_error", "Mean tracking error", s.get("error_mean_px"), L["tracking_error_px"], "<=", "px"),
        item("target_loss", "Target loss", s.get("target_loss_pct"), L["target_loss_pct"], "<", "%"),
        item("reacquisition", "Re-acquisition time (max)", s.get("reacquisition_time_max_s"), L["reacquisition_time_s"], "<=", "s"),
        item("processing", "Processing rate", s.get("processing_fps") or None, L["processing_fps"], ">=", "FPS"),
    ]
