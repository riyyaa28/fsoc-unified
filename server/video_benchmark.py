"""Video Benchmark mode: process an uploaded (evaluator) MP4 with the SAME
tracking core as the 3D simulation.

Ported from the supplied 2D dashboard's video path (``_upload_video`` /
``_render_video_frame``): the uploaded frames are the camera input, the
physical PTZ is bypassed, the beacon centroid is detected per frame and a
Kalman track is maintained.  In addition to the original behaviour, a
*virtual* PTZ boresight (starting at the screen centre and limited by the
configured pan/tilt speeds) follows the beacon inside the frame, so tracking
error, acquisition and re-acquisition are measured exactly as in simulation.

Detector: the trained YOLO model (``beacon_yolo.pt``) when ultralytics is
installed, otherwise the shared ring-signature/bright-blob detector.
"""

from __future__ import annotations

import base64
import csv
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from disturbance.sensor_model import DisturbanceModel
from sim.overlay import draw_crosshair
from vision.tracking_core import SIH_LIMITS, Candidate, PerformanceMeter, RateLimitedPTZ, TrackCore, detect_candidates


class VideoBenchmark:
    def __init__(self, upload_dir: Path, reports_dir: Path) -> None:
        self.upload_dir, self.reports_dir = upload_dir, reports_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.yolo: Any | None = None
        self.yolo_error: str | None = None
        self._clear()

    def _clear(self) -> None:
        self.path: Path | None = None
        self.meta: dict[str, Any] = {}
        self.status = "EMPTY"            # EMPTY | READY | PROCESSING | COMPLETE | STOPPED | ERROR
        self.message = "Upload an .mp4 file to start a benchmark."
        self.frame_index = 0
        self.rows: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.image = ""
        self.current: dict[str, Any] = {}
        self.meter = PerformanceMeter()
        self.run_id: str | None = None
        self.settings: dict[str, Any] = {}
        self.report: dict[str, Any] | None = None

    @property
    def active(self) -> bool:
        return self.status != "EMPTY"

    # ------------------------------------------------------------------
    def load(self, path: Path, filename: str) -> dict[str, Any]:
        self.stop()
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            path.unlink(missing_ok=True)
            raise ValueError("Could not open the uploaded file as a video.")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        fps_reported = fps if math.isfinite(fps) and 1.0 < fps <= 240.0 else None
        ok, frame = capture.read()
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        capture.release()
        if not ok or frame is None:
            path.unlink(missing_ok=True)
            raise ValueError("The video contains no readable frames.")
        with self.lock:
            self._clear()
            self.path = path
            h, w = frame.shape[:2]
            self.meta = {
                "filename": filename, "width": w, "height": h, "fps": fps_reported or 30.0,
                "fps_reported": fps_reported, "frames": total, "duration_s": round(total / (fps_reported or 30.0), 2) if total else None,
                "is_30fps": bool(fps_reported and abs(fps_reported - 30.0) < 0.6),
            }
            self.status = "READY"
            self.message = f"Loaded {filename}: {w}x{h} @ {self.meta['fps']:.2f} FPS" + ("" if fps_reported else " (FPS not reported; assuming 30)")
            self.image = self._encode(self._annotate(frame, None, [], (w / 2, h / 2)))
        return self.meta

    def start(self, settings: dict[str, Any], on_event: Callable[[str], None] | None = None) -> None:
        if self.path is None:
            raise ValueError("Upload a video first.")
        self.stop()
        with self.lock:
            path, meta = self.path, dict(self.meta)
            self._clear()
            self.path, self.meta = path, meta
            self.settings = settings
            self.run_id = time.strftime("video_%Y%m%dT%H%M%S")
            self.status = "PROCESSING"
            self.message = "Processing frames..."
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._process, name="video-benchmark", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.thread and self.thread.is_alive():
            self.stop_flag.set()
            self.thread.join(timeout=5)
        self.thread = None

    def clear(self) -> None:
        self.stop()
        with self.lock:
            if self.path:
                self.path.unlink(missing_ok=True)
            self._clear()

    # ------------------------------------------------------------------
    def _event(self, t: float, message: str) -> None:
        self.events.append({"timestamp": round(t, 3), "message": message, "category": "video"})
        self.events = self.events[-120:]

    def _detector(self, detector: str) -> str:
        if detector != "yolo":
            return "classical"
        if self.yolo is None and self.yolo_error is None:
            try:
                from vision.yolo_detector import YoloBeaconDetector
                self.yolo = YoloBeaconDetector(str(Path(__file__).resolve().parents[1] / "beacon_yolo.pt"))
            except Exception as error:
                self.yolo_error = error.__class__.__name__
        return "yolo" if self.yolo is not None else "classical"

    def _process(self) -> None:
        s = self.settings
        capture = cv2.VideoCapture(str(self.path))
        fps = float(self.meta["fps"])
        w, h = int(self.meta["width"]), int(self.meta["height"])
        detector = self._detector(s.get("detector", "classical"))
        if s.get("detector") == "yolo" and detector != "yolo":
            self._event(0.0, f"YOLO unavailable ({self.yolo_error}); using classical ring/blob detector")
        disturbance = DisturbanceModel(seed=7)
        if s.get("apply_disturbances"):
            disturbance.configure(s.get("disturbances", {}))
            self._event(0.0, "Scenario disturbances applied to video frames")
        # Virtual PTZ inside the frame: degrees per pixel from the configured FOV.
        deg_px = (float(s.get("fov_h_deg", 4.0)) / w, float(s.get("fov_v_deg", 3.0)) / h)
        ptz = RateLimitedPTZ((0.0, 0.0, float(w), float(h)), deg_px, float(s.get("max_pan_dps", 8.0)), float(s.get("max_tilt_dps", 8.0)), bandwidth_hz=3.0)
        ptz.set_position(w / 2, h / 2)            # initial camera position: screen centre
        track = TrackCore(confirm_frames=int(s.get("confirm_frames", 3)), coast_frames=int(s.get("coast_frames", 12)), gate=max(25.0, 0.06 * w), accept=("ring", "blob"))
        threshold = float(s.get("link_threshold_px", 10.0))
        size = float(s.get("target_size_px", 10.0))
        realtime = s.get("pacing", "realtime") == "realtime"
        self.meter.reset(0.0)
        self.meter.begin_acquisition(0.0)
        index = 0
        wall_start = time.perf_counter()
        try:
            while not self.stop_flag.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                started = time.perf_counter()
                t = index / fps
                if disturbance.any_image_effect():
                    frame = disturbance.apply_frame(frame)
                candidates: list[Candidate]
                if detector == "yolo":
                    try:
                        point, conf = self.yolo.detect(frame)
                    except Exception:
                        point, conf = None, 0.0
                    candidates = [Candidate(float(point[0]), float(point[1]), float(conf), "ring", 0.0, 0.0)] if point is not None else []
                else:
                    candidates, _ = detect_candidates(frame, size)
                result = track.update([(c.x, c.y, c.score, c.kind) for c in candidates], (ptz.x, ptz.y), acquire_gate=float(max(w, h)))
                # Signature memory: once the locked target has shown the beacon
                # ring signature, plain bright blobs (decoys, glints) are no
                # longer eligible for (re-)acquisition.
                if result["locked"] and result.get("kind") == "ring" and track.accept != ("ring",):
                    track.accept = ("ring",)
                    self._event(t, "Beacon ring signature confirmed; plain bright blobs now rejected")
                aim_before = (ptz.x, ptz.y)
                meas = result["measurement"]
                error = math.hypot(meas[0] - aim_before[0], meas[1] - aim_before[1]) if meas else None
                centre_offset = math.hypot(meas[0] - w / 2, meas[1] - h / 2) if meas else None
                if result["locked"] and result["estimate"]:
                    vx, vy = track.velocity()
                    ptz.step(result["estimate"], 1 / fps, (vx * fps, vy * fps))
                elif meas:
                    ptz.step(meas, 1 / fps)
                else:
                    ptz.step(None, 1 / fps)
                processing_ms = (time.perf_counter() - started) * 1000
                centred = bool(result["locked"] and error is not None and error <= threshold)
                for message in self.meter.record(t, 1 / fps, processing_ms, result["locked"], result["detected"], error, True, centred):
                    self._event(t, message)
                row = {
                    "frame": index, "time_s": round(t, 4), "detected": result["detected"], "locked": result["locked"], "state": result["state"],
                    "centroid_x": round(meas[0], 2) if meas else None, "centroid_y": round(meas[1], 2) if meas else None,
                    "camera_centre_x": round(aim_before[0], 2), "camera_centre_y": round(aim_before[1], 2),
                    "tracking_error_px": round(error, 3) if error is not None else None,
                    "offset_from_frame_centre_px": round(centre_offset, 3) if centre_offset is not None else None,
                    "pan_cmd_deg": round((aim_before[0] - w / 2) * deg_px[0], 4), "tilt_cmd_deg": round((h / 2 - aim_before[1]) * deg_px[1], 4),
                    "confidence": round(result["score"], 3), "candidates": len(candidates), "processing_ms": round(processing_ms, 3),
                }
                index += 1
                publish = (not realtime and index % 3 == 0) or realtime
                with self.lock:
                    self.rows.append(row)
                    self.frame_index = index
                    self.current = row
                    if publish:
                        self.image = self._encode(self._annotate(frame, result, candidates, aim_before, row))
                if realtime:
                    delay = wall_start + index / fps - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
        except Exception as error:  # keep the server alive; surface the reason
            with self.lock:
                self.status, self.message = "ERROR", f"Processing failed: {error}"
            capture.release()
            return
        capture.release()
        with self.lock:
            stopped = self.stop_flag.is_set()
            self.status = "STOPPED" if stopped else "COMPLETE"
            self.message = f"{'Stopped' if stopped else 'Completed'} after {index} frames ({index / fps:.2f} s of video) in {time.perf_counter() - wall_start:.2f} s wall time."
            self.meta["frames_processed"] = index
            self.meta["detector_used"] = "YOLO (beacon_yolo.pt)" if detector == "yolo" else "Classical ring-signature / bright-blob"
            self._event(index / fps, self.message)

    # ------------------------------------------------------------------
    def _annotate(self, frame: np.ndarray, result: dict[str, Any] | None, candidates: list[Candidate], aim: tuple[float, float], row: dict[str, Any] | None = None) -> np.ndarray:
        view = frame.copy()
        h, w = view.shape[:2]
        k = max(1.0, w / 640)
        for c in candidates:
            s = int(9 * k)
            cv2.rectangle(view, (int(c.x) - s, int(c.y) - s), (int(c.x) + s, int(c.y) + s), (150, 150, 150), 1)
        # Frame centre (fixed physical camera centre) and virtual PTZ boresight.
        draw_crosshair(view, (w // 2, h // 2), size=int(10 * k), gap=int(4 * k), color=(160, 160, 160), thickness=1)
        draw_crosshair(view, (int(aim[0]), int(aim[1])), size=int(18 * k), gap=int(6 * k), color=(0, 255, 255), thickness=max(1, int(k)))
        if result and result.get("measurement"):
            mx, my = map(int, result["measurement"])
            s = int(16 * k)
            cv2.rectangle(view, (mx - s, my - s), (mx + s, my + s), (80, 255, 80), max(1, int(2 * k)))
            cv2.line(view, (int(aim[0]), int(aim[1])), (mx, my), (80, 255, 80), 1, cv2.LINE_AA)
        elif result and result.get("estimate"):
            ex, ey = map(int, result["estimate"])
            cv2.circle(view, (ex, ey), int(18 * k), (255, 190, 90), max(1, int(2 * k)), cv2.LINE_AA)
        if row:
            label = f"F{row['frame']}  {row['time_s']:.2f}s  {row['state']}"
            if row["tracking_error_px"] is not None:
                label += f"  ERR {row['tracking_error_px']:.1f}px"
            cv2.putText(view, label, (int(10 * k), int(24 * k)), cv2.FONT_HERSHEY_SIMPLEX, 0.55 * k, (255, 255, 255), max(1, int(k)), cv2.LINE_AA)
        return view

    @staticmethod
    def _encode(view: np.ndarray) -> str:
        h, w = view.shape[:2]
        if w > 800:
            view = cv2.resize(view, (800, int(h * 800 / w)), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii") if ok else ""

    def summary(self) -> dict[str, Any]:
        s = self.meter.summary()
        s["video"] = dict(self.meta)
        s["frames_processed"] = self.frame_index
        return s

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            series = [{"t": r["time_s"], "error": r["tracking_error_px"], "detected": r["detected"], "locked": r["locked"]} for r in self.rows[-240:]]
            total = self.meta.get("frames") or 0
            return {
                "status": self.status, "message": self.message, "meta": self.meta, "run_id": self.run_id,
                "frame_index": self.frame_index, "progress": round(min(1.0, self.frame_index / total), 4) if total else None,
                "image": self.image, "current": self.current, "summary": self.summary(), "series": series,
                "events": self.events[-40:], "settings": {k: v for k, v in self.settings.items() if k != "disturbances"},
                "report": self.report, "sih_limits": SIH_LIMITS,
            }

    def export(self, report_generator: Any, scenario: dict[str, Any]) -> dict[str, Any]:
        if not self.rows or self.run_id is None:
            raise ValueError("Process a video before generating its report.")
        with self.lock:
            rows = list(self.rows)
            summary = self.summary()
            settings = dict(self.settings)
            events = list(self.events)
        csv_path = self.reports_dir / f"{self.run_id}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        document = {"summary": {**summary, "session_id": self.run_id, "source": "video_benchmark", "duration": summary["duration_s"], "average_fps": summary["processing_fps"], "average_tracking_error": summary["error_mean_px"]}, "config": settings, "scenario": scenario, "events": events}
        (self.reports_dir / f"{self.run_id}.json").write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")
        report_generator.generate_video(self.run_id, summary, settings, rows, events, scenario)
        self.report = {"run_id": self.run_id, "csv": f"/api/performance/{self.run_id}/csv", "pdf": f"/api/performance/{self.run_id}/pdf", "json": f"/api/performance/{self.run_id}/json"}
        return self.report
