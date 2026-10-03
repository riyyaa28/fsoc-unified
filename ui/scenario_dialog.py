"""Scenario editor: every Benchmark-1 parameter, with save / load as JSON."""

from dataclasses import asdict

from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from sim.scenario import (
    ACQUISITION_AIDS, ATMOSPHERES, CAMERA_TYPES, PATTERNS, PLATFORM_MOTIONS, SHAPES, SKIES, ScenarioConfig,
)

# field -> (tab, label, widget kind, options, tooltip)
FIELDS = [
    ("name", "Run", "Scenario name", "text", None, ""),
    ("duration_s", "Run", "Duration (s)", "float", (0, 36000, 1, 1), "0 = run until PAUSE / RESET"),
    ("seed", "Run", "Random seed", "int", (0, 2**31 - 1), "0 = different every run; the seed used is saved with the logs"),
    ("occlusions", "Run", "Occlusions (s)", "text", None, "Beacon hidden during these intervals, e.g. 4-5, 10-11.5"),

    ("screen_width", "Camera", "Screen width (px)", "int", (800, 8000), "Spec minimum 2000"),
    ("screen_height", "Camera", "Screen height (px)", "int", (800, 8000), "Spec minimum 2000"),
    ("camera_width", "Camera", "Camera width (px)", "int", (160, 1920), "Spec 640"),
    ("camera_height", "Camera", "Camera height (px)", "int", (120, 1440), "Spec 480"),
    ("camera_type", "Camera", "Camera display", "choice", CAMERA_TYPES,
     "The tracker always uses the monochrome sensor image (spec: monochrome FPA); "
     "'colour' shows the camera view in colour"),
    ("sky", "Camera", "Sky / background", "choice", SKIES,
     "dusk = the original blue gradient; day; night; dark = plain monochrome sky at the background level"),
    ("fov_x_deg", "Camera", "FOV horizontal (deg)", "float", (0.1, 90, 0.1, 2), "Default 4"),
    ("fov_y_deg", "Camera", "FOV vertical (deg)", "float", (0.1, 90, 0.1, 2), "Default 3"),
    ("update_rate_hz", "Camera", "Update rate (Hz)", "float", (5, 120, 1, 1), "Spec minimum 30"),
    ("max_pan_deg_s", "Camera", "Max pan speed (deg/s)", "float", (0.1, 90, 0.5, 2), "Spec 5-10, default 5"),
    ("max_tilt_deg_s", "Camera", "Max tilt speed (deg/s)", "float", (0.1, 90, 0.5, 2), "Spec 5-10, default 5"),
    ("acquisition_aid", "Camera", "Acquisition", "choice", ACQUISITION_AIDS,
     "wide-field: a low-resolution finder sees the whole screen and cues the camera; "
     "scan: the narrow camera searches alone"),

    ("target_shape", "Target", "Shape", "choice", SHAPES,
     "ringed = the original beacon (bright core with halo rings); square is the spec default"),
    ("target_size_px", "Target", "Size (px)", "int", (3, 40), "Spec 5-20, default 10 (core diameter for ringed)"),
    ("target_level", "Target", "Beacon brightness (0-255)", "int", (1, 255), ""),
    ("background_level", "Target", "Background level (0-255)", "int", (0, 254), "Used by the 'dark' sky"),
    ("initial_target", "Target", "Initial location", "text", None, "'random' or x,y in screen px"),
    ("motion", "Target", "Motion", "choice", PATTERNS, "Spec: straight, circular, figure 8, random; optional spiral, sinusoidal"),
    ("auto_switch", "Target", "Switch motion automatically", "bool", None, "Change pattern every 5-9 s"),
    ("target_speed_px_s", "Target", "Speed (px/s)", "float", (0, 5000, 10, 1), ""),
    ("decoys", "Target", "Decoy targets", "int", (0, 20), "Additional dimmer spots (multiple targets)"),

    ("salt_pepper_pct", "Disturbances", "Salt & pepper (% pixels)", "float", (0, 50, 1, 1), "Spec around 10 %"),
    ("gaussian_sigma", "Disturbances", "Gaussian noise sigma", "float", (0, 100, 1, 1), "Spec max standard deviation 20"),
    ("poisson", "Disturbances", "Poisson noise", "bool", None, "Photon shot noise"),
    ("poisson_peak", "Disturbances", "Poisson photons at white", "float", (1, 10000, 5, 0), "Lower = noisier"),
    ("jitter_px", "Disturbances", "Camera jitter (+/- px/frame)", "int", (0, 50), "Spec max 20"),
    ("atmosphere", "Disturbances", "Atmosphere", "choice", ATMOSPHERES, "Clear, haze, fog, rain, low light"),
    ("atmosphere_strength", "Disturbances", "Atmosphere strength (0-1)", "float", (0, 1, 0.05, 2), ""),
    ("platform_motion", "Disturbances", "Platform motion", "choice", PLATFORM_MOTIONS, "Spec default: linear"),
    ("platform_px_per_frame", "Disturbances", "Platform motion (px/frame)", "float", (0, 50, 1, 1), "Spec max 20"),
]


class ScenarioDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scenario configuration")
        self.setMinimumWidth(520)
        self.widgets = {}
        tabs = QTabWidget()
        forms = {}
        for name in ("Camera", "Target", "Disturbances", "Run"):
            page = QWidget()
            forms[name] = QFormLayout(page)
            tabs.addTab(page, name.upper())
        for key, tab, label, kind, options, tip in FIELDS:
            widget = self._make_widget(kind, options)
            widget.setToolTip(tip)
            forms[tab].addRow(QLabel(label), widget)
            self.widgets[key] = (widget, kind)

        load_button = QPushButton("LOAD...")
        save_button = QPushButton("SAVE...")
        defaults_button = QPushButton("DEFAULTS")
        load_button.clicked.connect(self._load)
        save_button.clicked.connect(self._save)
        defaults_button.clicked.connect(lambda: self.set_config(ScenarioConfig()))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("APPLY && RESET")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        for button in (load_button, save_button, defaults_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(buttons)

        note = QLabel("Applying resets the simulation. Disturbances, motion, decoys and beacon visibility "
                      "can also be changed live from the main window.")
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(note)
        layout.addLayout(row)
        self.set_config(config)

    @staticmethod
    def _make_widget(kind, options):
        if kind == "int":
            w = QSpinBox()
            w.setRange(*options)
        elif kind == "float":
            lo, hi, step, decimals = options
            w = QDoubleSpinBox()
            w.setRange(lo, hi)
            w.setSingleStep(step)
            w.setDecimals(decimals)
        elif kind == "choice":
            w = QComboBox()
            w.addItems(options)
        elif kind == "bool":
            w = QCheckBox()
        else:
            w = QLineEdit()
        return w

    def set_config(self, config):
        values = asdict(config)
        for key, (widget, kind) in self.widgets.items():
            value = values[key]
            if kind in ("int", "float"):
                widget.setValue(value)
            elif kind == "choice":
                widget.setCurrentText(str(value))
            elif kind == "bool":
                widget.setChecked(bool(value))
            else:
                widget.setText(str(value))

    def config(self):
        values = {}
        for key, (widget, kind) in self.widgets.items():
            if kind in ("int", "float"):
                values[key] = widget.value()
            elif kind == "choice":
                values[key] = widget.currentText()
            elif kind == "bool":
                values[key] = widget.isChecked()
            else:
                values[key] = widget.text().strip()
        return ScenarioConfig(**values).validated()

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load scenario", "", "Scenario (*.json)")
        if not path:
            return
        try:
            self.set_config(ScenarioConfig.load(path))
        except Exception as exc:
            QMessageBox.warning(self, "Scenario not loaded", str(exc))

    def _save(self):
        config = self.config()
        path, _ = QFileDialog.getSaveFileName(self, "Save scenario", f"{config.name}.json", "Scenario (*.json)")
        if path:
            config.save(path)
