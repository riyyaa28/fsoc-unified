"""Per-run performance metrics for the coarse-alignment tracking loop.

The dashboard calls ``begin_frame()`` at the top of every simulation tick
and ``end_frame(...)`` at the bottom. ``summary()`` then turns the recorded
frames into the figures the problem statement asks the performance report
to contain (simulation duration, FPS, acquisition / re-acquisition time,
average / RMS / maximum tracking error, lock retention, processing time).

Timing uses the real clock (``time.perf_counter``), counting only time
while the simulation is running, because the scene moves in real time
and the loop does not always hit its nominal 30 Hz.

Tracking error is measured against the simulator's ground truth: the
distance between the true beacon centroid and the camera boresight
(image centre) in the frame the detector saw. This is independent of the
tracker's own estimate, so a lock on a decoy shows up as a large error.
"""

import math
import time
from collections import Counter
from datetime import datetime

import numpy as np

# Performance specifications from the problem statement.
SPEC_ACQUISITION_S = 2.0      # Acquisition time <= 2 s
SPEC_TRACKING_ERROR_PX = 10.0  # Tracking error <= 10 px
SPEC_TARGET_LOSS_PCT = 5.0    # Target loss < 5 %
SPEC_REACQUISITION_S = 1.0    # Re-acquisition time <= 1 s
SPEC_MIN_FPS = 20.0           # Processing speed >= 20 FPS

STATE_LOCKED = "LOCKED"
STATE_PREDICTING = "PREDICTING"  # beacon deliberately hidden, Kalman coasting
STATE_SEEKING = "SEEKING"        # no track: target lost / not yet acquired


class RunMetrics:
    def __init__(self):
        self.started_at = datetime.now()
        self.frames = []
        self.active_time = 0.0
        self.current_fps = 0.0
        self._last_tick = None
        self._frame_start = None
        self._frame_dt = None

    def __len__(self):
        return len(self.frames)

    def pause(self):
        # The next tick after a pause must not count the paused gap.
        self._last_tick = None

    def begin_frame(self):
        now = time.perf_counter()
        dt = None
        if self._last_tick is not None:
            dt = now - self._last_tick
            self.active_time += dt
            if dt > 0:
                inst_fps = 1.0 / dt
                self.current_fps = (
                    inst_fps if self.current_fps == 0.0
                    else 0.1 * inst_fps + 0.9 * self.current_fps
                )
        self._last_tick = now
        self._frame_start = now
        self._frame_dt = dt
        return dt

    def end_frame(
        self,
        state,
        source,
        tracking_error_px,
        centroid_error_px,
        confidence,
        target_in_fov,
        beacon_hidden,
        pattern,
        disturbances,
    ):
        processing_ms = (time.perf_counter() - self._frame_start) * 1000.0
        self.frames.append(
            {
                "frame": len(self.frames),
                "t_s": self.active_time,
                "frame_interval_ms": (
                    None if self._frame_dt is None else self._frame_dt * 1000.0
                ),
                "processing_ms": processing_ms,
                "state": state,
                "source": source,
                "tracking_error_px": tracking_error_px,
                "centroid_error_px": centroid_error_px,
                "confidence": confidence,
                "target_in_fov": bool(target_in_fov),
                "beacon_hidden": bool(beacon_hidden),
                "pattern": pattern,
                "disturbances": dict(disturbances),
            }
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self):
        frames = self.frames
        n = len(frames)
        s = {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "frames": n,
            "duration_s": self.active_time,
        }
        if n == 0:
            return s

        # --- Frame rate and processing time ---------------------------
        intervals = np.array(
            [f["frame_interval_ms"] for f in frames if f["frame_interval_ms"]],
            dtype=float,
        )
        processing = np.array([f["processing_ms"] for f in frames], dtype=float)
        s["fps_mean"] = (n - 1) / self.active_time if self.active_time > 0 else None
        s["fps_min"] = (
            1000.0 / float(np.percentile(intervals, 99)) if intervals.size else None
        )
        s["frame_interval_ms_mean"] = (
            float(intervals.mean()) if intervals.size else None
        )
        s["processing_ms_mean"] = float(processing.mean())
        s["processing_ms_p95"] = float(np.percentile(processing, 95))
        s["processing_ms_max"] = float(processing.max())
        s["processing_fps_capacity"] = (
            1000.0 / s["processing_ms_mean"] if s["processing_ms_mean"] > 0 else None
        )

        # --- Acquisition ----------------------------------------------
        first_lock = next(
            (i for i, f in enumerate(frames) if f["state"] == STATE_LOCKED), None
        )
        s["acquisition_time_s"] = (
            None if first_lock is None else frames[first_lock]["t_s"] - frames[0]["t_s"]
        )

        # --- Loss / re-acquisition events -----------------------------
        # A loss event starts on the first SEEKING frame after the target
        # was held, and ends on the next LOCKED frame.
        reacq = []
        unrecovered = 0
        occlusions = []
        loss_start = None
        occl_start = None
        held = False
        for f in frames:
            state = f["state"]
            if state == STATE_PREDICTING:
                if occl_start is None:
                    occl_start = f["t_s"]
            elif occl_start is not None:
                occlusions.append(f["t_s"] - occl_start)
                occl_start = None

            if state == STATE_SEEKING:
                if held and loss_start is None:
                    loss_start = f["t_s"]
            elif state == STATE_LOCKED:
                if loss_start is not None:
                    reacq.append(f["t_s"] - loss_start)
                    loss_start = None
                held = True
        unrecovered_s = None
        if loss_start is not None:
            unrecovered = 1
            unrecovered_s = frames[-1]["t_s"] - loss_start
        if occl_start is not None:
            occlusions.append(frames[-1]["t_s"] - occl_start)

        s["loss_events"] = len(reacq) + unrecovered
        s["unrecovered_losses"] = unrecovered
        s["unrecovered_loss_s"] = unrecovered_s
        s["reacquisition_times_s"] = reacq
        s["reacquisition_s_mean"] = float(np.mean(reacq)) if reacq else None
        s["reacquisition_s_max"] = float(np.max(reacq)) if reacq else None
        s["occlusion_events"] = len(occlusions)
        s["occlusion_s_total"] = float(sum(occlusions))

        # --- Lock retention -------------------------------------------
        # Counted from first acquisition. Deliberate occlusion windows
        # (HIDE BEACON) are excluded from both sides of the ratio.
        after = frames[first_lock:] if first_lock is not None else []
        locked = sum(1 for f in after if f["state"] == STATE_LOCKED)
        seeking = sum(1 for f in after if f["state"] == STATE_SEEKING)
        if locked + seeking:
            s["lock_retention_pct"] = 100.0 * locked / (locked + seeking)
            s["target_loss_pct"] = 100.0 - s["lock_retention_pct"]
        else:
            s["lock_retention_pct"] = None
            s["target_loss_pct"] = None
        s["frames_locked"] = sum(1 for f in frames if f["state"] == STATE_LOCKED)
        s["frames_predicting"] = sum(
            1 for f in frames if f["state"] == STATE_PREDICTING
        )
        s["frames_seeking"] = sum(1 for f in frames if f["state"] == STATE_SEEKING)

        # --- Tracking error (ground truth vs boresight, locked frames) --
        s.update(_error_stats(
            [f["tracking_error_px"] for f in frames if f["state"] == STATE_LOCKED],
            "tracking_error",
        ))
        # --- Centroiding error (tracker estimate vs ground truth) -------
        s.update(_error_stats(
            [f["centroid_error_px"] for f in frames if f["state"] == STATE_LOCKED],
            "centroid_error",
        ))

        # --- Detection sources, scenario ------------------------------
        sources = Counter(f["source"] for f in frames)
        s["source_pct"] = {k: 100.0 * v / n for k, v in sources.most_common()}
        s["patterns"] = list(dict.fromkeys(f["pattern"] for f in frames))
        max_levels = {}
        for f in frames:
            for k, v in f["disturbances"].items():
                max_levels[k] = max(max_levels.get(k, 0), v)
        s["disturbance_max_levels"] = max_levels

        s["criteria"] = _criteria(s)
        return s


