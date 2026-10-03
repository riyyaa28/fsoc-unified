from vision.yolo_detector import YoloBeaconDetector  # must load before PyQt5

import sys
import cv2
import math
import numpy as np
import time

from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QGridLayout,
    QComboBox,
    QSlider,
    QSizePolicy,
    QFileDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QDialog,
)

from PyQt5.QtGui import QDesktopServices, QImage, QPixmap
from PyQt5.QtCore import QTimer, Qt, QUrl

import os
from datetime import datetime

from ui.video_report import write_video_benchmark_report
from vision.video_tracker import (
    BenchmarkMetrics, VideoBeaconTracker, find_ground_truth, load_ground_truth,
)
from sim.scenario import ATMOSPHERES, PLATFORM_MOTIONS, ScenarioConfig, ScenarioRunner, apply_disturbances
from ui.scenario_dialog import ScenarioDialog
from ui.scenario_report import export_scenario_outputs


class Dashboard(QWidget):

    def __init__(self):
        super().__init__()

        # ==========================================================
        # WINDOW
        # ==========================================================

        self.setWindowTitle("FSOC Coarse Alignment Control Center")

        # Fit the complete dashboard inside the available screen.
        screen = QApplication.primaryScreen()

        if screen is not None:
            available = screen.availableGeometry()

            self.setMinimumSize(1000, 650)

            self.resize(
                max(1000, available.width() - 20),
                max(650, available.height() - 20),
            )

        else:
            self.setMinimumSize(1000, 650)

            self.resize(1200, 800)

        # ==========================================================
        # PIPELINE SETUP
        # ==========================================================

        # Benchmark-1 scenario engine: virtual PTZ camera over a large screen
        # (sim/scenario.py). A fresh ScenarioRunner is built on RESET / START.
        self.scenario_config = ScenarioConfig()
        self.runner = None

        # YOLO detector: AI assistance for acquisition (runs on a worker thread)
        self.detector = YoloBeaconDetector(
            weights_path="beacon_yolo.pt", conf_threshold=0.25
        )
        # The first inference takes seconds (model set-up, mostly Python). Do it
        # at start-up so it never competes with the 30 Hz loop later.
        try:
            self.detector.detect(np.zeros((320, 320, 3), np.uint8))
        except Exception:
            pass
        self._yolo_warm = True

        self.frame_count = 0
        self.acquisition_time = None
        self.reacquisition_time = None
        self.screen_center = (320, 240)

        # ==========================================================
        # SIMULATION CONTROLS
        # ==========================================================

        self.running = False
        self.media_capture = None
        self.media_path = None

        # HIDE BEACON: the target keeps moving but is not drawn.
        self.beacon_hidden = False
        self.search_state = "READY"

        # ==========================================================
        # TELEMETRY VARIABLES
        # ==========================================================

        self.current_dx = 0.0
        self.current_dy = 0.0
        self.current_error = float("nan")

        self.current_confidence = 0.0
        self.current_distance = None

        self.current_source = "kalman"
        self.current_processing_ms = 0.0
        self.current_fps = 0.0
        self.activity_log = []
        self.activity_sample_elapsed = 0.0
        self.activity_previous_state = None
        self._reset_report_session()

        # ==========================================================
        # STABLE DISPLAY TELEMETRY
        # ==========================================================

        self.display_confidence = 0.0
        self.CONFIDENCE_ALPHA = 0.12

        self.display_source = "YOLO"
        self.pending_source = "YOLO"
        self.source_hold_count = 0
        self.SOURCE_HOLD_FRAMES = 5

        # ==========================================================
        # UI
        # ==========================================================

        self._build_ui()

        # ==========================================================
        # TIMER
        # ==========================================================

        # Frame loop: a single-shot precise timer re-armed against an absolute
        # deadline every 1 / rate s (see _tick), so timer lateness is made up on
        # the next frame instead of permanently lowering the frame rate.
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)
        self.loop_rate_hz = self.scenario_config.update_rate_hz
        self._next_deadline = None

        self._sync_controls_from_config()
        self._new_runner()
        QTimer.singleShot(0, lambda: self._draw_scenario_views(preview=True))

        self.system_status.setText(
            "● SYSTEM READY   |   SIMULATION PAUSED   |   PRESS START"
        )

    # ==============================================================
    # UI CONSTRUCTION
    # ==============================================================

    def _build_ui(self):

        # ----------------------------------------------------------
        # TITLE
        # ----------------------------------------------------------

        title = QLabel("FSOC COARSE ALIGNMENT CONTROL CENTER")

        title.setObjectName("mainTitle")

        # ----------------------------------------------------------
        # SYSTEM STATUS
        # ----------------------------------------------------------

        self.system_status = QLabel("● SYSTEM INITIALIZING")

        self.system_status.setObjectName("systemStatus")
        # Text changes every frame; ignoring its size hint stops each update
        # from triggering a relayout / full-window repaint.
        self.system_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        # ----------------------------------------------------------
        # FULL SCENE VIEW
        # ----------------------------------------------------------

        self.full_scene_label = QLabel()

        # Size comes from the layout stretch only, not from the pixmap it shows,
        # so replacing the pixmap every frame never re-runs the layout.
        self.full_scene_label.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Ignored
        )

        self.full_scene_label.setMinimumSize(480, 300)

        self.full_scene_label.setAlignment(Qt.AlignCenter)

        self.full_scene_label.setObjectName("videoPanel")

        # ----------------------------------------------------------
        # PAT TELEMETRY
        # ----------------------------------------------------------

        self.pat_panel = QLabel("Initializing telemetry...")

        self.pat_panel.setMinimumWidth(0)

        # Reserve enough room for every telemetry row at the larger font.
        self.pat_panel.setMinimumHeight(450)
        self.pat_panel.setMaximumHeight(480)

        self.pat_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.pat_panel.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.pat_panel.setObjectName("telemetryPanel")

        # ----------------------------------------------------------
        # BORESIGHT VIEW
        # ----------------------------------------------------------

        boresight_title = QLabel("FSOC CAMERA / BORESIGHT")

        boresight_title.setObjectName("boresightTitle")

        self.boresight_label = QLabel()

        self.boresight_label.setAlignment(Qt.AlignCenter)

        self.boresight_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        # Give the telemetry panel the vertical space it needs; the camera
        # preview can shrink while remaining large enough to inspect.
        self.boresight_label.setMinimumSize(360, 170)

        self.boresight_label.setObjectName("boresightPanel")

        # ----------------------------------------------------------
        # RIGHT SIDE
        # ----------------------------------------------------------

        right_column = QVBoxLayout()

        right_column.setContentsMargins(0, 0, 0, 0)

        right_column.setSpacing(5)

        # Telemetry gets only its required height.
        right_column.addWidget(self.pat_panel, 0)

        # Boresight header.
        right_column.addWidget(boresight_title, 0)

        # Boresight receives ALL remaining vertical space.
        right_column.addWidget(self.boresight_label, 1)

        video_row = QHBoxLayout()
        video_row.setSpacing(8)
        video_row.addWidget(self.full_scene_label, 7)
        video_row.addLayout(right_column, 3)

        controls_title = QLabel("SIMULATION CONTROL")
        controls_title.setObjectName("sectionTitle")
        controls_panel = self._build_controls_panel()

        disturbance_title = QLabel("ENVIRONMENT / DISTURBANCE CONTROL")
        disturbance_title.setObjectName("sectionTitle")
        disturbance_panel = self._build_disturbance_panel()

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(5)
        main_layout.addWidget(title)

        main_layout.addLayout(video_row, 6)
        main_layout.addWidget(self.system_status)
        main_layout.addWidget(controls_title)
        main_layout.addLayout(controls_panel)
        main_layout.addWidget(disturbance_title)
        main_layout.addLayout(disturbance_panel)

        self.setLayout(main_layout)
        self.setStyleSheet("""

            QWidget {
                background-color: #071426;
                color: #c7e5ef;
                font-family: Consolas, 'Courier New', monospace;
            }

            QLabel#mainTitle {
                background-color: #0a1722;
                color: #6df7ff;
                border: 1px solid #236078;
                border-left: 4px solid #20e5ff;
                border-radius: 2px;
                padding: 9px 12px;
                font-size: 16px;
                font-weight: bold;
            }

            QLabel#videoPanel {
                background-color: #081a30;
                border: 1px solid #1a5266;
                border-radius: 2px;
            }

            QLabel#boresightTitle {
                background-color: #0b1a25;
                color: #66f3ff;
                border: 1px solid #20566a;
                border-left: 3px solid #18d8f2;
                border-radius: 1px;
                padding: 5px 8px;
                font-family: Consolas, monospace;
                font-size: 11px;
                font-weight: bold;
            }

            QLabel#boresightPanel {
                background-color: #081a30;
                border: 1px solid #22d6d0;
                border-radius: 1px;
            }

            QLabel#telemetryPanel {
                background-color: #091621;
                border: 1px solid #1c475b;
                border-top: 2px solid #1686a1;
                border-radius: 1px;
                padding: 7px;
                color: #a9d2df;
                font-family: Consolas, monospace;
                font-size: 9px;
            }

            QLabel#systemStatus {
                background-color: #082923;
                color: #65ffd2;
                border: 1px solid #17ae91;
                border-left: 4px solid #31ffd2;
                border-radius: 1px;
                padding: 8px 10px;
                font-family: Consolas;
                font-weight: bold;
            }

            QLabel#sectionTitle {
                color: #57c8e3;
                font-size: 10px;
                font-weight: bold;
                padding: 4px 7px 2px;
                border-bottom: 1px solid #173d50;
            }

            QPushButton {
                background-color: #0d1b27;
                color: #d5f5ff;
                border: 1px solid #286077;
                border-radius: 1px;
                padding: 5px 10px;
            }

            QPushButton:hover {
                background-color: #123244;
                border-color: #42eaff;
            }

            QPushButton:checked {
                background-color: #0d3c39;
                color: #71ffe1;
                border: 1px solid #25e2c0;
            }

            QComboBox {
                background-color: #0d1b27;
                color: #d5f5ff;
                border: 1px solid #286077;
                padding: 5px;
                border-radius: 1px;
            }

            QSlider::groove:horizontal {
                height: 4px;
                background: #344452;
            }

            QSlider::handle:horizontal {
                width: 12px;
                margin: -4px 0;
                background: #28d7b0;
                border-radius: 6px;
            }

            QToolTip {
                background-color: #0f2233;
                color: #e6f6ff;
                border: 1px solid #28d7b0;
                padding: 6px;
                font-family: Consolas;
                font-size: 12px;
            }
        """)

    # ==============================================================
    # CONTROLS
    # ==============================================================

    def _build_controls_panel(self):

        row = QHBoxLayout()

        # ----------------------------------------------------------
        # SIMULATION BUTTONS
        # ----------------------------------------------------------

        self.start_button = QPushButton("START")

        self.pause_button = QPushButton("PAUSE")

        self.reset_button = QPushButton("RESET")
        self.load_media_button = QPushButton("UPLOAD VIDEO")
        self.ground_truth_button = QPushButton("LOAD GROUND TRUTH")
        self.scenario_button = QPushButton("SCENARIO...")
        self.report_button = QPushButton("GENERATE REPORT")
        self.activity_button = QPushButton("ACTIVITY LOG")

        self.hide_button = QPushButton("HIDE BEACON")

        tips = {
            self.start_button: "Start (or resume) the simulation, or play the uploaded video.",
            self.pause_button: "Pause. START continues from the same point.",
            self.reset_button: "Stop and start a fresh scenario run (back to simulation mode "
                               "if a video was loaded).",
            self.scenario_button: "Open every scenario setting (camera, target, motion, noise, "
                                  "atmosphere, duration...) and save / load scenario files.",
            self.load_media_button: "Use a video file as the camera input instead of the simulation "
                                    "(Benchmark 2). The camera movement is bypassed.",
            self.ground_truth_button: (
                "<b>Video mode only.</b> Load a file with the <i>true</i> beacon position for each "
                "frame of the uploaded video (one row per frame: frame, x, y).<br><br>"
                "With it, the app compares its own measured beacon positions against the truth and "
                "reports the centroiding error and RMSE (how many pixels off it was).<br><br>"
                "Without it, the measured positions are still saved so they can be checked later.<br>"
                "A file named <i>&lt;video name&gt;_gt.csv</i> next to the video is loaded "
                "automatically, so you usually don't need this button."
            ),
            self.report_button: "Save the performance logs (per-frame CSV, summary) and the PDF "
                                "report to Downloads\\fsoc-benchmark, and open the report.",
            self.activity_button: "Show everything that has happened in this session.",
            self.hide_button: "Hide the beacon (it keeps moving) to test how the tracker coasts "
                              "and re-acquires it.",
        }
        for button, tip in tips.items():
            button.setToolTip(tip)

        self.start_button.clicked.connect(self._start_simulation)

        self.pause_button.clicked.connect(self._pause_simulation)

        self.reset_button.clicked.connect(self._reset_simulation)
        self.load_media_button.clicked.connect(self._load_media)
        self.ground_truth_button.clicked.connect(self._load_ground_truth)
        self.scenario_button.clicked.connect(self._open_scenario_dialog)
        self.report_button.clicked.connect(self._export_report_pdf)
        self.activity_button.clicked.connect(self._show_activity_log)

        self.hide_button.clicked.connect(self._toggle_beacon_visibility)

        row.addWidget(self.start_button)

        row.addWidget(self.pause_button)

        row.addWidget(self.reset_button)
        row.addWidget(self.scenario_button)
        row.addWidget(self.load_media_button)
        row.addWidget(self.ground_truth_button)
        row.addWidget(self.report_button)
        row.addWidget(self.activity_button)

        row.addWidget(self.hide_button)

        row.addSpacing(12)

        # ----------------------------------------------------------
        # PATTERN
        # ----------------------------------------------------------

        row.addWidget(QLabel("Pattern:"))

        self.pattern_combo = QComboBox()

        self.pattern_combo.addItems(
            ["Auto", "straight", "circular", "figure8", "random", "spiral", "sinusoidal"]
        )

        self.pattern_combo.setCurrentText(self.scenario_config.motion)

        self.pattern_combo.currentTextChanged.connect(self._on_pattern_changed)

        row.addWidget(self.pattern_combo)

        # ----------------------------------------------------------
        # DECOYS
        # ----------------------------------------------------------

        row.addWidget(QLabel("Decoys:"))

        self.decoy_slider = QSlider(Qt.Horizontal)

        self.decoy_slider.setMinimum(0)

        self.decoy_slider.setMaximum(10)

        self.decoy_slider.setValue(self.scenario_config.decoys)

        self.decoy_slider.valueChanged.connect(self._on_decoy_count_changed)

        row.addWidget(self.decoy_slider)

        self.decoy_count_label = QLabel(str(self.scenario_config.decoys))

        row.addWidget(self.decoy_count_label)

        return row

    def _start_simulation(self):
        if self.media_path:
            if self.media_capture is None:
                # Replaying a finished clip: start a fresh measurement session so
                # frame numbers line up with the ground truth again.
                truth_path = self.video_info.get("ground_truth")
                if not self._open_video(self.media_path, autostart=False):
                    return
                if truth_path and self.video_metrics.ground_truth is None:
                    self._apply_ground_truth(truth_path, announce=False)
            if self.media_capture is not None and self.media_capture.isOpened():
                self.running = True
                self._start_loop()
                self._record_activity("SIMULATION", "Uploaded video playback started")
                self.system_status.setText("● VIDEO PLAYING   |   BEACON DETECTION ACTIVE")
            return
        if not self.running:
            if self.runner is None or self.runner.finished():
                self._new_runner()
            self.loop_rate_hz = self.runner.cfg.update_rate_hz
            self.running = True
            self._last_tick = None
            self._start_loop()
            self._record_activity("SIMULATION", "Scenario running")

    def _pause_simulation(self):
        self.running = False
        self.timer.stop()
        self._record_activity("SIMULATION", "Simulation paused")
        self.system_status.setText("● SYSTEM PAUSED   |   PRESS START TO RESUME")

    def _load_media(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a video", "",
            "Video files (*.mp4 *.avi *.mov *.mkv *.m4v *.wmv *.webm)",
        )
        if not path:
            return
        self._open_video(path)

    def _open_video(self, path, autostart=True):
        """Open a video as the camera input and start a new measurement session."""
        self.timer.stop()
        self.running = False
        if self.media_capture is not None:
            self.media_capture.release()
            self.media_capture = None
        self.media_path = path
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            capture.release()
            self.media_path = None
            QMessageBox.warning(self, "Could not open video", "This video file could not be read.")
            self.media_path = None
            return False
        self.media_capture = capture
        fps = capture.get(cv2.CAP_PROP_FPS)
        self.loop_rate_hz = float(fps) if fps and math.isfinite(fps) and 1 <= fps <= 120 else 30.0
        self._reset_report_session()
        self.frame_count = 0
        total_frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        self.video_info = {
            "name": os.path.basename(path),
            "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": float(fps) if fps and math.isfinite(fps) and fps > 0 else None,
            "frames": int(total_frames) if total_frames and total_frames > 0 else None,
        }
        info = self.video_info
        length = (
            f", {info['frames'] / info['fps']:.1f} s" if info["frames"] and info["fps"] else ""
        )
        self._record_activity(
            "VIDEO",
            f"Loaded {info['name']} ({info['width']}x{info['height']}, "
            f"{info['fps'] or 0:.1f} fps{length})",
        )
        self._start_video_benchmark(path, capture)
        truth_note = "GROUND TRUTH LOADED" if self.video_metrics.ground_truth is not None else "NO GROUND TRUTH"
        self.system_status.setText(f"● VIDEO LOADED   |   PTZ BYPASSED   |   {truth_note}")
        if autostart:
            self.running = True
            self._start_loop()
        return True

    # ==============================================================
    # VIDEO INPUT (BENCHMARK 2: PTZ BYPASSED)
    # ==============================================================

    VIDEO_FOV_DEG = (4.0, 3.0)      # reference camera FOV, used for angular error

    def _start_video_benchmark(self, path, capture):
        """Set up the coarse-pointing pipeline and metrics for a new video."""
        info = self.video_info
        self.video_tracker = VideoBeaconTracker(
            info["width"], info["height"], info["fps"] or 30.0, beacon_size=10, yolo=self.detector,
        )
        # YOLO's first inference takes seconds (model warm-up). Do it now,
        # before playback, so it neither freezes a frame nor skews timing.
        if not getattr(self, "_yolo_warm", False):
            try:
                self.detector.detect(np.zeros((320, 320, 3), np.uint8))
            except Exception:
                pass
            self._yolo_warm = True
        gt_path = find_ground_truth(path)
        self.video_metrics = BenchmarkMetrics(
            info["fps"] or 30.0, info["width"], info["height"], self.VIDEO_FOV_DEG, None,
        )
        if gt_path:
            self._apply_ground_truth(gt_path, announce=False)

    def _apply_ground_truth(self, gt_path, announce=True):
        try:
            truth = load_ground_truth(gt_path, self.video_metrics.fps)
        except Exception as exc:
            QMessageBox.warning(self, "Ground truth not loaded", f"{os.path.basename(gt_path)}:\n{exc}")
            return False
        # Recompute live values for any frames already processed.
        old = self.video_metrics
        self.video_metrics = BenchmarkMetrics(old.fps, old.width, old.height, old.fov_deg, truth)
        self.video_metrics.wall_started = old.wall_started
        for result, frame_ms in zip(old.results, old.frame_ms or [None] * len(old.results)):
            self.video_metrics.add(result, frame_ms)
        self.video_info["ground_truth"] = gt_path
        visible = sum(1 for value in truth.values() if value is not None)
        self._record_activity(
            "GROUND TRUTH",
            f"Loaded {os.path.basename(gt_path)}: {len(truth)} frames, beacon visible in {visible}",
        )
        if announce:
            QMessageBox.information(
                self, "Ground truth loaded",
                f"{os.path.basename(gt_path)}\n\n{len(truth)} frames ({visible} with the beacon visible).\n"
                "Centroiding error and RMSE will be computed against it.",
            )
        return True

    def _load_ground_truth(self):
        if not self.media_path or getattr(self, "video_metrics", None) is None:
            QMessageBox.information(self, "Load a video first",
                                    "Upload a video, then load its ground-truth centroid file.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ground-truth centroid file", os.path.dirname(self.media_path),
            "Ground truth (*.csv *.txt);;All files (*)",
        )
        if path:
            self._apply_ground_truth(path)

    def _process_uploaded_frame(self, frame, frame_started=None):
        """Run one video frame through the coarse-pointing pipeline (PTZ bypassed)."""
        frame_started = frame_started or time.perf_counter()
        index = self.video_metrics_frame_index()
        result = self.video_tracker.process(frame, index)
        metrics = self.video_metrics
        truth = metrics.truth(index)
        error = metrics.centroid_error(result)

        self._draw_video_views(frame, result, truth)

        live_state = {"LOCKED": "LOCKED", "TENTATIVE": "ACQUIRING", "COAST": "COASTING"}.get(result.state, "SEARCHING")
        self.search_state = live_state
        if result.measured:
            status = f"● BEACON {live_state}   |   ({result.x:.1f}, {result.y:.1f}) px   |   SNR {result.snr:.0f}"
        elif result.state == "COAST":
            status = "● BEACON COASTING   |   FOLLOWING KALMAN PREDICTION"
        else:
            status = "● SEARCHING FOR BEACON   |   WHOLE-FRAME ACQUISITION"
        self.system_status.setText(status)

        # Detection confidence shown on the panel: SNR mapped to 0..1
        # (0.5 at the tracking threshold, 1.0 at four times the threshold).
        confidence = min(1.0, result.snr / (4 * VideoBeaconTracker.TRACK_SNR)) if result.measured else 0.0
        self.display_confidence = confidence
        self.display_source = result.source or "--"
        self.current_confidence = confidence
        self.current_source = result.source or "--"
        self.current_distance = None
        self.current_error = error[2] if error else float("nan")
        bore = metrics.boresight(result)
        center = (metrics.width / 2.0, metrics.height / 2.0)
        self.frame_count += 1
        if self.frame_count % 30 == 0:
            self._sample_activity()

        live = metrics.live_values()
        self._update_pat_panel(
            (result.x, result.y) if result.x is not None else None, confidence, self.display_source,
            math.hypot(bore[0], bore[1]) if bore else float("nan"), None,
            telemetry_center=center, ptz_bypassed=True,
            overrides={
                "acquisition": self._fmt(live["acquisition_s"], 2, " s"),
                "reacquisition": self._fmt(live["reacquisition_s"], 2, " s"),
                "distance_label": "Centroid err",
                "distance": (self._fmt(live["error_px"], 2, " px") if metrics.ground_truth is not None
                             else "no truth file"),
                "pan_label": "Centroid RMSE",
                "pan": self._fmt(live["rmse_px"], 2, " px") if metrics.ground_truth is not None else "no truth file",
                "tilt_label": "Lock retention",
                "tilt": self._fmt(live["lock_retention_pct"], 1, " %"),
                "loop": "MEDIAN → MATCHED FILTER (+YOLO) → CENTROID → KALMAN",
            },
        )
        self.current_processing_ms = result.processing_ms
        frame_ms = (time.perf_counter() - frame_started) * 1000.0
        metrics.add(result, frame_ms)
        self.current_fps = min(self.video_metrics.fps, 1000.0 / max(1.0, frame_ms))

    def video_metrics_frame_index(self):
        return len(self.video_metrics.results)

    def _draw_video_views(self, frame, result, truth):
        """Scene view: whole frame with markers. Bore-sight view: zoom on the beacon."""
        height, width = frame.shape[:2]
        label = self.full_scene_label
        scale = min(max(label.width(), 320) / width, max(label.height(), 240) / height)
        display = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))),
                             interpolation=cv2.INTER_AREA)
        if display.ndim == 2:
            display = cv2.cvtColor(display, cv2.COLOR_GRAY2BGR)
        dh, dw = display.shape[:2]
        cv2.drawMarker(display, (dw // 2, dh // 2), (0, 255, 255), cv2.MARKER_CROSS, 24, 1)
        if truth is not None:
            tx, ty = int(round(truth[0] * scale)), int(round(truth[1] * scale))
            cv2.drawMarker(display, (tx, ty), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 14, 1)
        if result.x is not None:
            bx, by = int(round(result.x * scale)), int(round(result.y * scale))
            color = (0, 255, 0) if result.measured else (0, 170, 255)
            box = max(8, int(result.size_px * scale) + 8) if result.measured else 14
            cv2.rectangle(display, (bx - box, by - box), (bx + box, by + box), color, 2)
            cv2.putText(display, f"{result.state} {result.x:.1f},{result.y:.1f}", (max(4, bx + box + 4), max(16, by - box)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
        cv2.putText(display, f"FRAME {result.frame}  t={result.time_s:.2f}s  {result.processing_ms:.1f} ms",
                    (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1, cv2.LINE_AA)
        if truth is not None:
            cv2.putText(display, "+ ground truth", (8, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 1, cv2.LINE_AA)
        label.setPixmap(self._cv_to_qpixmap(display, label))

        # Bore-sight view: 4x zoom around the reported beacon position (or the centre).
        cx, cy = (result.x, result.y) if result.x is not None else (width / 2, height / 2)
        half_w, half_h = 60, 45
        x0 = int(np.clip(round(cx) - half_w, 0, max(0, width - 2 * half_w)))
        y0 = int(np.clip(round(cy) - half_h, 0, max(0, height - 2 * half_h)))
        crop = frame[y0:y0 + 2 * half_h, x0:x0 + 2 * half_w]
        if crop.size:
            zoom = cv2.resize(crop, (crop.shape[1] * 4, crop.shape[0] * 4), interpolation=cv2.INTER_NEAREST)
            if zoom.ndim == 2:
                zoom = cv2.cvtColor(zoom, cv2.COLOR_GRAY2BGR)
            if result.x is not None:
                zx, zy = int(round((result.x - x0) * 4)), int(round((result.y - y0) * 4))
                color = (0, 255, 0) if result.measured else (0, 170, 255)
                cv2.drawMarker(zoom, (zx, zy), color, cv2.MARKER_CROSS, 40, 2)
            if truth is not None:
                gx, gy = int(round((truth[0] - x0) * 4)), int(round((truth[1] - y0) * 4))
                cv2.drawMarker(zoom, (gx, gy), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 24, 2)
            self.boresight_label.setPixmap(self._cv_to_qpixmap(zoom, self.boresight_label))

    def _video_output_base(self):
        folder = os.path.join(os.path.expanduser("~"), "Downloads", "fsoc-benchmark")
        os.makedirs(folder, exist_ok=True)
        stem = os.path.splitext(self.video_info.get("name", "video"))[0]
        return os.path.join(folder, f"{stem}_{datetime.now().strftime('%Y%m%d-%H%M%S')}")

    def _export_video_outputs(self, base=None, complete=None):
        """Write centroid log, summary and PDF for the current video. Returns the PDF path."""
        base = base or self._video_output_base()
        metrics = self.video_metrics
        info = dict(self.video_info)
        metrics.write_csv(base + "_centroids.csv")
        metrics.write_summary(base + "_summary.csv", {
            "video": self.media_path, "resolution": f"{info['width']}x{info['height']}",
            "video_fps": info.get("fps"), "ground_truth_file": info.get("ground_truth") or "",
            "fov_deg": f"{metrics.fov_deg[0]}x{metrics.fov_deg[1]}",
        })
        events = [
            (entry["time"][11:], self.REPORT_EVENT_NAMES.get(entry["event"], entry["event"]), entry["details"])
            for entry in self._session_events()
        ]
        pdf = base + "_report.pdf"
        write_video_benchmark_report(
            pdf, metrics, info, events, self.video_complete if complete is None else complete,
        )
        return pdf

    def _finish_video(self):
        """End of the clip: log it and export the benchmark outputs automatically."""
        if self.video_complete:
            return
        self.video_complete = True
        processed = len(self.video_metrics.results)
        self._record_activity(
            "VIDEO",
            f"Playback complete: {processed} frames processed ({processed / self.video_metrics.fps:.1f} s of video)",
        )
        try:
            base = self._video_output_base()
            self._export_video_outputs(base, complete=True)
        except Exception as exc:
            self._record_activity("REPORT", f"Automatic export failed: {exc}")
            self.system_status.setText(f"● VIDEO COMPLETE   |   EXPORT FAILED: {exc}")
            return
        self._record_activity("REPORT", f"Benchmark outputs saved: {base}_centroids.csv / _summary.csv / _report.pdf")
        s = self.video_metrics.summary()
        rmse = f"RMSE {s['rmse_px']:.2f} px   |   " if s.get("rmse_px") is not None else ""
        self.system_status.setText(
            f"● VIDEO COMPLETE   |   {rmse}LOCK {self._fmt(s.get('lock_retention_pct'), 1, ' %')}   |   "
            f"LOGS SAVED TO Downloads\\fsoc-benchmark"
        )

    # ==============================================================
    # SCENARIO SIMULATION (BENCHMARK 1: VIRTUAL PTZ CAMERA)
    # ==============================================================

    POISSON_LEVELS = {1: 200.0, 2: 60.0, 3: 15.0}      # photons at full white
    STATUS_STYLES = {
        "ok": "background-color:#123b35; color:#4dffd8; border:1px solid #28d7b0;",
        "warn": "background-color:#402c18; color:#ffd27a; border:1px solid #d99b42;",
        "idle": "",
    }

    def _new_runner(self):
        """Fresh scenario run from the current configuration."""
        self.runner = ScenarioRunner(self.scenario_config, yolo=self.detector, yolo_async=True)
        self.frame_count = 0
        self._overview_key = None
        self._fps_ema = 0.0
        self._last_tick = None
        self._scenario_exported = False
        self._reset_report_session()
        self._record_activity(
            "SCENARIO",
            f"Scenario '{self.runner.cfg.name}' started (seed {self.runner.seed}); "
            f"{self.runner.cfg.motion} motion, target {self.runner.cfg.target_size_px} px",
        )

    def _set_status(self, text, style="idle"):
        self.system_status.setText(text)
        if getattr(self, "_status_style", None) != style:
            # Restyling is costly; only do it when the state class changes.
            self._status_style = style
            extra = self.STATUS_STYLES[style]
            self.system_status.setStyleSheet(
                f"QLabel {{ {extra} border-radius:5px; padding:8px; font-family:Consolas; font-weight:bold; }}"
                if extra else ""
            )

    def _open_scenario_dialog(self):
        dialog = ScenarioDialog(self.scenario_config, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.scenario_config = dialog.config()
        self._sync_controls_from_config()
        self._record_activity("SCENARIO", f"Scenario '{self.scenario_config.name}' applied")
        self._reset_simulation()

    def _sync_controls_from_config(self):
        cfg = self.scenario_config
        widgets = [self.pattern_combo, self.decoy_slider] + list(self.disturbance_controls.values())
        for widget in widgets:
            widget.blockSignals(True)
        self.pattern_combo.setCurrentText("Auto" if cfg.auto_switch else cfg.motion)
        self.decoy_slider.setValue(cfg.decoys)
        self.decoy_count_label.setText(str(cfg.decoys))
        c = self.disturbance_controls
        c["salt_pepper_pct"].setValue(int(round(cfg.salt_pepper_pct)))
        c["gaussian_sigma"].setValue(int(round(cfg.gaussian_sigma)))
        poisson_level = 0
        if cfg.poisson:
            poisson_level = min(self.POISSON_LEVELS, key=lambda k: abs(self.POISSON_LEVELS[k] - cfg.poisson_peak))
        c["poisson"].setValue(poisson_level)
        c["jitter_px"].setValue(cfg.jitter_px)
        c["atmosphere"].setCurrentText(cfg.atmosphere)
        c["atmosphere_strength"].setValue(int(round(cfg.atmosphere_strength * 100)))
        c["platform_motion"].setCurrentText(cfg.platform_motion)
        c["platform_px_per_frame"].setValue(int(round(cfg.platform_px_per_frame)))
        for widget in widgets:
            widget.blockSignals(False)
        self._refresh_disturbance_labels()

    def _toggle_beacon_visibility(self):
        self.beacon_hidden = not self.beacon_hidden
        if self.runner is not None:
            self.runner.scene.beacon_hidden = self.beacon_hidden
        self.hide_button.setText("SHOW BEACON" if self.beacon_hidden else "HIDE BEACON")
        self._record_activity(
            "BEACON",
            "Beacon hidden; tracker coasts, then searches" if self.beacon_hidden
            else "Beacon visible again; re-acquisition timed from now",
        )

    def _reset_simulation(self):
        self.running = False
        self.timer.stop()
        if self.media_capture is not None:
            self.media_capture.release()
            self.media_capture = None
        self.media_path = None
        self.beacon_hidden = False
        self.hide_button.setText("HIDE BEACON")
        self.search_state = "READY"
        self._new_runner()
        self._draw_scenario_views(preview=True)
        self._set_status("● SYSTEM READY   |   SCENARIO RESET   |   PRESS START")

    def _build_disturbance_panel(self):
        """Live disturbance controls (spec values; the full set is in SCENARIO...)."""
        panel = QGridLayout()
        panel.setHorizontalSpacing(8)
        panel.setVerticalSpacing(3)
        panel.setColumnStretch(1, 1)
        panel.setColumnStretch(4, 1)
        self.disturbance_controls = {}
        self.disturbance_value_labels = {}

        def slider(key, maximum, tip):
            widget = QSlider(Qt.Horizontal)
            widget.setRange(0, maximum)
            widget.setToolTip(tip)
            widget.valueChanged.connect(lambda _v, k=key: self._on_disturbance_changed(k))
            widget.sliderReleased.connect(lambda k=key: self._log_disturbance(k))
            return widget

        def combo(key, items, tip):
            widget = QComboBox()
            widget.addItems(items)
            widget.setToolTip(tip)
            widget.currentTextChanged.connect(lambda _t, k=key: (self._on_disturbance_changed(k),
                                                                  self._log_disturbance(k)))
            return widget

        rows = [
            [("SALT & PEPPER", "salt_pepper_pct", slider("salt_pepper_pct", 20, "% of pixels (spec ~10 %)")),
             ("ATMOSPHERE", "atmosphere", combo("atmosphere", ATMOSPHERES, "Clear, haze, fog, rain, low light"))],
            [("GAUSSIAN σ", "gaussian_sigma", slider("gaussian_sigma", 50, "Standard deviation (spec max 20)")),
             ("STRENGTH", "atmosphere_strength", slider("atmosphere_strength", 100, "Atmospheric effect strength"))],
            [("POISSON", "poisson", slider("poisson", 3, "Photon shot noise: off / low / medium / high")),
             ("PLATFORM", "platform_motion", combo("platform_motion", PLATFORM_MOTIONS, "Platform motion (spec default linear)"))],
            [("JITTER", "jitter_px", slider("jitter_px", 20, "Camera jitter +/- px per frame (spec max 20)")),
             ("PLATFORM SPEED", "platform_px_per_frame", slider("platform_px_per_frame", 20, "px per frame (spec max 20)"))],
        ]
        for row, pair in enumerate(rows):
            for col, (label, key, widget) in enumerate(pair):
                base = col * 3
                name = QLabel(label)
                name.setMinimumWidth(110)
                value = QLabel("OFF")
                value.setMinimumWidth(62)
                value.setAlignment(Qt.AlignCenter)
                panel.addWidget(name, row, base)
                panel.addWidget(widget, row, base + 1)
                panel.addWidget(value, row, base + 2)
                self.disturbance_controls[key] = widget
                self.disturbance_value_labels[key] = value
        return panel

    def _disturbance_values(self):
        c = self.disturbance_controls
        level = c["poisson"].value()
        return {
            "salt_pepper_pct": float(c["salt_pepper_pct"].value()),
            "gaussian_sigma": float(c["gaussian_sigma"].value()),
            "poisson": level > 0,
            "poisson_peak": self.POISSON_LEVELS.get(level, self.scenario_config.poisson_peak),
            "jitter_px": int(c["jitter_px"].value()),
            "atmosphere": c["atmosphere"].currentText(),
            "atmosphere_strength": c["atmosphere_strength"].value() / 100.0,
            "platform_motion": c["platform_motion"].currentText(),
            "platform_px_per_frame": float(c["platform_px_per_frame"].value()),
        }

    def _refresh_disturbance_labels(self):
        v = self._disturbance_values()
        labels = self.disturbance_value_labels
        labels["salt_pepper_pct"].setText(f"{v['salt_pepper_pct']:.0f} %" if v["salt_pepper_pct"] else "OFF")
        labels["gaussian_sigma"].setText(f"σ {v['gaussian_sigma']:.0f}" if v["gaussian_sigma"] else "OFF")
        labels["poisson"].setText({0: "OFF", 1: "LOW", 2: "MED", 3: "HIGH"}[self.disturbance_controls["poisson"].value()])
        labels["jitter_px"].setText(f"±{v['jitter_px']} px" if v["jitter_px"] else "OFF")
        labels["atmosphere"].setText("")
        labels["atmosphere_strength"].setText(f"{v['atmosphere_strength']:.2f}")
        labels["platform_motion"].setText("")
        labels["platform_px_per_frame"].setText(f"{v['platform_px_per_frame']:.0f} px/f"
                                                if v["platform_px_per_frame"] else "OFF")

    def _on_disturbance_changed(self, key):
        values = self._disturbance_values()
        if key == "platform_px_per_frame" and values[key] > 0 and values["platform_motion"] == "none":
            # Spec default platform motion is linear.
            self.disturbance_controls["platform_motion"].blockSignals(True)
            self.disturbance_controls["platform_motion"].setCurrentText("linear")
            self.disturbance_controls["platform_motion"].blockSignals(False)
            values["platform_motion"] = "linear"
        for name, value in values.items():
            setattr(self.scenario_config, name, value)
        if self.runner is not None and not self.media_path:
            self.runner.update_disturbances(**values)
        self._refresh_disturbance_labels()
        if not isinstance(self.disturbance_controls[key], QSlider) or not self.disturbance_controls[key].isSliderDown():
            if isinstance(self.disturbance_controls[key], QSlider):
                self._log_disturbance(key)

    def _log_disturbance(self, key):
        label = self.disturbance_value_labels[key].text() or self._disturbance_values()[key]
        self._record_activity("PARAMETER", f"{key.replace('_', ' ')} set to {label}")

    def _on_pattern_changed(self, text):
        auto = text == "Auto"
        self.scenario_config.auto_switch = auto
        if not auto:
            self.scenario_config.motion = text
        if self.runner is not None:
            self.runner.set_pattern(self.scenario_config.motion, auto=auto)
        self._record_activity("PATTERN", f"Motion pattern set to {text}")

    def _on_decoy_count_changed(self, value):
        self.decoy_count_label.setText(str(value))
        self.scenario_config.decoys = int(value)
        if self.runner is not None:
            self.runner.set_decoys(int(value))
        self._record_activity("PARAMETER", f"Decoy target count set to {value}")

    def _scenario_step(self, frame_started):
        runner = self.runner
        now = time.perf_counter()
        if self._last_tick is not None:
            interval = now - self._last_tick
            rate = 1.0 / interval if interval > 0 else 0.0
            self._fps_ema = rate if not self._fps_ema else 0.9 * self._fps_ema + 0.1 * rate
        self._last_tick = now
        result = runner.step(frame_started)
        self.frame_count += 1
        self.current_fps = self._fps_ema
        self.current_processing_ms = result.processing_ms
        self.search_state = {"LOCKED": "LOCKED", "TENTATIVE": "ACQUIRING",
                             "COAST": "COASTING"}.get(result.state, "SEARCHING")
        error = runner.metrics.centroid_error(result)
        self.current_error = error[2] if error else float("nan")
        self._draw_scenario_views()
        if self.frame_count % 3 == 0:
            self._update_scenario_panel(result)
        if self.frame_count % 30 == 0:
            self._sample_activity()
        if self.search_state == "LOCKED":
            style, detail = "ok", f"LOCKED   |   CENTROID ERR {self._fmt(self.current_error, 2, ' px')}"
        elif self.search_state in ("ACQUIRING", "COASTING"):
            style, detail = "warn", self.search_state
        else:
            style, detail = "warn", "SEARCHING   |   SPIRAL SCAN"
        self._set_status(
            f"● t = {runner.time_s:6.2f} s   |   PAT: {detail}   |   {self._fps_ema:4.1f} FPS", style
        )
        if runner.finished():
            self._finish_scenario()

    def _finish_scenario(self):
        self.running = False
        self.timer.stop()
        if self._scenario_exported:
            return
        self._scenario_exported = True
        self._record_activity("SCENARIO", f"Scenario complete after {self.runner.time_s:.1f} s")
        try:
            base = self._scenario_output_base()
            self._export_scenario_outputs(base)
        except Exception as exc:
            self._record_activity("REPORT", f"Automatic export failed: {exc}")
            self._set_status(f"● SCENARIO COMPLETE   |   EXPORT FAILED: {exc}", "warn")
            return
        s = self.runner.metrics.summary()
        self._set_status(
            f"● SCENARIO COMPLETE   |   ACQ {self._fmt(s.get('acquisition_time_s'), 2, ' s')}   |   "
            f"LOCK {self._fmt(s.get('lock_retention_pct'), 1, ' %')}   |   RMSE {self._fmt(s.get('rmse_px'), 2, ' px')}"
            f"   |   LOGS SAVED TO Downloads\\fsoc-benchmark", "ok"
        )

    def _scenario_output_base(self):
        folder = os.path.join(os.path.expanduser("~"), "Downloads", "fsoc-benchmark")
        os.makedirs(folder, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in self.runner.cfg.name) or "scenario"
        return os.path.join(folder, f"scenario_{safe}_{datetime.now().strftime('%Y%m%d-%H%M%S')}")

    def _export_scenario_outputs(self, base=None):
        base = base or self._scenario_output_base()
        events = [
            (entry["time"][11:], self.REPORT_EVENT_NAMES.get(entry["event"], entry["event"]), entry["details"])
            for entry in self._session_events()
        ]
        export_scenario_outputs(self.runner, base, events=events, complete=self.runner.finished())
        self._record_activity("REPORT", f"Scenario logs saved: {base}_frames.csv / _summary.csv / _report.pdf")
        return base + "_report.pdf"

    def _update_scenario_panel(self, result):
        runner = self.runner
        cfg = runner.cfg
        live = runner.metrics.live_values()
        pan = (runner.camera[0] - cfg.screen_width / 2) / cfg.px_per_deg_x
        tilt = (runner.camera[1] - cfg.screen_height / 2) / cfg.px_per_deg_y
        center = (cfg.camera_width / 2.0, cfg.camera_height / 2.0)
        offset = (math.hypot(result.x - center[0], result.y - center[1]) if result.x is not None else float("nan"))
        confidence = min(1.0, result.snr / (4 * VideoBeaconTracker.TRACK_SNR)) if result.measured else 0.0
        self._update_pat_panel(
            (result.x, result.y) if result.x is not None else None, confidence, result.source or "--",
            offset, None, telemetry_center=center, ptz_bypassed=False,
            overrides={
                "acquisition": self._fmt(live["acquisition_s"], 2, " s"),
                "reacquisition": self._fmt(live["reacquisition_s"], 2, " s"),
                "distance_label": "Centroid err",
                "distance": self._fmt(live["error_px"], 2, " px"),
                "pan_label": "Pan / tilt",
                "pan": f"{pan:+.2f}° / {tilt:+.2f}°",
                "tilt_label": "Lock retention",
                "tilt": self._fmt(live["lock_retention_pct"], 1, " %"),
                "loop": "SPIRAL SEARCH → MATCHED FILTER (+YOLO) → KALMAN → PTZ",
            },
        )

    @staticmethod
    def _pixmap(image):
        if image.ndim == 2:
            h, w = image.shape
            qimg = QImage(image.data, w, h, w, QImage.Format_Grayscale8).copy()
        else:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(qimg)

    def _draw_scenario_views(self, preview=False):
        """Left: whole screen with the camera footprint. Right: the camera image."""
        runner = self.runner
        if runner is None:
            return
        if preview or self.frame_count % 2 == 0:
            # The screen overview is a thumbnail; every other frame is plenty.
            self._draw_screen_overview(runner)
        self._draw_camera_view(runner, preview)

    def _state_color(self):
        return {"LOCKED": (80, 255, 120), "ACQUIRING": (0, 210, 255),
                "COASTING": (0, 170, 255)}.get(self.search_state, (60, 60, 255))

    def _draw_screen_overview(self, runner):
        cfg = runner.cfg
        scene = runner.scene
        state = self.search_state
        color = self._state_color()

        label = self.full_scene_label
        lw, lh = max(label.width(), 320), max(label.height(), 240)
        scale = min(lw / cfg.screen_width, lh / cfg.screen_height)
        key = (id(runner), lw, lh)
        if self._overview_key != key:
            pad = scene.pad
            size = (max(1, int(cfg.screen_width * scale)), max(1, int(cfg.screen_height * scale)))
            if scene.background_bgr is not None:
                screen = scene.background_bgr[pad:pad + cfg.screen_height, pad:pad + cfg.screen_width]
                self._overview_base = cv2.resize(screen, size, interpolation=cv2.INTER_AREA)
            else:
                screen = scene.background[pad:pad + cfg.screen_height, pad:pad + cfg.screen_width]
                small = cv2.resize(screen, size, interpolation=cv2.INTER_AREA)
                small = cv2.convertScaleAbs(small, alpha=1.5, beta=12)  # brighten the dark sky for viewing
                self._overview_base = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
            self._overview_key = key
        view = self._overview_base.copy()
        # Decoy beacons: white spots at their own brightness.
        for d in scene.decoys:
            level = int(d["level"])
            cv2.circle(view, (int(d["pos"][0] * scale), int(d["pos"][1] * scale)),
                       max(2, int(round(d["size"] * scale))), (level, level, level), -1, cv2.LINE_AA)
        # The designated beacon: bright core with its halo rings (original look).
        bx, by = int(scene.motion.pos[0] * scale), int(scene.motion.pos[1] * scale)
        core = max(2, int(round(cfg.target_size_px * scale)))
        if runner.beacon_visible():
            if cfg.target_shape == "ringed":
                cv2.circle(view, (bx, by), int(core * 3.5), (80, 80, 80), 1, cv2.LINE_AA)
                cv2.circle(view, (bx, by), core * 2, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.circle(view, (bx, by), core, (255, 255, 255), -1, cv2.LINE_AA)
        else:
            cv2.circle(view, (bx, by), core * 2, (110, 110, 110), 1, cv2.LINE_AA)   # hidden: outline only
        cam = runner.camera
        x0, y0 = int((cam[0] - cfg.camera_width / 2) * scale), int((cam[1] - cfg.camera_height / 2) * scale)
        x1, y1 = int((cam[0] + cfg.camera_width / 2) * scale), int((cam[1] + cfg.camera_height / 2) * scale)
        cv2.rectangle(view, (x0, y0), (x1, y1), color, 2)
        cv2.drawMarker(view, (int(cam[0] * scale), int(cam[1] * scale)), color, cv2.MARKER_CROSS, 10, 1)
        cv2.putText(view, f"SCREEN {cfg.screen_width}x{cfg.screen_height}   t={runner.time_s:.2f}s   {state}",
                    (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
        label.setPixmap(self._pixmap(view))

    def _draw_camera_view(self, runner, preview):
        cfg = runner.cfg
        scene = runner.scene
        color = self._state_color()
        frame, origin = runner.last_frame, runner.last_origin
        if frame is None or preview:
            origin = runner.camera - np.array([cfg.camera_width / 2, cfg.camera_height / 2])
            frame = apply_disturbances(scene.render_camera(origin, runner.beacon_visible(), runner.time_s),
                                       cfg, runner.rng)
        blabel = self.boresight_label
        bw, bh = max(blabel.width(), 200), max(blabel.height(), 150)
        s = min(bw / cfg.camera_width, bh / cfg.camera_height)
        size = (max(1, int(cfg.camera_width * s)), max(1, int(cfg.camera_height * s)))
        # Colour view (the tracker still uses the monochrome sensor frame).
        cam_view = scene.colourise(frame, origin, size) if cfg.camera_type == "colour" else None
        if cam_view is None:
            cam_view = cv2.cvtColor(cv2.resize(frame, size, interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR)
        ch, cw = cam_view.shape[:2]
        cv2.drawMarker(cam_view, (cw // 2, ch // 2), (0, 255, 255), cv2.MARKER_CROSS, 22, 1)
        result = runner.last_result
        if not preview and result is not None and result.x is not None:
            px, py = int(result.x * s), int(result.y * s)
            # Box just outside the beacon (and its outer halo ring) so it stays visible.
            extent = cfg.target_size_px * (1.75 if cfg.target_shape == "ringed" else 0.6)
            box = int(extent * s) + 5
            cv2.rectangle(cam_view, (px - box, py - box), (px + box, py + box), color, 1)
        truth = runner.last_truth
        if not preview and truth is not None:
            cv2.drawMarker(cam_view, (int(truth[0] * s), int(truth[1] * s)), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 10, 1)
        blabel.setPixmap(self._pixmap(cam_view))

    def _record_activity(self, event, details, severity="INFO"):
        from datetime import datetime
        self.activity_log.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": str(event), "details": str(details), "severity": str(severity),
            "session": getattr(self, "report_session_id", 0),
        })
        if len(self.activity_log) > 3000:
            del self.activity_log[:500]

    def _sample_activity(self):
        """Once per second: log state changes, reference crossings and a telemetry sample."""
        metrics = self.video_metrics if self.media_path else (self.runner.metrics if self.runner else None)
        live = metrics.live_values() if metrics is not None else {}
        state = self.search_state
        if state != self.activity_previous_state:
            self._record_activity("TRACKING", f"Beacon state changed to {state}")
            self.activity_previous_state = state
        fps = self.current_fps
        lock = live.get("lock_retention_pct")
        checks = (("fps", 0 < fps < 30, f"Update rate {fps:.1f} Hz (reference 30 Hz)"),
                  ("lock", lock is not None and lock < 95, f"Beacon lock retention {self._fmt(lock, 1, '%')} (reference 95%)"))
        for key, crossed, details in checks:
            if crossed and key not in self.activity_alerts:
                self._record_activity("THRESHOLD ALERT", details, "ALERT")
            elif not crossed and key in self.activity_alerts:
                self._record_activity("THRESHOLD CLEARED", f"{key.upper()} back within reference")
            if crossed:
                self.activity_alerts.add(key)
            else:
                self.activity_alerts.discard(key)
        self._record_activity(
            "SAMPLE",
            f"state={state}; fps={fps:.1f}; centroid_error={self._fmt(live.get('error_px'), 2, 'px')}; "
            f"lock_retention={self._fmt(lock, 1, '%')}; processing={self.current_processing_ms:.2f}ms; "
            f"disturbances={self._disturbance_text()}",
        )

    def _disturbance_text(self):
        cfg = self.scenario_config
        parts = []
        if cfg.salt_pepper_pct:
            parts.append(f"s&p {cfg.salt_pepper_pct:g}%")
        if cfg.gaussian_sigma:
            parts.append(f"gaussian {cfg.gaussian_sigma:g}")
        if cfg.poisson:
            parts.append(f"poisson {cfg.poisson_peak:g}")
        if cfg.jitter_px:
            parts.append(f"jitter {cfg.jitter_px}px")
        if cfg.atmosphere != "clear":
            parts.append(f"{cfg.atmosphere} {cfg.atmosphere_strength:.2f}")
        if cfg.platform_motion != "none" and cfg.platform_px_per_frame:
            parts.append(f"platform {cfg.platform_motion} {cfg.platform_px_per_frame:g}px/f")
        return ", ".join(parts) or "none"

    def _reset_report_session(self):
        """Start a fresh measurement window for the next generated report."""
        self.report_session_id = getattr(self, "report_session_id", 0) + 1
        self.activity_alerts = set()
        self.activity_previous_state = None
        self.video_info = {}
        self.video_complete = False
        self.video_tracker = None
        self.video_metrics = None

    def _show_activity_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Beacon Activity Log")
        dialog.resize(1000, 580)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(self.activity_log), 4, dialog)
        table.setHorizontalHeaderLabels(["TIME", "TYPE", "DETAILS", "LEVEL"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        for row, entry in enumerate(self.activity_log):
            for col, key in enumerate(("time", "event", "details", "severity")):
                table.setItem(row, col, QTableWidgetItem(entry[key]))
        layout.addWidget(table)
        dialog.exec_()

    # ==============================================================
    # TECHNICAL REPORT
    # ==============================================================

    REPORT_EVENT_NAMES = {
        "THRESHOLD ALERT": "REFERENCE CROSSED",
        "THRESHOLD CLEARED": "REFERENCE RESTORED",
    }

    @staticmethod
    def _fmt(value, digits, unit=""):
        if value is None or not math.isfinite(value):
            return "--"
        return f"{value:.{digits}f}{unit}"

    def _session_events(self):
        return [
            entry for entry in self.activity_log
            if entry["event"] != "SAMPLE" and entry.get("session") == self.report_session_id
        ]

    def _export_report_pdf(self):
        now = datetime.now()
        report_id = now.strftime("FSOC-%Y%m%d-%H%M%S")
        video_mode = bool(self.media_path)
        kind = "video-tracking" if video_mode else "2d-tracking"
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(downloads, exist_ok=True)
        path = os.path.join(downloads, f"fsoc-{kind}-report-{report_id[5:]}.pdf")
        try:
            if video_mode:
                path = self._export_video_outputs()
            else:
                path = self._export_scenario_outputs()
        except Exception as exc:
            QMessageBox.warning(self, "Report not saved", f"The report could not be generated:\n{exc}")
            return
        self._record_activity("REPORT", f"Technical report saved to {path}")
        # Open the report that was just written, so an older file in
        # Downloads is never mistaken for it.
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        QMessageBox.information(
            self, "Report downloaded",
            f"Report saved to your Downloads folder and opened:\n\n{os.path.basename(path)}",
        )

    def _cv_to_qpixmap(self, bgr_frame, target_label=None):
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg)
        if target_label is not None:
            size = target_label.size()
            if size.width() > 0 and size.height() > 0:
                pix = pix.scaled(
                    size.width(),
                    size.height(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
        return pix

    def _update_pat_panel(
        self, tracked_pos, display_confidence, display_source, error, dist_m,
        telemetry_center=None, ptz_bypassed=False, overrides=None,
    ):
        center_x, center_y = telemetry_center or self.screen_center
        if tracked_pos is not None:
            dx = tracked_pos[0] - center_x
            dy = tracked_pos[1] - center_y

        else:
            dx = 0.0
            dy = 0.0

        error_text = f"{error:.1f} px" if math.isfinite(error) else "--"
        dist_text = f"{dist_m:.1f} m" if dist_m is not None else "--"
        acquisition_text = "--" if ptz_bypassed or self.acquisition_time is None else f"{self.acquisition_time:.2f} s"
        reacquisition_text = "--" if ptz_bypassed or self.reacquisition_time is None else f"{self.reacquisition_time:.2f} s"
        pan_text = "PTZ BYPASSED" if ptz_bypassed else "--"
        tilt_text = "PTZ BYPASSED" if ptz_bypassed else "--"
        loop_text = "YOLO → VIDEO DETECTION → PTZ BYPASSED" if ptz_bypassed else "YOLO → FUSION → KALMAN → PTZ"
        distance_label = "Distance"
        pan_label, tilt_label = "PAN", "TILT"
        if overrides:
            acquisition_text = overrides.get("acquisition", acquisition_text)
            reacquisition_text = overrides.get("reacquisition", reacquisition_text)
            dist_text = overrides.get("distance", dist_text)
            distance_label = overrides.get("distance_label", distance_label)
            pan_text = overrides.get("pan", pan_text)
            tilt_text = overrides.get("tilt", tilt_text)
            pan_label = overrides.get("pan_label", pan_label)
            tilt_label = overrides.get("tilt_label", tilt_label)
            loop_text = overrides.get("loop", loop_text)

        panel_html = f"""
        <table width="100%"
               cellspacing="0"
               cellpadding="1"
               style="
                   font-family:Consolas,monospace;
                   font-size: 11px;
                   color:#b9cbd6;
                   border-collapse:collapse;
                   line-height:1.15;
               ">
          <tr>
            <td colspan="2"
                align="center"
                style="
                    color:#d7e2ea;
                    border-bottom:1px solid #607784;
                    padding:5px;
                ">
                FSOC PAT TELEMETRY
            </td>
          </tr>
          <tr>
            <td colspan="2"
                style="
                    color:#54f5d0;
                    padding-top:5px;
                ">
                TARGET
            </td>
          </tr>
          <tr>
            <td>Optical Beacon</td>
            <td align="right"></td>
          </tr>
          <tr>
            <td>STATE</td>
            <td align="right">
                ● {self.search_state}
            </td>
          </tr>
          <tr>
            <td colspan="2"
                style="
                    border-top:1px solid #607784;
                    color:#54f5d0;
                    padding-top:5px;
                ">
                DETECTION / TRACKING
            </td>
          </tr>
          <tr>
            <td>Confidence</td>
            <td align="right">
                {display_confidence:.2f}
            </td>
          </tr>
          <tr>
            <td>Source</td>
            <td align="right">
                {display_source}
            </td>
          </tr>
          <tr>
            <td>{distance_label}</td>
            <td align="right">
                {dist_text}
            </td>
          </tr>
          <tr><td>Acquisition</td><td align="right">{acquisition_text}</td></tr>
          <tr><td>Reacquisition</td><td align="right">{reacquisition_text}</td></tr>
          <tr>
            <td colspan="2"
                style="
                    border-top:1px solid #607784;
                    color:#54f5d0;
                    padding-top:5px;
                ">
                BORESIGHT ERROR
            </td>
          </tr>
          <tr>
            <td>ΔX</td>
            <td align="right">
                {dx:+.1f} px
            </td>
          </tr>
          <tr>
            <td>ΔY</td>
            <td align="right">
                {dy:+.1f} px
            </td>
          </tr>
          <tr>
            <td>Total</td>
            <td align="right">
                {error_text}
            </td>
          </tr>
          <tr>
            <td colspan="2"
                style="
                    border-top:1px solid #607784;
                    color:#54f5d0;
                    padding-top:5px;
                ">
                {"PTZ BYPASSED" if ptz_bypassed else "VIRTUAL PTZ"}
            </td>
          </tr>
          <tr>
            <td>{pan_label}</td>
            <td align="right">
                {pan_text}
            </td>
          </tr>
          <tr>
            <td>{tilt_label}</td>
            <td align="right">
                {tilt_text}
            </td>
          </tr>
          <tr>
            <td colspan="2"
                style="
                    border-top:1px solid #607784;
                    color:#54f5d0;
                    padding-top:5px;
                ">
                ALIGNMENT LOOP
            </td>
          </tr>

          <tr>
            <td colspan="2">
                {loop_text}
            </td>
          </tr>
          <tr>
            <td>Boresight</td>
            <td align="right">
                CENTER
            </td>
          </tr>

          <tr>
            <td>Tracking</td>
            <td align="right">
                {"ACTIVE" if self.search_state == "LOCKED" else "SEARCHING" if ptz_bypassed else "ACTIVE"}
            </td>
          </tr>

        </table>
        """

        self.pat_panel.setTextFormat(Qt.RichText)

        self.pat_panel.setText(panel_html)

    def _start_loop(self):
        self._next_deadline = time.perf_counter()
        self.timer.start(0)

    def _tick(self):
        """Run one frame, then re-arm for the next 1 / rate deadline."""
        if not self.running:
            return
        self.update_frame()
        if not self.running:
            return
        period = 1.0 / self.loop_rate_hz
        now = time.perf_counter()
        self._next_deadline = (self._next_deadline or now) + period
        if self._next_deadline < now - period:
            # More than a frame behind (e.g. a slow frame): resynchronise
            # rather than bursting to catch up.
            self._next_deadline = now
        self.timer.start(max(0, int((self._next_deadline - now) * 1000)))

    def update_frame(self):

        if not self.running:
            return
        frame_started = time.perf_counter()

        if self.media_path:
            ok, frame = self.media_capture.read() if self.media_capture is not None else (False, None)
            if not ok:
                self.timer.stop()
                self.running = False
                if self.media_capture is not None:
                    self.media_capture.release()
                    self.media_capture = None
                self._finish_video()
                return
            self._process_uploaded_frame(frame, frame_started)
            return

        self._scenario_step(frame_started)

    def closeEvent(self, event):
        if self.media_capture is not None:
            self.media_capture.release()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = Dashboard()
    win.show()
    sys.exit(app.exec_())
