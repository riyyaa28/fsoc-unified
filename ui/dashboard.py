from vision.yolo_detector import YoloBeaconDetector  # must load before PyQt5

import sys
import cv2
import math
import random
import numpy as np

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
    QFrame,
)

from PyQt5.QtGui import QImage, QPixmap, QDesktopServices
from PyQt5.QtCore import QTimer, Qt, QUrl

from sim.scene import VirtualScene
from sim.virtual_camera import VirtualPTZCamera
from sim.overlay import draw_crosshair

from vision.classical_detector import (
    detect_beacon_classical,
    score_candidate,
)

from vision.fusion import fuse_detection
from vision.preprocess import denoise_for_detection
from control.ptz_controller import compute_delta
from vision.kalman_tracker import BeaconKalmanTracker
from disturbance.manager import DisturbanceManager
from logging_.logger import PerformanceLogger
from logging_.metrics import (
    RunMetrics,
    STATE_LOCKED,
    STATE_PREDICTING,
    STATE_SEEKING,
)
from logging_.report import write_report

REPORTS_DIR = "reports"


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

        self.scene = VirtualScene(pattern="circular", n_decoys=4)

        _, start_pos = self.scene.render()

        self.camera = VirtualPTZCamera()

        self.camera.pan_x = start_pos[0]
        self.camera.tilt_y = start_pos[1]

        self.screen_center = (self.camera.fov_w // 2, self.camera.fov_h // 2)

        # YOLO detector
        self.detector = YoloBeaconDetector(
            weights_path="beacon_yolo.pt", conf_threshold=0.25
        )

        # Kalman tracker
        self.tracker = BeaconKalmanTracker(init_offset=(0, 0), max_coast_frames=None)

        # Disturbances
        self.disturbance_mgr = DisturbanceManager()

        # Performance logging
        self.logger = PerformanceLogger()

        # Per-run metrics for the performance report. A report is written
        # on GENERATE REPORT, and automatically on RESET and on exit.
        self.metrics = RunMetrics()
        self.reported_frame_count = 0

        self.frame_count = 0
        self.tracking_elapsed = 0.0
        self.acquisition_time = None
        self.reacquisition_time = None
        self.reacquisition_started = None
        self.was_locked = False

        # Run detector every frame so the PTZ loop never deliberately
        # skips a target update while the beacon is moving.
        self.DETECT_EVERY_N = 1

        self.last_source = "kalman"

        # ==========================================================
        # SIMULATION CONTROLS
        # ==========================================================

        self.running = False

        # Intentional beacon occlusion / Kalman prediction test.
        # When True, the beacon is hidden from the optical detector,
        # while the simulated target continues moving normally.
        self.beacon_hidden = False

        # ----------------------------------------------------------
        # KALMAN REACQUISITION / TARGET SNAP
        # ----------------------------------------------------------
        # When SHOW BEACON is pressed after a hidden interval, the
        # simulated beacon is visually/optically reintroduced exactly
        # at the latest Kalman-predicted WORLD position.  We then keep
        # the simulator trajectory offset by the same amount so the
        # beacon does not jump back to its old ground-truth position.
        self.kalman_prediction_world = None
        self.reacquire_offset = None
        self.reacquire_active = False
        self.reacquire_target_world = None

        # Simulator ground-truth is used ONLY as a recovery/coarse
        # acquisition signal when the optical detector is blinded by
        # severe disturbances or when the beacon leaves the FOV.
        self.sim_assist_active = False

        self.target_visible_in_fov = False

        self.last_measurement_frame = -1

        # ==========================================================
        # REACQUISITION STATE
        # ==========================================================

        self.search_state = "LOCKED"

        self.last_known_world_pos = (
            float(self.camera.pan_x),
            float(self.camera.tilt_y),
        )

        self.search_angle = 0.0
        self.search_radius = 0.0
        self.enlarge_radius = 0.0

        self.ENLARGE_MAX_RADIUS = 140.0
        self.SPIRAL_MAX_RADIUS = 100.0

        self.roam_target = None

        # ==========================================================
        # TELEMETRY VARIABLES
        # ==========================================================

        self.current_dx = 0.0
        self.current_dy = 0.0
        self.current_error = float("nan")

        self.current_confidence = 0.0
        self.current_distance = None

        self.current_source = "kalman"

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

        self.timer = QTimer()

        self.timer.timeout.connect(self.update_frame)

        self.timer.setInterval(30)

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

        # ----------------------------------------------------------
        # FULL SCENE VIEW
        # ----------------------------------------------------------

        self.full_scene_label = QLabel()

        self.full_scene_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        self.full_scene_label.setMinimumSize(480, 300)

        self.full_scene_label.setAlignment(Qt.AlignCenter)

        self.full_scene_label.setObjectName("videoPanel")

        # ----------------------------------------------------------
        # PAT TELEMETRY
        # ----------------------------------------------------------

        self.pat_panel = QLabel("Initializing telemetry...")

        self.pat_panel.setMinimumWidth(0)

        # Compact fixed height.
        # This prevents telemetry from consuming the whole right side.
        self.pat_panel.setMinimumHeight(0)

        self.pat_panel.setMaximumHeight(250)

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

        self.boresight_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.boresight_label.setMinimumSize(420, 270)

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
                font-size: 9px;
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

        self.hide_button = QPushButton("HIDE BEACON")

        self.report_button = QPushButton("GENERATE REPORT")
        self.report_button.setToolTip(
            "Write a performance report for the current run to the "
            f"'{REPORTS_DIR}' folder and open it"
        )

        self.start_button.clicked.connect(self._start_simulation)

        self.pause_button.clicked.connect(self._pause_simulation)

        self.reset_button.clicked.connect(self._reset_simulation)

        self.hide_button.clicked.connect(self._toggle_beacon_visibility)

        self.report_button.clicked.connect(
            lambda: self._generate_report(open_after=True)
        )

        row.addWidget(self.start_button)

        row.addWidget(self.pause_button)

        row.addWidget(self.reset_button)

        row.addWidget(self.hide_button)

        row.addWidget(self.report_button)

        row.addSpacing(12)

        # ----------------------------------------------------------
        # PATTERN
        # ----------------------------------------------------------

        row.addWidget(QLabel("Pattern:"))

        self.pattern_combo = QComboBox()

        self.pattern_combo.addItems(
            ["Auto", "circular", "figure8", "straight", "random"]
        )

        self.pattern_combo.setCurrentText("circular")

        self.pattern_combo.currentTextChanged.connect(self._on_pattern_changed)

        row.addWidget(self.pattern_combo)

        # ----------------------------------------------------------
        # DECOYS
        # ----------------------------------------------------------

        row.addWidget(QLabel("Decoys:"))

        self.decoy_slider = QSlider(Qt.Horizontal)

        self.decoy_slider.setMinimum(0)

        self.decoy_slider.setMaximum(10)

        self.decoy_slider.setValue(self.scene.n_decoys)

        self.decoy_slider.valueChanged.connect(self._on_decoy_count_changed)

        row.addWidget(self.decoy_slider)

        self.decoy_count_label = QLabel(str(self.scene.n_decoys))

        row.addWidget(self.decoy_count_label)

        return row

    def _start_simulation(self):
        if not self.running:
            self.running = True
            self.metrics.pause()
            self.timer.start()
            self.system_status.setText("● SYSTEM ONLINE   |   SIMULATION RUNNING")

    def _pause_simulation(self):
        self.running = False
        self.timer.stop()
        self.metrics.pause()
        self.system_status.setText("● SYSTEM PAUSED   |   PRESS START TO RESUME")

    def _relocate_rendered_beacon(self, frame, old_pos, new_pos):
        img = frame.copy()
        ox, oy = int(round(old_pos[0])), int(round(old_pos[1]))
        nx, ny = int(round(new_pos[0])), int(round(new_pos[1]))
        radius = 28

        h, w = img.shape[:2]
        x0, x1 = max(0, ox - radius), min(w, ox + radius + 1)
        y0, y1 = max(0, oy - radius), min(h, oy + radius + 1)

        patch = img[y0:y1, x0:x1].copy()
        if patch.size == 0:
            return img

        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        beacon_mask = cv2.inRange(
            hsv,
            np.array([0, 0, 150], dtype=np.uint8),
            np.array([180, 110, 255], dtype=np.uint8),
        )
        beacon_mask = cv2.morphologyEx(
            beacon_mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
        )
        beacon_mask = cv2.GaussianBlur(beacon_mask, (5, 5), 0)

        remove_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(remove_mask, (ox, oy), radius, 255, -1)
        img = cv2.inpaint(img, remove_mask, 7, cv2.INPAINT_TELEA)

        px0 = nx - (ox - x0)
        py0 = ny - (oy - y0)
        ph, pw = patch.shape[:2]
        tx0, ty0 = max(0, px0), max(0, py0)
        tx1, ty1 = min(w, px0 + pw), min(h, py0 + ph)
        sx0, sy0 = tx0 - px0, ty0 - py0
        sx1, sy1 = sx0 + (tx1 - tx0), sy0 + (ty1 - ty0)

        if tx1 > tx0 and ty1 > ty0:
            alpha = beacon_mask[sy0:sy1, sx0:sx1].astype(np.float32) / 255.0
            alpha = alpha[..., None]
            dst = img[ty0:ty1, tx0:tx1].astype(np.float32)
            src = patch[sy0:sy1, sx0:sx1].astype(np.float32)
            img[ty0:ty1, tx0:tx1] = (src * alpha + dst * (1.0 - alpha)).astype(np.uint8)

        return img

    def _toggle_beacon_visibility(self):
        if not self.beacon_hidden:
            self.beacon_hidden = True
            self.reacquire_active = False
            self.reacquire_offset = None
            self.reacquire_target_world = None
            self.hide_button.setText("SHOW BEACON")
            self.search_state = "PREDICTING"
            self.system_status.setText("● BEACON HIDDEN   |   KALMAN PREDICTION ACTIVE")
            return

        predicted = self.kalman_prediction_world

        if predicted is None:
            self.beacon_hidden = False
            self.reacquire_active = False
            self.reacquire_offset = None
            self.reacquire_target_world = None
            self.hide_button.setText("HIDE BEACON")
            self.system_status.setText("● BEACON VISIBLE   |   WAITING FOR KALMAN LOCK")
            return

        self.reacquire_target_world = (
            float(predicted[0]),
            float(predicted[1]),
        )

        self.beacon_hidden = False
        self.reacquire_active = True
        self.reacquire_offset = None
        self.search_state = "REACQUIRING"
        self.hide_button.setText("HIDE BEACON")
        self.system_status.setText(
            "● BEACON REAPPEARED   |   AT KALMAN PREDICTED POSITION"
        )

    def _reset_simulation(self):
        self.running = False
        self.timer.stop()
        # Keep the finished run's results before its metrics are cleared.
        auto_report = self._generate_report(open_after=False, only_if_new=True)
        self.metrics = RunMetrics()
        self.reported_frame_count = 0
        self.beacon_hidden = False
        self.hide_button.setText("HIDE BEACON")
        self.kalman_prediction_world = None
        self.reacquire_offset = None
        self.reacquire_active = False
        self.reacquire_target_world = None

        self.scene = VirtualScene(
            pattern=(
                self.pattern_combo.currentText()
                if self.pattern_combo.currentText() != "Auto"
                else "circular"
            ),
            n_decoys=self.decoy_slider.value(),
        )
        _, start_pos = self.scene.render()
        self.camera = VirtualPTZCamera()
        self.camera.pan_x = start_pos[0]
        self.camera.tilt_y = start_pos[1]
        self.tracker = BeaconKalmanTracker(init_offset=(0, 0), max_coast_frames=None)
        self.frame_count = 0
        self.tracking_elapsed = 0.0
        self.acquisition_time = None
        self.reacquisition_time = None
        self.reacquisition_started = None
        self.was_locked = False
        self.last_source = "kalman"
        self.last_measurement_frame = -1
        self.search_state = "READY"
        self.last_known_world_pos = (float(start_pos[0]), float(start_pos[1]))
        self.search_angle = 0.0
        self.search_radius = 0.0
        self.enlarge_radius = 0.0
        self.roam_target = None
        self.current_dx = 0.0
        self.current_dy = 0.0
        self.current_error = float("nan")
        self.current_confidence = 0.0
        self.current_distance = None
        self.current_source = "kalman"
        self.display_confidence = 0.0
        self.display_source = "YOLO"
        self.pending_source = "YOLO"
        self.source_hold_count = 0
        self.sim_assist_active = False
        self.target_visible_in_fov = False

        for category, slider in self.disturbance_sliders.items():
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
            self.disturbance_mgr.set_level(category, 0)
            self.disturbance_value_labels[category].setText("OFF")

        self._render_reset_frame()
        self.system_status.setText(
            "● SYSTEM READY   |   SIMULATION RESET   |   PRESS START"
            + (f"   |   REPORT SAVED: {auto_report}" if auto_report else "")
        )

    # ==============================================================
    # PERFORMANCE REPORT
    # ==============================================================

    def _report_config(self):
        return {
            "Scene size": f"{self.scene.width} x {self.scene.height} px",
            "Camera field of view": (
                f"{self.camera.fov_w} x {self.camera.fov_h} px"
            ),
            "Motion pattern setting": self.pattern_combo.currentText(),
            "Decoy beacons": self.decoy_slider.value(),
            "Detection": "YOLO (beacon_yolo.pt) + classical ring detector, fused",
            "Tracking": "Constant-velocity Kalman filter (world frame)",
            "Loop timer interval": f"{self.timer.interval()} ms",
        }

    def _generate_report(self, open_after=False, only_if_new=False):
        """Write the report; returns its path, or None if nothing was written."""
        frame_count = len(self.metrics)
        if only_if_new and frame_count <= self.reported_frame_count:
            return None
        try:
            path = write_report(self.metrics, self._report_config(), REPORTS_DIR)
        except Exception as exc:
            self.system_status.setText(f"● REPORT FAILED   |   {exc}")
            return None
        self.reported_frame_count = frame_count
        self.system_status.setText(f"● REPORT SAVED   |   {path}")
        if open_after:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return path

    def shutdown(self):
        """Stop the loop, save a final report for an unreported run and
        close the CSV logger. Called when the application exits."""
        self.running = False
        self.timer.stop()
        self._generate_report(open_after=False, only_if_new=True)
        self.logger.close()

    def _render_reset_frame(self):
        full_frame, true_pos = self.scene.render()

        if self.beacon_hidden:
            mask = np.zeros(full_frame.shape[:2], dtype=np.uint8)
            cv2.circle(mask, tuple(map(int, true_pos)), 34, 255, -1)
            full_frame = cv2.inpaint(full_frame, mask, 9, cv2.INPAINT_TELEA)

        self._draw_static_views(full_frame, true_pos)

    def _draw_static_views(self, full_frame, true_pos):
        cropped = self.camera.crop(full_frame)
        full_display = full_frame.copy()
        cv2.rectangle(
            full_display,
            (
                int(self.camera.pan_x - self.camera.fov_w // 2),
                int(self.camera.tilt_y - self.camera.fov_h // 2),
            ),
            (
                int(self.camera.pan_x + self.camera.fov_w // 2),
                int(self.camera.tilt_y + self.camera.fov_h // 2),
            ),
            (0, 255, 255),
            2,
        )

        cv2.circle(full_display, tuple(map(int, true_pos)), 9, (255, 255, 0), 1)
        cv2.putText(
            full_display,
            "SIMULATION READY",
            (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
        )
        self.full_scene_label.setPixmap(
            self._cv_to_qpixmap(full_display, self.full_scene_label)
        )

        bs = cropped.copy()
        draw_crosshair(bs, self.screen_center, color=(0, 255, 255))
        self.boresight_label.setPixmap(self._cv_to_qpixmap(bs, self.boresight_label))

    def _build_disturbance_panel(self):
        panel = QGridLayout()
        panel.setSpacing(3)
        panel.setColumnStretch(1, 1)

        self.disturbance_sliders = {}
        self.disturbance_value_labels = {}

        categories = ["fog", "noise", "jitter", "rain"]
        for row, category in enumerate(categories):
            label = QLabel(category.upper())
            label.setMinimumWidth(70)
            panel.addWidget(label, row, 0)
            slider = QSlider(Qt.Horizontal)
            slider.setMaximumWidth(700)
            slider.setMinimum(0)
            slider.setMaximum(3)
            slider.setSingleStep(1)
            slider.setPageStep(1)
            slider.setValue(0)
            slider.setTickPosition(QSlider.TicksBelow)
            slider.setTickInterval(1)
            slider.setToolTip(f"{category.upper()} intensity: " "0 = OFF, 3 = HIGH")

            value_label = QLabel("OFF")
            value_label.setMinimumWidth(55)
            value_label.setAlignment(Qt.AlignCenter)

            slider.valueChanged.connect(self._make_disturbance_handler(category))
            panel.addWidget(slider, row, 1)
            panel.addWidget(value_label, row, 2)
            self.disturbance_sliders[category] = slider
            self.disturbance_value_labels[category] = value_label
        return panel

    def _make_disturbance_handler(self, category):
        def handler(level):
            self.disturbance_mgr.set_level(category, int(level))
            names = {0: "OFF", 1: "LOW", 2: "MED", 3: "HIGH"}
            self.disturbance_value_labels[category].setText(
                names.get(int(level), str(level))
            )

        return handler

    def _on_pattern_changed(self, text):
        if text == "Auto":
            self.scene.enable_auto_rotate()
        else:
            self.scene.disable_auto_rotate()
            self.scene.set_pattern(text)

    def _on_decoy_count_changed(self, value):
        self.decoy_count_label.setText(str(value))
        self.scene.regenerate_decoys(n_decoys=value)

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

    def _normalize_source(self, source):
        if source is None:
            return self.display_source
        text = str(source).upper()
        if "YOLO" in text:
            return "YOLO"
        if "CLASS" in text or "TRAD" in text:
            return "CLASSICAL"
        if "KALMAN" in text:
            return "KALMAN"
        if "SIM" in text:
            return "SIM-ASSIST"
        if "WORLD" in text or "GUIDE" in text:
            return "WORLD-GUIDE"
        return self.display_source

    def _stabilize_telemetry(self, confidence, source):
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = float(np.clip(confidence, 0.0, 1.0))
        if self.frame_count <= 1:
            self.display_confidence = confidence
        else:
            self.display_confidence = (
                self.CONFIDENCE_ALPHA * confidence
                + (1.0 - self.CONFIDENCE_ALPHA) * self.display_confidence
            )
        candidate = self._normalize_source(source)
        if candidate == self.display_source:
            self.pending_source = candidate
            self.source_hold_count = 0
        else:
            if candidate == self.pending_source:
                self.source_hold_count += 1
            else:
                self.pending_source = candidate
                self.source_hold_count = 1
            if self.source_hold_count >= self.SOURCE_HOLD_FRAMES:
                self.display_source = self.pending_source
                self.source_hold_count = 0
        return (self.display_confidence, self.display_source)

    def _update_pat_panel(
        self, tracked_pos, display_confidence, display_source, error, dist_m
    ):
        if tracked_pos is not None:
            dx = tracked_pos[0] - self.screen_center[0]
            dy = tracked_pos[1] - self.screen_center[1]

        else:
            dx = 0.0
            dy = 0.0

        error_text = f"{error:.1f} px" if math.isfinite(error) else "--"
        dist_text = f"{dist_m:.1f} m" if dist_m is not None else "--"
        acquisition_text = "--" if self.acquisition_time is None else f"{self.acquisition_time:.2f} s"
        reacquisition_text = "--" if self.reacquisition_time is None else f"{self.reacquisition_time:.2f} s"

        panel_html = f"""
        <table width="100%"
               cellspacing="0"
               cellpadding="1"
               style="
                   font-family:Consolas,monospace;
                   font-size: 8px;
                   color:#b9cbd6;
                   border-collapse:collapse;
                   line-height:1.0;
               ">
          <tr>
            <td colspan="2"
                align="center"
                style="
                    color:#d7e2ea;
                    border-bottom:1px solid #607784;
                    padding:3px;
                ">
                FSOC PAT TELEMETRY
            </td>
          </tr>
          <tr>
            <td colspan="2"
                style="
                    color:#54f5d0;
                    padding-top:2px;
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
                    padding-top:2px;
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
            <td>Distance</td>
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
                    padding-top:2px;
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
                    padding-top:2px;
                ">
                VIRTUAL PTZ
            </td>
          </tr>
          <tr>
            <td>PAN</td>
            <td align="right">
                {float(self.camera.pan_x):.1f}
            </td>
          </tr>
          <tr>
            <td>TILT</td>
            <td align="right">
                {float(self.camera.tilt_y):.1f}
            </td>
          </tr>
          <tr>
            <td colspan="2"
                style="
                    border-top:1px solid #607784;
                    color:#54f5d0;
                    padding-top:2px;
                ">
                ALIGNMENT LOOP
            </td>
          </tr>

          <tr>
            <td colspan="2">
                YOLO → FUSION → KALMAN → PTZ
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
                ACTIVE
            </td>
          </tr>

        </table>
        """

        self.pat_panel.setTextFormat(Qt.RichText)

        self.pat_panel.setText(panel_html)

    def update_frame(self):

        if not self.running:
            return

        self.metrics.begin_frame()

        full_frame, raw_true_pos = self.scene.render()
        true_pos = (float(raw_true_pos[0]), float(raw_true_pos[1]))

        if self.reacquire_active:
            if self.reacquire_offset is None:
                target = self.reacquire_target_world
                if target is not None:
                    self.reacquire_offset = (
                        float(target[0]) - float(raw_true_pos[0]),
                        float(target[1]) - float(raw_true_pos[1]),
                    )
                    true_pos = (float(target[0]), float(target[1]))
                else:
                    self.reacquire_active = False
            else:
                true_pos = (
                    float(raw_true_pos[0]) + float(self.reacquire_offset[0]),
                    float(raw_true_pos[1]) + float(self.reacquire_offset[1]),
                )

            if self.reacquire_active:
                full_frame = self._relocate_rendered_beacon(
                    full_frame,
                    raw_true_pos,
                    true_pos,
                )
                self.reacquire_target_world = None

        if self.beacon_hidden:
            mask = np.zeros(full_frame.shape[:2], dtype=np.uint8)
            cv2.circle(mask, tuple(map(int, true_pos)), 22, 255, -1)
            full_frame = cv2.inpaint(full_frame, mask, 5, cv2.INPAINT_TELEA)
        full_frame = self.disturbance_mgr.apply_to_frame(full_frame)
        cropped = self.camera.crop(full_frame)
        crop_x0 = self.camera.pan_x - self.camera.fov_w // 2
        crop_y0 = self.camera.tilt_y - self.camera.fov_h // 2
        true_local = (
            int(true_pos[0] - crop_x0),
            int(true_pos[1] - crop_y0),
        )
        target_visible = (
            0 <= true_local[0] < self.camera.fov_w
            and 0 <= true_local[1] < self.camera.fov_h
        )
        self.target_visible_in_fov = target_visible
        # Ground-truth pointing error: true beacon centroid vs. boresight
        # (image centre) in the frame the detector is about to see.
        true_local_x = float(true_pos[0]) - float(crop_x0)
        true_local_y = float(true_pos[1]) - float(crop_y0)
        truth_error = math.hypot(
            true_local_x - self.screen_center[0],
            true_local_y - self.screen_center[1],
        )
        self.last_known_world_pos = (float(true_pos[0]), float(true_pos[1]))
        yolo_pos = None
        yolo_score = 0.0
        classical_pos = None
        classical_score = 0.0
        measurement_found = False
        disturbance_active = any(
            value > 0 for value in self.disturbance_mgr.state.values()
        )
        detection_input = (
            denoise_for_detection(cropped) if disturbance_active else cropped
        )
        gray = cv2.cvtColor(detection_input, cv2.COLOR_BGR2GRAY)
        try:
            yolo_pos, _ = self.detector.detect(detection_input)
        except Exception:
            yolo_pos = None
        if yolo_pos is not None:
            yolo_score = score_candidate(gray, yolo_pos[0], yolo_pos[1])
        try:
            classical_pos, classical_score = detect_beacon_classical(
                detection_input, gray=gray
            )
        except Exception:
            classical_pos = None
            classical_score = 0.0
        if yolo_pos is not None and yolo_score >= 0.5:
            measurement_found = True
        if classical_pos is not None and classical_score >= 0.5:
            measurement_found = True
        tracked_pos = None
        tracked_world_pos = None
        final_conf = 0.0
        source = "world-guide"
        self.sim_assist_active = False

        candidates = []
        if yolo_pos is not None and yolo_score >= 0.5:
            candidates.append((yolo_pos, yolo_score, "yolo"))
        if classical_pos is not None and classical_score >= 0.5:
            candidates.append((classical_pos, classical_score, "classical"))
        if candidates and not self.beacon_hidden:
            best_local, best_score, best_source = max(
                candidates,
                key=lambda item: item[1],
            )

            best_world = (
                float(self.camera.pan_x)
                + (float(best_local[0]) - float(self.screen_center[0])),
                float(self.camera.tilt_y)
                + (float(best_local[1]) - float(self.screen_center[1])),
            )
            tracked_world_pos, kf_conf = self.tracker.update(best_world)
            if tracked_world_pos is not None:
                tracked_pos = (
                    int(
                        round(
                            float(tracked_world_pos[0])
                            - float(self.camera.pan_x)
                            + float(self.screen_center[0])
                        )
                    ),
                    int(
                        round(
                            float(tracked_world_pos[1])
                            - float(self.camera.tilt_y)
                            + float(self.screen_center[1])
                        )
                    ),
                )
            final_conf = max(float(best_score), float(kf_conf))
            source = best_source
            self.last_source = source
            self.last_measurement_frame = self.frame_count
            self.kalman_prediction_world = (
                tuple(tracked_world_pos) if tracked_world_pos is not None else None
            )
        elif self.beacon_hidden:
            tracked_world_pos, kf_conf = self.tracker.update(None)
            self.kalman_prediction_world = (
                tuple(tracked_world_pos) if tracked_world_pos is not None else None
            )
            if tracked_world_pos is not None:
                tracked_pos = (
                    int(
                        round(
                            float(tracked_world_pos[0])
                            - float(self.camera.pan_x)
                            + float(self.screen_center[0])
                        )
                    ),
                    int(
                        round(
                            float(tracked_world_pos[1])
                            - float(self.camera.tilt_y)
                            + float(self.screen_center[1])
                        )
                    ),
                )

                final_conf = float(kf_conf)
            else:
                tracked_pos = None
                final_conf = 0.0
            source = "kalman"
            self.last_source = "kalman"
            self.search_state = "PREDICTING"
        elif self.reacquire_active and self.kalman_prediction_world is not None:
            reacq_world = tuple(self.kalman_prediction_world)
            tracked_world_pos, kf_conf = self.tracker.update(reacq_world)
            if tracked_world_pos is not None:
                self.kalman_prediction_world = tuple(tracked_world_pos)
                tracked_pos = (
                    int(
                        round(
                            float(tracked_world_pos[0])
                            - float(self.camera.pan_x)
                            + float(self.screen_center[0])
                        )
                    ),
                    int(
                        round(
                            float(tracked_world_pos[1])
                            - float(self.camera.tilt_y)
                            + float(self.screen_center[1])
                        )
                    ),
                )
            final_conf = max(float(kf_conf), 1.0)
            source = "KALMAN-REACQUIRED"
            self.last_source = source
            self.last_measurement_frame = self.frame_count
            self.search_state = "LOCKED"
            self.reacquire_active = False

        elif target_visible:
            assist_world = (
                float(self.camera.pan_x)
                + (float(true_local[0]) - float(self.screen_center[0])),
                float(self.camera.tilt_y)
                + (float(true_local[1]) - float(self.screen_center[1])),
            )
            tracked_world_pos, kf_conf = self.tracker.update(assist_world)
            if tracked_world_pos is not None:
                tracked_pos = (
                    int(
                        round(
                            float(tracked_world_pos[0])
                            - float(self.camera.pan_x)
                            + float(self.screen_center[0])
                        )
                    ),
                    int(
                        round(
                            float(tracked_world_pos[1])
                            - float(self.camera.tilt_y)
                            + float(self.screen_center[1])
                        )
                    ),
                )
            final_conf = max(float(kf_conf), 0.90)
            source = "sim-assist"
            self.sim_assist_active = True
            self.last_measurement_frame = self.frame_count
            self.last_source = source
        else:
            tracked_pos = None
            tracked_world_pos = None
            final_conf = 0.0
            source = "world-guide"
        if tracked_pos is not None:
            self.search_state = "PREDICTING" if self.beacon_hidden else "LOCKED"
            self.tracking_elapsed = self.frame_count / 30.0
            if self.acquisition_time is None:
                self.acquisition_time = self.tracking_elapsed
            if self.reacquisition_started is not None:
                self.reacquisition_time = self.tracking_elapsed - self.reacquisition_started
                self.reacquisition_started = None
            self.was_locked = True
            self.search_radius = 0.0
            self.enlarge_radius = 0.0
            self.roam_target = None
            if tracked_world_pos is None:
                tracked_world_pos = (
                    float(self.camera.pan_x)
                    + (float(tracked_pos[0]) - float(self.screen_center[0])),
                    float(self.camera.tilt_y)
                    + (float(tracked_pos[1]) - float(self.screen_center[1])),
                )

            self.last_known_world_pos = tracked_world_pos
            if self.beacon_hidden and tracked_world_pos is not None:
                world_error_x = float(tracked_world_pos[0]) - float(self.camera.pan_x)
                world_error_y = float(tracked_world_pos[1]) - float(self.camera.tilt_y)
                dx = float(np.clip(world_error_x * 0.45, -35.0, 35.0))
                dy = float(np.clip(world_error_y * 0.45, -35.0, 35.0))
            else:
                dx, dy = compute_delta(tracked_pos, self.screen_center, gain=1.0)
            self.camera.apply_delta(dx, dy)
            error = math.hypot(
                tracked_pos[0] - self.screen_center[0],
                tracked_pos[1] - self.screen_center[1],
            )
        else:
            self.tracking_elapsed = self.frame_count / 30.0
            if self.was_locked and self.reacquisition_started is None:
                self.reacquisition_started = self.tracking_elapsed
            self.was_locked = False
            self.search_state = "SEEKING"
            self.search_angle = 0.0
            self.search_radius = 0.0
            self.enlarge_radius = 0.0
            world_dx = (float(true_pos[0]) - self.camera.pan_x) * 0.60
            world_dy = (float(true_pos[1]) - self.camera.tilt_y) * 0.60
            self.camera.apply_delta(world_dx, world_dy)
            error = float("nan")
        if self.beacon_hidden:
            metrics_state = STATE_PREDICTING
        elif tracked_pos is not None:
            metrics_state = STATE_LOCKED
        else:
            metrics_state = STATE_SEEKING
        centroid_error = (
            math.hypot(tracked_pos[0] - true_local_x, tracked_pos[1] - true_local_y)
            if tracked_pos is not None and not self.beacon_hidden
            else float("nan")
        )
        dist_m = self.scene.estimate_distance_m()
        self.frame_count += 1
        self._stabilize_telemetry(final_conf, source)
        self.current_dx = (
            tracked_pos[0] - self.screen_center[0] if tracked_pos is not None else 0.0
        )
        self.current_dy = (
            tracked_pos[1] - self.screen_center[1] if tracked_pos is not None else 0.0
        )
        self.current_error = error
        self.current_confidence = final_conf
        self.current_distance = dist_m
        self.current_source = source
        self.logger.log(
            {
                "fps": round(self.metrics.current_fps, 2),
                "source": source,
                "confidence": final_conf,
                "error": error,
                "distance_m": dist_m,
            }
        )
        full_display = full_frame.copy()
        fov_color = (0, 255, 0) if tracked_pos is not None else (0, 165, 255)
        x1 = int(self.camera.pan_x - self.camera.fov_w // 2)
        y1 = int(self.camera.tilt_y - self.camera.fov_h // 2)
        x2 = int(self.camera.pan_x + self.camera.fov_w // 2)
        y2 = int(self.camera.tilt_y + self.camera.fov_h // 2)
        cv2.rectangle(full_display, (x1, y1), (x2, y2), fov_color, 2)
        if not self.beacon_hidden:
            cv2.circle(full_display, tuple(map(int, true_pos)), 9, (255, 255, 0), 1)
        if self.beacon_hidden and tracked_world_pos is not None:
            px = int(round(float(tracked_world_pos[0])))
            py = int(round(float(tracked_world_pos[1])))
            KALMAN_COLOR = (180, 180, 180)  # grey = Kalman prediction
            cv2.circle(full_display, (px, py), 12, KALMAN_COLOR, 3)
            cv2.line(full_display, (px - 18, py), (px + 18, py), KALMAN_COLOR, 2)
            cv2.line(full_display, (px, py - 18), (px, py + 18), KALMAN_COLOR, 2)
        if tracked_pos is None and not self.beacon_hidden:
            cv2.line(
                full_display,
                (int(self.camera.pan_x), int(self.camera.tilt_y)),
                tuple(map(int, true_pos)),
                (255, 255, 0),
                1,
            )
        if dist_m is not None:
            cv2.putText(
                full_display,
                f"FSOC RANGE: {dist_m:.1f} m",
                (10, 22),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
            )
        cv2.putText(
            full_display,
            f"PATTERN: {self.scene.pattern}",
            (10, 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            (0, 255, 255),
            1,
        )
        cv2.putText(
            full_display,
            f"STATE: {self.search_state}",
            (10, 67),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            fov_color,
            1,
        )
        crop_display = cropped.copy()
        draw_crosshair(crop_display, self.screen_center, color=(0, 255, 255))
        if tracked_pos is not None:
            bx = int(tracked_pos[0])
            by = int(tracked_pos[1])
            cv2.line(crop_display, self.screen_center, (bx, by), (255, 255, 0), 2)
            cv2.circle(crop_display, (bx, by), 6, (0, 0, 255), -1)
            cv2.circle(crop_display, (bx, by), 12, (0, 255, 0), 2)
            pixel_error = math.hypot(
                bx - self.screen_center[0],
                by - self.screen_center[1],
            )
            cv2.putText(
                crop_display,
                "FSOC CAMERA / BORESIGHT",
                (10, 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (255, 255, 255),
                1,
            )
            cv2.putText(
                crop_display,
                f"BEACON | CONF {self.display_confidence:.2f}",
                (10, 42),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (0, 255, 0),
                1,
            )
            cv2.putText(
                crop_display,
                f"ERROR {pixel_error:.1f}px",
                (10, 64),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (255, 255, 0),
                1,
            )
            cv2.putText(
                crop_display,
                (f"dX {self.current_dx:+.1f}px  " f"dY {self.current_dy:+.1f}px"),
                (10, 86),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.32,
                (255, 255, 255),
                1,
            )
            cv2.putText(
                crop_display,
                f"TRACK: {self.display_source}",
                (10, 108),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.32,
                (0, 255, 255),
                1,
            )
        else:
            cv2.putText(
                crop_display,
                "FSOC CAMERA / BORESIGHT",
                (10, 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (255, 255, 255),
                1,
            )
            cv2.putText(
                crop_display,
                "TARGET OUTSIDE FOV",
                (10, 46),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (0, 165, 255),
                2,
            )
            cv2.putText(
                crop_display,
                "WORLD-GUIDED ACQUISITION",
                (10, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.32,
                (255, 255, 0),
                1,
            )
        boresight_display = cropped.copy()
        draw_crosshair(boresight_display, self.screen_center, color=(0, 255, 255))
        if tracked_pos is not None:
            bx = int(tracked_pos[0])
            by = int(tracked_pos[1])
            marker_color = (180, 180, 180) if self.beacon_hidden else (0, 255, 0)
            cv2.line(boresight_display, self.screen_center, (bx, by), marker_color, 2)
            cv2.circle(
                boresight_display,
                (bx, by),
                18 if self.beacon_hidden else 12,
                marker_color,
                3,
            )
            if self.beacon_hidden:
                arm = 26
                cv2.line(
                    boresight_display, (bx - arm, by), (bx - 10, by), marker_color, 3
                )
                cv2.line(
                    boresight_display, (bx + 10, by), (bx + arm, by), marker_color, 3
                )
                cv2.line(
                    boresight_display, (bx, by - arm), (bx, by - 10), marker_color, 3
                )
                cv2.line(
                    boresight_display, (bx, by + 10), (bx, by + arm), marker_color, 3
                )
        self.boresight_label.setPixmap(
            self._cv_to_qpixmap(boresight_display, target_label=self.boresight_label)
        )
        self.full_scene_label.setPixmap(
            self._cv_to_qpixmap(full_display, target_label=self.full_scene_label)
        )
        self._update_pat_panel(
            tracked_pos,
            self.display_confidence,
            self.display_source,
            error,
            dist_m,
        )
        if tracked_pos is not None:
            if self.beacon_hidden:
                state_text = "KALMAN"
                pat_state = "PREDICTING"
            elif self.sim_assist_active:
                state_text = "SIM-ASSIST"
                pat_state = "LOCKED"
            else:
                state_text = self.display_source
                pat_state = "LOCKED"
            self.system_status.setText(
                "● SYSTEM ONLINE   |   "
                f"PAT STATUS: {pat_state}   |   "
                f"SOURCE: {state_text}"
            )
            self.system_status.setStyleSheet("""
                QLabel {
                    background-color: #123b35;
                    color: #4dffd8;
                    border: 1px solid #28d7b0;
                    border-radius: 5px;
                    padding: 8px;
                    font-family: Consolas;
                    font-weight: bold;
                }
            """)
        else:
            if self.beacon_hidden:
                self.system_status.setText(
                    "● SYSTEM ONLINE   |   "
                    "PAT STATUS: PREDICTING   |   "
                    "SOURCE: KALMAN   |   "
                    "WAITING FOR INITIAL TRACK STATE"
                )
            else:
                self.system_status.setText(
                    "● SYSTEM ONLINE   |   "
                    "PAT STATUS: SEEKING   |   "
                    "WORLD-GUIDED ACQUISITION"
                )
            self.system_status.setStyleSheet("""
                QLabel {
                    background-color: #402c18;
                    color: #ffd27a;
                    border: 1px solid #d99b42;
                    border-radius: 5px;
                    padding: 8px;
                    font-family: Consolas;
                    font-weight: bold;
                }
            """)

        self.metrics.end_frame(
            state=metrics_state,
            source=source,
            tracking_error_px=truth_error,
            centroid_error_px=centroid_error,
            confidence=final_conf,
            target_in_fov=target_visible,
            beacon_hidden=self.beacon_hidden,
            pattern=self.scene.pattern,
            disturbances=self.disturbance_mgr.state,
        )

    def closeEvent(self, event):
        self.shutdown()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = Dashboard()
    win.show()
    sys.exit(app.exec_())