def _error_stats(values, prefix):
    arr = np.array(
        [v for v in values if v is not None and math.isfinite(v)], dtype=float
    )
    if arr.size == 0:
        return {
            f"{prefix}_samples": 0,
            f"{prefix}_mean_px": None,
            f"{prefix}_rmse_px": None,
            f"{prefix}_p95_px": None,
            f"{prefix}_max_px": None,
            f"{prefix}_within_spec_pct": None,
        }
    return {
        f"{prefix}_samples": int(arr.size),
        f"{prefix}_mean_px": float(arr.mean()),
        f"{prefix}_rmse_px": float(np.sqrt(np.mean(arr ** 2))),
        f"{prefix}_p95_px": float(np.percentile(arr, 95)),
        f"{prefix}_max_px": float(arr.max()),
        f"{prefix}_within_spec_pct": float(
            100.0 * np.mean(arr <= SPEC_TRACKING_ERROR_PX)
        ),
    }


def _criteria(s):
    """Pass/fail rows against the problem statement's specifications.
    ``passed`` is None when the run produced no data for that metric."""

    def row(name, spec, value, unit, passed):
        return {
            "name": name, "spec": spec, "value": value,
            "unit": unit, "passed": passed,
        }

    def check(value, ok):
        return None if value is None else bool(ok(value))

    acq = s.get("acquisition_time_s")
    rmse = s.get("tracking_error_rmse_px")
    loss = s.get("target_loss_pct")
    reacq = s.get("reacquisition_s_max")
    # A loss still open when the run ended fails once it has lasted longer
    # than the spec; a shorter one can't be judged yet.
    open_loss = s.get("unrecovered_loss_s")
    if open_loss is not None:
        reacq = max(reacq or 0.0, open_loss)
    if open_loss is not None and open_loss > SPEC_REACQUISITION_S:
        reacq_passed = False
    elif open_loss is not None and reacq is None:
        reacq_passed = None
    else:
        reacq_passed = check(reacq, lambda v: v <= SPEC_REACQUISITION_S)
    fps = s.get("fps_mean")
    return [
        row("Acquisition time", f"<= {SPEC_ACQUISITION_S:g} s", acq, "s",
            check(acq, lambda v: v <= SPEC_ACQUISITION_S)),
        row("Tracking error (RMS)", f"<= {SPEC_TRACKING_ERROR_PX:g} px", rmse, "px",
            check(rmse, lambda v: v <= SPEC_TRACKING_ERROR_PX)),
        row("Target loss", f"< {SPEC_TARGET_LOSS_PCT:g} %", loss, "%",
            check(loss, lambda v: v < SPEC_TARGET_LOSS_PCT)),
        row("Re-acquisition time (max)", f"<= {SPEC_REACQUISITION_S:g} s", reacq, "s",
            reacq_passed),
        row("Processing speed", f">= {SPEC_MIN_FPS:g} FPS", fps, "FPS",
            check(fps, lambda v: v >= SPEC_MIN_FPS)),
    ]
