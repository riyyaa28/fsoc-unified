"""Scenario simulator for Benchmark 1 (virtual PTZ camera over a large screen).

Implements the problem-statement parameters:

* Screen (default 2000 x 2000 px) holding one designated beacon target and
  optional decoy beacons, on the original blue gradient sky (or day / night /
  plain dark). The tracker always sees the monochrome luminance image.
* Virtual camera: 640 x 480 px focal-plane array, 4 x 3 deg FOV, 30 Hz,
  starting at the centre of the screen, with pan / tilt speed limits in
  deg/s (converted to px per frame through the FOV).
* Target: ringed beacon (bright core + halo rings, the original look) or a
  square / circle / Gaussian spot, 5-20 px, random or user-defined
  initial location, motion straight / circular / figure-8 / random / spiral /
  sinusoidal (optionally switching automatically).
* Disturbances in the camera feed: salt & pepper, Gaussian and Poisson noise,
  camera jitter, atmospheric effects (haze, fog, rain, low light), platform
  motion, and timed occlusions.

Time advances by exactly 1 / update_rate per frame, so every metric is in
simulation time regardless of how fast the host machine runs.

The pointing loop (ScenarioRunner) feeds each camera frame to
vision.video_tracker.VideoBeaconTracker, then commands the camera: follow the
tracked beacon while it is tracked, otherwise run a spiral search. Ground
truth is known exactly, so centroiding error is measured on every frame.
"""

import json
import math
import time
from dataclasses import asdict, dataclass, fields

import cv2
import numpy as np

from vision.video_tracker import COAST, LOCKED, BenchmarkMetrics, VideoBeaconTracker

PATTERNS = ["straight", "circular", "figure8", "random", "spiral", "sinusoidal"]
PLATFORM_MOTIONS = ["none", "linear", "circular", "random", "sinusoidal", "figure8", "spiral"]
ATMOSPHERES = ["clear", "haze", "fog", "rain", "low light"]
SHAPES = ["ringed", "square", "circle", "gaussian"]
CAMERA_TYPES = ["monochrome", "colour"]
SKIES = ["dusk", "day", "night", "dark"]
ACQUISITION_AIDS = ["wide-field", "scan"]


# ======================================================================
# Configuration
# ======================================================================

@dataclass
class ScenarioConfig:
    name: str = "default"
    # screen and camera
    screen_width: int = 2000
    screen_height: int = 2000
    camera_width: int = 640
    camera_height: int = 480
    fov_x_deg: float = 4.0
    fov_y_deg: float = 3.0
    update_rate_hz: float = 30.0
    # The tracker always works on the monochrome (luminance) sensor image;
    # "colour" only changes what is displayed.
    camera_type: str = "colour"
    # Background: the original blue gradient ("dusk"), "day", "night", or a
    # plain dark monochrome sky ("dark", level = background_level).
    sky: str = "dusk"
    max_pan_deg_s: float = 5.0
    max_tilt_deg_s: float = 5.0
    # "wide-field": a low-resolution finder sees the whole screen and cues the
    # narrow camera; "scan": the narrow camera alone runs a spiral search.
    acquisition_aid: str = "wide-field"
    # target
    # "ringed" is the original beacon: bright core (size = core diameter)
    # with two fainter halo rings.
    target_shape: str = "ringed"
    target_size_px: int = 10
    target_level: int = 255
    background_level: int = 35
    initial_target: str = "random"          # "random" or "x,y" in screen px
    motion: str = "circular"
    auto_switch: bool = False               # change pattern every few seconds
    target_speed_px_s: float = 150.0
    decoys: int = 4
    # disturbances
    salt_pepper_pct: float = 0.0            # % of pixels (spec: around 10 %)
    gaussian_sigma: float = 0.0             # std dev in grey levels (spec max 20)
    poisson: bool = False
    poisson_peak: float = 60.0              # photons at full white; lower = noisier
    jitter_px: int = 0                      # +/- px per frame (spec max 20)
    atmosphere: str = "clear"
    atmosphere_strength: float = 0.5        # 0..1
    platform_motion: str = "none"
    platform_px_per_frame: float = 0.0      # spec max 20
    occlusions: str = ""                    # "4-5, 10-11.5" (seconds, beacon hidden)
    # run
    duration_s: float = 0.0                 # 0 = run until stopped
    seed: int = 0                           # 0 = different every run

    @property
    def px_per_deg_x(self):
        return self.camera_width / self.fov_x_deg

    @property
    def px_per_deg_y(self):
        return self.camera_height / self.fov_y_deg

    def validated(self):
        c = ScenarioConfig(**asdict(self))
        c.screen_width = int(np.clip(c.screen_width, 800, 8000))
        c.screen_height = int(np.clip(c.screen_height, 800, 8000))
        c.camera_width = int(np.clip(c.camera_width, 160, min(1920, c.screen_width - 100)))
        c.camera_height = int(np.clip(c.camera_height, 120, min(1440, c.screen_height - 100)))
        c.fov_x_deg = float(np.clip(c.fov_x_deg, 0.1, 90))
        c.fov_y_deg = float(np.clip(c.fov_y_deg, 0.1, 90))
        c.update_rate_hz = float(np.clip(c.update_rate_hz, 5, 120))
        c.max_pan_deg_s = float(np.clip(c.max_pan_deg_s, 0.1, 90))
        c.max_tilt_deg_s = float(np.clip(c.max_tilt_deg_s, 0.1, 90))
        c.target_size_px = int(np.clip(c.target_size_px, 3, 40))
        c.target_level = int(np.clip(c.target_level, 1, 255))
        c.background_level = int(np.clip(c.background_level, 0, 254))
        c.target_speed_px_s = float(np.clip(c.target_speed_px_s, 0, 5000))
        c.decoys = int(np.clip(c.decoys, 0, 20))
        c.salt_pepper_pct = float(np.clip(c.salt_pepper_pct, 0, 50))
        c.gaussian_sigma = float(np.clip(c.gaussian_sigma, 0, 100))
        c.poisson_peak = float(np.clip(c.poisson_peak, 1, 10000))
        c.jitter_px = int(np.clip(c.jitter_px, 0, 50))
        c.atmosphere_strength = float(np.clip(c.atmosphere_strength, 0, 1))
        c.platform_px_per_frame = float(np.clip(c.platform_px_per_frame, 0, 50))
        c.duration_s = float(max(0.0, c.duration_s))
        if c.motion not in PATTERNS:
            c.motion = "circular"
        if c.platform_motion not in PLATFORM_MOTIONS:
            c.platform_motion = "none"
        if c.atmosphere not in ATMOSPHERES:
            c.atmosphere = "clear"
        if c.target_shape not in SHAPES:
            c.target_shape = "ringed"
        if c.camera_type not in CAMERA_TYPES:
            c.camera_type = "colour"
        if c.sky not in SKIES:
            c.sky = "dusk"
        if c.acquisition_aid not in ACQUISITION_AIDS:
            c.acquisition_aid = "wide-field"
        return c

    def occlusion_intervals(self):
        intervals = []
        for part in str(self.occlusions).replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            start, _, end = part.partition("-")
            try:
                intervals.append((float(start), float(end)))
            except ValueError:
                continue
        return intervals

    def save(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known}).validated()

    def rows(self):
        """(parameter, value) rows describing the scenario, for reports."""
        occl = self.occlusions.strip() or "none"
        return [
            ["Scenario", self.name],
            ["Screen size", f"{self.screen_width} x {self.screen_height} px"],
            ["Camera", f"{self.camera_width} x {self.camera_height} px, FOV {self.fov_x_deg:g} x "
                       f"{self.fov_y_deg:g} deg, {self.update_rate_hz:g} Hz; monochrome sensor"
                       f"{' (colour display)' if self.camera_type == 'colour' else ''}"],
            ["Sky / background", self.sky if self.sky != "dark" else f"dark, level {self.background_level}"],
            ["Angular resolution", f"{self.px_per_deg_x:.1f} x {self.px_per_deg_y:.1f} px/deg"],
            ["Initial camera position", "Centre of the screen"],
            ["Acquisition", "Wide-field finder (whole screen at 1/4 resolution) cues the camera; spiral scan "
                            "if the finder has no target" if self.acquisition_aid == "wide-field"
             else "Spiral scan with the narrow camera"],
            ["Max pan / tilt speed", f"{self.max_pan_deg_s:g} / {self.max_tilt_deg_s:g} deg/s "
                                     f"({self.max_pan_deg_s * self.px_per_deg_x / self.update_rate_hz:.1f} / "
                                     f"{self.max_tilt_deg_s * self.px_per_deg_y / self.update_rate_hz:.1f} px/frame)"],
            ["Target", f"{self.target_shape}, {self.target_size_px} x {self.target_size_px} px"
                       f"{' core with halo rings' if self.target_shape == 'ringed' else ''}, level {self.target_level}"],
            ["Initial target location", self.initial_target],
            ["Motion", f"{self.motion}{' (auto-switching)' if self.auto_switch else ''}, "
                       f"{self.target_speed_px_s:g} px/s ({self.target_speed_px_s / self.px_per_deg_x:.2f} deg/s)"],
            ["Decoy targets", str(self.decoys)],
            ["Salt & pepper noise", f"{self.salt_pepper_pct:g} % of pixels" if self.salt_pepper_pct else "off"],
            ["Gaussian noise", f"sigma {self.gaussian_sigma:g}" if self.gaussian_sigma else "off"],
            ["Poisson noise", f"on ({self.poisson_peak:g} photons at full white)" if self.poisson else "off"],
            ["Camera jitter", f"+/- {self.jitter_px} px/frame" if self.jitter_px else "off"],
            ["Atmosphere", f"{self.atmosphere}" + (f", strength {self.atmosphere_strength:.2f}"
                                                   if self.atmosphere != "clear" else "")],
            ["Platform motion", f"{self.platform_motion}, {self.platform_px_per_frame:g} px/frame"
             if self.platform_motion != "none" and self.platform_px_per_frame else "off"],
            ["Occlusions", occl],
            ["Duration", f"{self.duration_s:g} s" if self.duration_s else "until stopped"],
            ["Random seed", str(self.seed) if self.seed else "random"],
        ]


# ======================================================================
# Target motion
# ======================================================================

class TargetMotion:
    """Beacon trajectory on the screen. Pattern changes blend smoothly."""

    BLEND_S = 1.0

    def __init__(self, cfg, rng, start=None):
        self.cfg = cfg
        self.rng = rng
        self.w, self.h = cfg.screen_width, cfg.screen_height
        self.margin = max(40.0, 3.0 * cfg.target_size_px)
        self.speed = cfg.target_speed_px_s
        self.pos = np.array(start if start is not None else self._initial(), dtype=float)
        self.switch_timer = self._switch_interval()
        self.set_pattern(cfg.motion)

    def _initial(self):
        text = str(self.cfg.initial_target).strip().lower()
        if text not in ("", "random"):
            try:
                x, y = (float(v) for v in text.split(","))
                return self._clip(np.array([x, y]))
            except ValueError:
                pass
        return np.array([self.rng.uniform(self.margin, self.w - self.margin),
                         self.rng.uniform(self.margin, self.h - self.margin)])

    def _clip(self, p):
        return np.array([np.clip(p[0], self.margin, self.w - self.margin),
                         np.clip(p[1], self.margin, self.h - self.margin)])

    def _switch_interval(self):
        return float(self.rng.uniform(5.0, 9.0))

    def _space(self):
        return min(self.w, self.h) / 2.0 - self.margin

    def set_pattern(self, name):
        self.pattern = name if name in PATTERNS else "circular"
        self.t = 0.0
        self.anchor = self.pos.copy()
        angle = self.rng.uniform(0, 2 * math.pi)
        self.direction = np.array([math.cos(angle), math.sin(angle)])
        self.vel = self.direction * self.speed
        self.sign = 1.0 if self.rng.random() < 0.5 else -1.0
        space = self._space()
        if self.pattern == "circular":
            self.radius = min(0.25 * min(self.w, self.h), space)
            self.phase = angle
            centre = self.anchor - self.radius * np.array([math.cos(angle), math.sin(angle)])
            self.centre = np.clip(centre, self.margin + self.radius,
                                  np.array([self.w, self.h]) - self.margin - self.radius)
        elif self.pattern == "figure8":
            self.amp = min(0.3 * min(self.w, self.h), space)
            self.centre = np.clip(self.anchor, self.margin + self.amp,
                                  np.array([self.w, self.h]) - self.margin - self.amp)
        elif self.pattern == "spiral":
            self.r_max = min(0.3 * min(self.w, self.h), space)
            self.centre = np.clip(self.anchor, self.margin + self.r_max,
                                  np.array([self.w, self.h]) - self.margin - self.r_max)
            self.theta, self.growing = 0.0, True
            self.b = max(self.r_max / (4 * math.pi), 5.0)   # two turns out, two back
        elif self.pattern == "sinusoidal":
            self.s = 0.0
            self.amp = min(0.12 * min(self.w, self.h), space)
            self.wavelength = 0.5 * min(self.w, self.h)
            self.axis_dir = self.sign
            self.base_y = float(np.clip(self.anchor[1], self.margin + self.amp, self.h - self.margin - self.amp))
            self.x = float(self.anchor[0])
        self.offset = self.anchor - self._raw(0.0, advance=False)
        self.blend = 0.0

    def _raw(self, dt, advance=True):
        """Pattern position after advancing dt seconds (dt=0: current)."""
        p = self.pattern
        if p == "circular":
            omega = self.speed / max(self.radius, 1.0) * self.sign
            phase = self.phase + omega * (self.t + dt)
            return self.centre + self.radius * np.array([math.cos(phase), math.sin(phase)])
        if p == "figure8":
            omega = self.speed / max(self.amp * 1.2, 1.0)
            u = omega * (self.t + dt) * self.sign
            return self.centre + np.array([self.amp * math.sin(u), 0.5 * self.amp * math.sin(2 * u)])
        if p == "spiral":
            theta, growing = self.theta, self.growing
            if dt:
                r = self.b * theta
                d_theta = self.speed * dt / math.sqrt(r * r + self.b * self.b)
                theta = theta + d_theta if growing else theta - d_theta
                if self.b * theta >= self.r_max:
                    growing = False
                elif theta <= 0:
                    theta, growing = 0.0, True
                if advance:
                    self.theta, self.growing = theta, growing
            r = self.b * theta
            angle = theta * self.sign
            return self.centre + r * np.array([math.cos(angle), math.sin(angle)])
        if p == "sinusoidal":
            x, direction = self.x, self.axis_dir
            if dt:
                slope = 2 * math.pi * self.amp / self.wavelength
                x += direction * self.speed * dt / math.sqrt(1 + 0.5 * slope * slope)
                if x < self.margin or x > self.w - self.margin:
                    direction = -direction
                    x = float(np.clip(x, self.margin, self.w - self.margin))
                if advance:
                    self.x, self.axis_dir = x, direction
            return np.array([x, self.base_y + self.amp * math.sin(2 * math.pi * x / self.wavelength)])
        # straight / random: integrate velocity with reflection at the margins
        pos = self.anchor.copy()
        if not dt:
            return pos
        vel = self.vel.copy()
        if p == "random":
            vel += self.rng.normal(0, 1, 2) * self.speed * 1.5 * dt
            norm = np.linalg.norm(vel)
            target = self.speed * (0.6 + 0.8 * self.rng.random()) if norm < 1e-6 else norm
            target = float(np.clip(target, 0.5 * self.speed, 1.5 * self.speed))
            vel = vel / max(norm, 1e-6) * target
        pos = pos + vel * dt
        for axis, limit in ((0, self.w), (1, self.h)):
            if pos[axis] < self.margin or pos[axis] > limit - self.margin:
                vel[axis] = -vel[axis]
                pos[axis] = float(np.clip(pos[axis], self.margin, limit - self.margin))
        if advance:
            self.anchor, self.vel = pos, vel
        return pos

    def step(self, dt):
        if self.cfg.auto_switch:
            self.switch_timer -= dt
            if self.switch_timer <= 0:
                choices = [p for p in PATTERNS if p != self.pattern]
                self.set_pattern(choices[int(self.rng.integers(len(choices)))])
                self.switch_timer = self._switch_interval()
        raw = self._raw(dt)
        self.t += dt
        self.blend = min(1.0, self.blend + dt / self.BLEND_S)
        self.pos = self._clip(raw + self.offset * (1.0 - self.blend))
        return self.pos


class PlatformMotion:
    """Per-frame displacement of the camera platform (disturbance on pointing)."""

    def __init__(self, kind, amplitude, rng, fps):
        self.kind, self.amp, self.rng, self.fps = kind, float(amplitude), rng, fps
        angle = rng.uniform(0, 2 * math.pi)
        self.direction = np.array([math.cos(angle), math.sin(angle)])
        self.velocity = np.zeros(2)
        self.frame = 0

    def bounce(self, blocked):
        """Linear drift reverses on the axes where the camera hit the screen edge."""
        self.direction = np.where(blocked, -self.direction, self.direction)

    def step(self):
        """Displacement in px for this frame."""
        a, t = self.amp, self.frame / self.fps
        self.frame += 1
        if self.kind == "none" or a <= 0:
            return np.zeros(2)
        omega = 2 * math.pi / 4.0            # 4 s period for periodic motions
        if self.kind == "linear":
            return self.direction * a
        if self.kind == "circular":
            return a * np.array([math.cos(omega * t), math.sin(omega * t)])
        if self.kind == "sinusoidal":
            return self.direction * a * math.sin(omega * t)
        if self.kind == "figure8":
            v = np.array([math.sin(omega * t), math.sin(2 * omega * t)])
            return a * v / max(np.linalg.norm(v), 1.0)
        if self.kind == "spiral":
            return a * ((t / 8.0) % 1.0) * np.array([math.cos(omega * t), math.sin(omega * t)])
        # random: smooth random walk bounded by the amplitude
        self.velocity += self.rng.normal(0, a * 0.3, 2)
        norm = np.linalg.norm(self.velocity)
        if norm > a:
            self.velocity *= a / norm
        return self.velocity.copy()


# ======================================================================
# Scene and camera rendering
# ======================================================================

def _coverage(center, size, count):
    edges = np.arange(count, dtype=np.float32)
    lo = np.maximum(edges - 0.5, center - size / 2.0)
    hi = np.minimum(edges + 0.5, center + size / 2.0)
    return np.clip(hi - lo, 0.0, 1.0)


def spot_patch(shape, size, fx, fy):
    """Coverage patch (0..1) for a spot whose centre is at fractional offset (fx, fy)
    inside a (n x n) patch; returns (patch, n) with the spot centred at ((n-1)/2+fx, ...)."""
    n = int(math.ceil(size * (2.0 if shape == "gaussian" else 1.0))) + 3
    c = (n - 1) / 2.0
    if shape == "square":
        return np.outer(_coverage(c + fy, size, n), _coverage(c + fx, size, n)), n
    ss = 4                                   # supersampling for round shapes
    grid = (np.arange(n * ss, dtype=np.float32) + 0.5) / ss - 0.5
    gx = grid[None, :] - (c + fx)
    gy = grid[:, None] - (c + fy)
    if shape == "circle":
        inside = (gx * gx + gy * gy <= (size / 2.0) ** 2).astype(np.float32)
    elif shape == "ring":                    # 1 px wide ring of diameter `size`
        inside = (np.abs(np.sqrt(gx * gx + gy * gy) - size / 2.0) <= 0.5).astype(np.float32)
    else:                                    # gaussian: FWHM = size
        sigma = size / 2.3548
        inside = np.exp(-(gx * gx + gy * gy) / (2 * sigma * sigma)).astype(np.float32)
    return inside.reshape(n, ss, n, ss).mean(axis=(1, 3)), n


class Scene:
    """Static background, beacon, decoys and camera-image rendering."""

    def __init__(self, cfg, rng):
        self.cfg = cfg
        self.rng = rng
        self.w, self.h = cfg.screen_width, cfg.screen_height
        # The sky continues past the screen border so the camera can centre a
        # beacon anywhere on the screen, including at its edges (plus jitter).
        self.pad = max(cfg.camera_width, cfg.camera_height) // 2 + 60
        # background_bgr: colour sky for display (None for the plain dark sky);
        # background: its luminance, which is what the monochrome sensor sees.
        self.background_bgr, self.background = self._make_background()
        self.motion = TargetMotion(cfg, rng)
        self.decoys = self._make_decoys(cfg.decoys)
        self.beacon_hidden = False           # manual HIDE BEACON
        self._rain_layers = None

    def _make_background(self):
        w, h = self.w + 2 * self.pad, self.h + 2 * self.pad
        if self.cfg.sky != "dark":
            # The original sky gradient (sim/sky.py), stretched over the screen.
            from sim.sky import render_sky
            column = render_sky(1, h, mode=self.cfg.sky)
            colour = np.ascontiguousarray(np.broadcast_to(column, (h, w, 3)))
            return colour, cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY)
        # Plain dark monochrome sky: gentle gradient plus faint structure.
        base = float(self.cfg.background_level)
        small = self.rng.normal(0, 1, (max(4, h // 250), max(4, w // 250))).astype(np.float32)
        structure = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
        gradient = np.linspace(1.12, 0.88, h, dtype=np.float32)[:, None]
        bg = base * gradient + structure * max(2.0, base * 0.06)
        return None, np.clip(bg, 0, 255).astype(np.uint8)

    def _make_decoys(self, count):
        """Decoy beacons in the original style: round white spots of varying
        size and brightness drifting slowly. They stay a little smaller and
        dimmer than the beacon core, so the designated beacon remains the
        brightest target (as the problem statement requires it to be
        identifiable)."""
        decoys = []
        size = self.cfg.target_size_px
        drift = 0.3 * max(self.cfg.target_speed_px_s, 50.0)
        for _ in range(count):
            decoys.append({
                "pos": np.array([self.rng.uniform(60, self.w - 60), self.rng.uniform(60, self.h - 60)]),
                "vel": self.rng.uniform(-drift, drift, 2),
                "size": float(size * self.rng.choice([0.4, 0.5, 0.6, 0.7, 0.8])),
                "level": float(self.cfg.target_level * self.rng.uniform(0.35, 0.65)),
            })
        return decoys

    def set_decoys(self, count):
        self.decoys = self._make_decoys(count)

    def step(self, dt):
        self.motion.step(dt)
        for d in self.decoys:
            d["pos"] += d["vel"] * dt
            for axis, limit in ((0, self.w), (1, self.h)):
                if not 40 < d["pos"][axis] < limit - 40:
                    d["vel"][axis] *= -1
                    d["pos"][axis] = float(np.clip(d["pos"][axis], 40, limit - 40))

    def _draw_spot(self, images, x, y, shape, size, level):
        """Blend a white spot centred at (x, y) image px into one or more
        float32 images (grey H x W and / or colour H x W x 3)."""
        ix, iy = int(math.floor(x)), int(math.floor(y))
        patch, n = spot_patch(shape, size, x - ix, y - iy)
        half = (n - 1) // 2
        x0, y0 = ix - half, iy - half
        for image in images if isinstance(images, (list, tuple)) else (images,):
            if image is None:
                continue
            h, w = image.shape[:2]
            sx0, sy0 = max(0, -x0), max(0, -y0)
            sx1, sy1 = min(n, w - x0), min(n, h - y0)
            if sx1 <= sx0 or sy1 <= sy0:
                continue
            region = image[y0 + sy0:y0 + sy1, x0 + sx0:x0 + sx1]
            cover = patch[sy0:sy1, sx0:sx1]
            region += (cover[:, :, None] if region.ndim == 3 else cover) * (level - region)

    def draw_beacon(self, images, x, y, size=None, level=None):
        """The designated beacon. 'ringed' is the original look: a filled
        core (diameter = size) with a halo ring at 2x and a fainter one at
        3.5x the core diameter; the rings are symmetric, so the centroid is
        the core centre."""
        size = self.cfg.target_size_px if size is None else size
        level = self.cfg.target_level if level is None else level
        if self.cfg.target_shape == "ringed":
            self._draw_spot(images, x, y, "ring", 3.5 * size, level * 80 / 255)
            self._draw_spot(images, x, y, "ring", 2.0 * size, level * 200 / 255)
            self._draw_spot(images, x, y, "circle", size, level)
        else:
            self._draw_spot(images, x, y, self.cfg.target_shape, size, level)

    def render_camera(self, origin, beacon_visible, t):
        """Monochrome sensor image (float32, before noise), top-left at screen `origin`."""
        cw, ch = self.cfg.camera_width, self.cfg.camera_height
        ox, oy = int(math.floor(origin[0])) + self.pad, int(math.floor(origin[1])) + self.pad
        grey = self.background[oy:oy + ch, ox:ox + cw].astype(np.float32)
        margin = 4 * self.cfg.target_size_px + 4
        for d in self.decoys:
            x, y = d["pos"][0] - origin[0], d["pos"][1] - origin[1]
            if -margin < x < cw + margin and -margin < y < ch + margin:
                self._draw_spot(grey, x, y, "circle", d["size"], d["level"])
        bx, by = self.motion.pos[0] - origin[0], self.motion.pos[1] - origin[1]
        if beacon_visible and -margin < bx < cw + margin and -margin < by < ch + margin:
            self.draw_beacon(grey, bx, by)
        return grey

    def colourise(self, frame, origin, size):
        """Colour display version of a sensor frame at display `size` (w, h).

        The sky colour comes from the background; everything the sensor adds
        on top of the sky (beacon, decoys, noise, atmosphere) is the grey
        difference, laid over all three channels. Done at display size, it
        costs almost nothing per frame.
        """
        if self.background_bgr is None:
            return None
        cw, ch = self.cfg.camera_width, self.cfg.camera_height
        ox, oy = int(math.floor(origin[0])) + self.pad, int(math.floor(origin[1])) + self.pad
        sky = cv2.resize(self.background_bgr[oy:oy + ch, ox:ox + cw], size, interpolation=cv2.INTER_AREA)
        sky_grey = cv2.resize(self.background[oy:oy + ch, ox:ox + cw], size, interpolation=cv2.INTER_AREA)
        seen = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        added = seen.astype(np.int16) - sky_grey.astype(np.int16)
        return np.clip(sky.astype(np.int16) + added[:, :, None], 0, 255).astype(np.uint8)

    def rain_layer(self, frame_index, shape):
        if self._rain_layers is None or self._rain_layers[0].shape != shape:
            layers = []
            for _ in range(6):
                layer = np.zeros(shape, np.float32)
                for _ in range(int(shape[0] * shape[1] / 900)):
                    x, y = int(self.rng.integers(0, shape[1])), int(self.rng.integers(0, shape[0]))
                    length = int(self.rng.integers(8, 20))
                    cv2.line(layer, (x, y), (x + 2, y + length), 1.0, 1)
                layers.append(layer)
            self._rain_layers = layers
        return self._rain_layers[frame_index % len(self._rain_layers)]


class PoissonSampler:
    """Fast Poisson (photon shot) noise for 8-bit images.

    Each grey level L has expected photon count lambda = L * peak / 255. For
    every level the inverse CDF is tabulated at BINS evenly spaced
    probabilities, so a sample is one table lookup instead of a call into the
    generic sampler (about 20 ms per 640 x 480 frame). Tail probabilities
    below 1 / BINS are folded into the last bin.
    """

    # 1024 bins of uint16 photon counts keep the table (512 KB) in the CPU
    # cache even when a frame spans every grey level (e.g. the dusk sky).
    BINS = 1024
    _cache = {}

    def __init__(self, peak):
        self.peak = float(peak)
        self.scale = 255.0 / self.peak
        probabilities = (np.arange(self.BINS) + 0.5) / self.BINS
        table = np.zeros((256, self.BINS), np.uint16)
        for level in range(1, 256):
            lam = level * self.peak / 255.0
            k_max = int(lam + 12 * math.sqrt(lam) + 12)
            k = np.arange(k_max + 1)
            log_pmf = k * math.log(lam) - lam - np.array([math.lgamma(v + 1) for v in k])
            cdf = np.cumsum(np.exp(log_pmf))
            table[level] = np.minimum(np.searchsorted(cdf, probabilities * cdf[-1]), 65535)
        self.table = table
        self.flat = table.ravel()

    @classmethod
    def get(cls, peak):
        key = round(float(peak), 3)
        if key not in cls._cache:
            cls._cache[key] = cls(key)
        return cls._cache[key]

    def sample(self, image, rng, bins=None):
        index = np.clip(image + 0.5, 0, 255).astype(np.int32)
        index *= self.BINS
        index += bins if bins is not None else rng.integers(0, self.BINS, image.shape, dtype=np.int32)
        out = np.take(self.flat, index).astype(np.float32)
        out *= self.scale                    # photon counts back to grey levels
        return out


class NoiseSource:
    """Per-frame random fields for image noise from OpenCV's C++ generators.

    cv2.randn / cv2.randu are 2-4x faster than numpy for camera-sized arrays,
    which matters at 30 Hz. The generator is seeded from the scenario seed
    (OpenCV's RNG is per thread and the loop runs on one thread), so runs are
    reproducible. Buffers are reused from frame to frame.
    """

    def __init__(self, seed):
        cv2.setRNGSeed(int(seed) & 0x7FFFFFFF)
        self._buffers = {}

    def _buffer(self, key, shape, dtype):
        buf = self._buffers.get((key, shape))
        if buf is None:
            buf = self._buffers[(key, shape)] = np.empty(shape, dtype)
        return buf

    def fields(self, cfg, shape):
        out = {}
        if cfg.poisson:
            out["poisson_bins"] = cv2.randu(self._buffer("poisson", shape, np.int32), 0, PoissonSampler.BINS)
        if cfg.gaussian_sigma > 0:
            out["gaussian"] = cv2.randn(self._buffer("gaussian", shape, np.float32), 0.0, 1.0)
        if cfg.salt_pepper_pct > 0:
            out["salt_pepper"] = cv2.randu(self._buffer("salt_pepper", shape, np.float32), 0.0, 1.0)
        return out


def _atmosphere(image, cfg, rain_layer=None):
    """Contrast / brightness effects of the atmosphere (grey or colour image)."""
    s = cfg.atmosphere_strength
    if cfg.atmosphere == "haze":
        return image * (1 - 0.55 * s) + 150.0 * 0.55 * s
    if cfg.atmosphere == "fog":
        return cv2.GaussianBlur(image * (1 - 0.8 * s) + 175.0 * 0.8 * s, (0, 0), 0.8 + 1.5 * s)
    if cfg.atmosphere == "rain":
        image = image * (1 - 0.3 * s) + 90.0 * 0.3 * s
        if rain_layer is not None:
            image = image + (rain_layer[:, :, None] if image.ndim == 3 else rain_layer) * (60.0 * s)
        return image
    if cfg.atmosphere == "low light":
        return image * (1 - 0.8 * s)
    return image


def apparent_level(cfg, level=None):
    """Brightness of a full-white spot after the atmosphere (no noise). The
    tracker uses it to recognise the designated beacon among dimmer decoys."""
    level = float(cfg.target_level if level is None else level)
    s = cfg.atmosphere_strength
    if cfg.atmosphere == "haze":
        return level * (1 - 0.55 * s) + 150.0 * 0.55 * s
    if cfg.atmosphere == "fog":
        return level * (1 - 0.8 * s) + 175.0 * 0.8 * s
    if cfg.atmosphere == "rain":
        return level * (1 - 0.3 * s) + 90.0 * 0.3 * s
    if cfg.atmosphere == "low light":
        return level * (1 - 0.8 * s)
    return level


def probe_level(shape, size, level, worst="low", blur_sigma=0.0):
    """Core brightness the tracker would measure for a spot, found by
    rendering it on a black background at a few sub-pixel positions and
    applying the tracker's 3 x 3 median filter (after any fog blur).
    worst="low" returns the dimmest result (for the beacon), "high" the
    brightest (for decoys)."""
    results = []
    for fx, fy in ((0.0, 0.0), (0.5, 0.5), (0.25, 0.75), (0.5, 0.0)):
        patch, n = spot_patch(shape, size, fx, fy)
        pad = n + 6
        image = np.zeros((n + 2 * pad, n + 2 * pad), np.float32)
        image[pad:pad + n, pad:pad + n] = patch * level
        if blur_sigma > 0:
            image = cv2.GaussianBlur(image, (0, 0), blur_sigma)
        filtered = cv2.medianBlur(np.clip(image, 0, 255).astype(np.uint8), 3).astype(np.float32)
        peak = float(filtered.max())
        if peak <= 0:
            results.append(0.0)
            continue
        results.append(float(np.percentile(filtered[filtered > 0.4 * peak], 90)))   # same statistic as the tracker
    return min(results) if worst == "low" else max(results)


class LevelGate:
    """Brightness gate separating the designated beacon from decoys for one
    sensor (camera or finder), calibrated for that sensor's resolution."""

    def __init__(self, cfg, scale=1.0):
        self.shape = "circle" if cfg.target_shape == "ringed" else cfg.target_shape   # the core carries the level
        self.size = cfg.target_size_px / scale
        self.level = cfg.target_level
        self.beacon = probe_level(self.shape, self.size, self.level, "low")
        self.decoy = probe_level("circle", 0.8 * self.size, 0.65 * self.level, "high")
        self._fog = {}                       # fog strength -> (beacon, decoy) probe levels

    def _levels(self, cfg):
        if cfg.atmosphere != "fog" or cfg.atmosphere_strength <= 0:
            return self.beacon, self.decoy
        key = round(cfg.atmosphere_strength, 2)
        if key not in self._fog:
            # Fog blurs the spot (same sigma as _atmosphere), dimming a small core.
            # apparent_level() then applies the fog veil to these blurred levels.
            sigma = 0.8 + 1.5 * key
            self._fog[key] = (probe_level(self.shape, self.size, self.level, "low", sigma),
                              probe_level("circle", 0.8 * self.size, 0.65 * self.level, "high", sigma))
        return self._fog[key]

    def apply(self, tracker, cfg):
        """Set the tracker's expected beacon level and decoy ratio for the current atmosphere."""
        beacon_raw, decoy_raw = self._levels(cfg)
        beacon = apparent_level(cfg, beacon_raw)
        tracker.expected_level = beacon
        if cfg.decoys == 0:
            # Nothing to confuse the beacon with: only reject clearly dimmer spots.
            tracker.DECOY_LEVEL_RATIO = 0.4
            return
        decoy = apparent_level(cfg, decoy_raw)
        tracker.DECOY_LEVEL_RATIO = min(0.95, max(0.3, 0.5 * (beacon + decoy) / max(beacon, 1.0)))


def apply_disturbances(image, cfg, rng, rain_layer=None, fields=None):
    """Atmosphere then noise on a float32 grey camera image; returns uint8.

    fields: optional pre-drawn random fields (NoiseSource.fields); any that are
    missing are drawn from rng here.
    """
    fields = fields or {}
    image = _atmosphere(image, cfg, rain_layer)
    if cfg.poisson:
        image = PoissonSampler.get(cfg.poisson_peak).sample(image, rng, fields.get("poisson_bins"))
    if cfg.gaussian_sigma > 0:
        noise = fields.get("gaussian")
        noise = rng.standard_normal(image.shape, dtype=np.float32) if noise is None else noise
        noise *= cfg.gaussian_sigma
        noise += image
        image = noise
    out = np.clip(image, 0, 255).astype(np.uint8)
    if cfg.salt_pepper_pct > 0:
        u = fields.get("salt_pepper")
        u = rng.random(out.shape, dtype=np.float32) if u is None else u
        p = cfg.salt_pepper_pct / 200.0
        out[u < p] = 0
        out[u > 1 - p] = 255
    return out


# ======================================================================
# Pointing: search pattern and controller
# ======================================================================

class SpiralSearch:
    """Archimedean spiral of camera aim points, ring spacing ~80 % of the FOV height."""

    def __init__(self, centre, cfg, speed_px):
        self.centre = np.array(centre, float)
        self.spacing = 0.8 * min(cfg.camera_width, cfg.camera_height)
        self.b = self.spacing / (2 * math.pi)
        self.theta = 0.0
        self.speed = speed_px
        self.limit = math.hypot(cfg.screen_width, cfg.screen_height) / 2 + self.spacing
        self.screen_centre = np.array([cfg.screen_width / 2, cfg.screen_height / 2])

    def next_point(self):
        r = self.b * self.theta
        self.theta += self.speed / math.sqrt(r * r + self.b * self.b)
        r = self.b * self.theta
        if r > self.limit:
            # Whole screen covered: restart from the screen centre.
            self.centre, self.theta = self.screen_centre.copy(), 0.0
        return self.centre + r * np.array([math.cos(self.theta), math.sin(self.theta)])


class WideFieldFinder:
    """Wide-field acquisition sensor: the whole screen at 1/scale resolution.

    Rendered with the same atmosphere and noise model as the camera. It runs
    only while the narrow camera has no lock, confirms a target like the main
    tracker, and returns its position in screen px to cue the camera.
    """

    def __init__(self, cfg, scene, fps, scale=2):
        self.cfg, self.scene, self.scale = cfg, scene, scale
        self.w, self.h = cfg.screen_width // scale, cfg.screen_height // scale
        pad = scene.pad
        screen = scene.background[pad:pad + cfg.screen_height, pad:pad + cfg.screen_width]
        self.background = cv2.resize(screen, (self.w, self.h), interpolation=cv2.INTER_AREA).astype(np.float32)
        self.tracker = VideoBeaconTracker(self.w, self.h, fps, beacon_size=max(3.0, cfg.target_size_px / scale))
        # At 1/scale resolution the beacon is proportionally smaller, so the
        # camera's 3 px minimum-width rule for acquisition is scaled down too.
        self.tracker.ACQUIRE_MIN_WIDTH = 1.5
        self.tracker.EDGE_MARGIN_FACTOR = 0.0
        self.tracker.identify_by_level = True      # brightest spot on the screen = the beacon
        self.gate = LevelGate(cfg, scale)

    def usable(self):
        """Whether, at this resolution, the beacon stays clearly brighter than
        the brightest possible decoy (from the calibration render)."""
        return self.gate.beacon > 1.15 * self.gate.decoy

    def process(self, beacon_visible, noise, rng, index):
        cfg, s = self.cfg, self.scale
        image = self.background.copy()
        for d in self.scene.decoys:
            self.scene._draw_spot(image, d["pos"][0] / s, d["pos"][1] / s, "circle", max(1.0, d["size"] / s), d["level"])
        if beacon_visible:
            pos = self.scene.motion.pos
            # At 1/4 resolution the thin halo rings average away; draw the core.
            shape = "circle" if cfg.target_shape == "ringed" else cfg.target_shape
            self.scene._draw_spot(image, pos[0] / s, pos[1] / s, shape,
                                  max(1.0, cfg.target_size_px / s), cfg.target_level)
        frame = apply_disturbances(image, cfg, rng, None, noise.fields(cfg, image.shape))
        self.gate.apply(self.tracker, cfg)
        # The finder is fixed to the screen: only the beacon itself moves.
        self.tracker.motion_px = (cfg.target_speed_px_s / cfg.update_rate_hz + cfg.target_size_px) / s
        result = self.tracker.process(frame, index)
        # A confirmed finder track cues the camera; during a brief dropout its
        # prediction keeps the cue steady instead of flickering.
        if result.state in (LOCKED, COAST) and result.x is not None:
            return (result.x * s + (s - 1) / 2.0, result.y * s + (s - 1) / 2.0)
        return None


class ScenarioRunner:
    """One scenario: scene + camera + tracker + pointing loop + metrics."""

    FOLLOW_GAIN = 0.5

    def __init__(self, cfg, yolo=None, yolo_async=False, seed_override=None):
        self.cfg = cfg = cfg.validated()
        seed = seed_override if seed_override is not None else (cfg.seed or int(time.time() * 1000) % 2**31)
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.noise = NoiseSource(seed)
        self.scene = Scene(cfg, self.rng)
        self.fps = cfg.update_rate_hz
        self.dt = 1.0 / self.fps
        self.v_max = np.array([cfg.max_pan_deg_s * cfg.px_per_deg_x / self.fps,
                               cfg.max_tilt_deg_s * cfg.px_per_deg_y / self.fps])
        # The boresight can point at any point of the screen.
        self.cam_min = np.zeros(2)
        self.cam_max = np.array([float(cfg.screen_width), float(cfg.screen_height)])
        self.camera = np.array([cfg.screen_width / 2.0, cfg.screen_height / 2.0])   # spec: start at centre
        self.platform = PlatformMotion(cfg.platform_motion, cfg.platform_px_per_frame, self.rng, self.fps)
        self.tracker = VideoBeaconTracker(cfg.camera_width, cfg.camera_height, self.fps,
                                          beacon_size=cfg.target_size_px, yolo=yolo, yolo_async=yolo_async)
        # The designated beacon is known to be the brightest (full-white core)
        # spot; decoys are dimmer. Identify it by brightness, not contrast.
        self.tracker.identify_by_level = True
        self.level_gate = LevelGate(cfg)
        self.metrics = ScenarioMetrics(self.fps, cfg.camera_width, cfg.camera_height,
                                       (cfg.fov_x_deg, cfg.fov_y_deg), {})
        self.metrics.screen_size = (cfg.screen_width, cfg.screen_height)
        self.search = SpiralSearch(self.camera, cfg, 0.9 * float(self.v_max.min()))
        self.finder = WideFieldFinder(cfg, self.scene, self.fps) if cfg.acquisition_aid == "wide-field" else None
        if self.finder is not None and not self.finder.usable():
            # Too small for the finder's resolution: it could not tell the beacon
            # from decoys, so acquisition uses the camera's spiral scan instead.
            self.finder = None
        self.acquisition_mode = "wide-field finder" if self.finder is not None else "spiral scan"
        self.finder_result = None
        self.occlusions = cfg.occlusion_intervals()
        self.frame_index = 0
        self.last_command = np.zeros(2)
        self.last_frame = None
        self.last_result = None
        self.last_truth = None
        self.last_origin = self.camera - np.array([cfg.camera_width / 2, cfg.camera_height / 2])
        self._was_tracking = False

    # -- live controls ------------------------------------------------------

    def set_pattern(self, name, auto=False):
        self.cfg.auto_switch = auto
        if name in PATTERNS:
            self.cfg.motion = name
            self.scene.motion.set_pattern(name)

    def set_decoys(self, count):
        self.cfg.decoys = int(count)
        self.scene.set_decoys(int(count))

    def update_disturbances(self, **values):
        for key, value in values.items():
            setattr(self.cfg, key, value)
        if "platform_motion" in values or "platform_px_per_frame" in values:
            self.platform = PlatformMotion(self.cfg.platform_motion, self.cfg.platform_px_per_frame, self.rng, self.fps)

    @property
    def time_s(self):
        return self.frame_index / self.fps

    def beacon_visible(self):
        t = self.time_s
        occluded = any(start <= t < end for start, end in self.occlusions)
        return not occluded and not self.scene.beacon_hidden

    def finished(self):
        return self.cfg.duration_s > 0 and self.time_s >= self.cfg.duration_s

    # -- one frame -----------------------------------------------------------

    def step(self, frame_started=None):
        frame_started = frame_started or time.perf_counter()
        cfg = self.cfg
        index = self.frame_index
        half = np.array([cfg.camera_width / 2.0, cfg.camera_height / 2.0])

        # 1. Image formation at the current pointing (+ jitter).
        jitter = (self.rng.integers(-cfg.jitter_px, cfg.jitter_px + 1, 2).astype(float)
                  if cfg.jitter_px else np.zeros(2))
        origin = self.camera - half + jitter
        visible = self.beacon_visible()
        image = self.scene.render_camera(origin, visible, self.time_s)
        rain = self.scene.rain_layer(index, image.shape) if cfg.atmosphere == "rain" else None
        frame = apply_disturbances(image, cfg, self.rng, rain, self.noise.fields(cfg, image.shape))
        truth = tuple(self.scene.motion.pos - origin) if visible else None
        self.metrics.ground_truth[index] = truth

        # 2. Detection / tracking in the camera image. Without a camera lock the
        #    wide-field finder looks for the beacon over the whole screen.
        hint = None
        self.finder_result = None
        if self.finder is not None and self.tracker.state != LOCKED:
            self.finder_result = self.finder.process(visible, self.noise, self.rng, index)
            if self.finder_result is not None:
                fx, fy = self.finder_result
                hint = (fx - origin[0], fy - origin[1], 4 * self.finder.scale + 3 * cfg.target_size_px)
        self.level_gate.apply(self.tracker, cfg)                 # follows live atmosphere changes
        # Image motion between frames the tracker cannot predict: the change
        # in jitter (up to 2x its amplitude), platform drift and the beacon's
        # own motion. Bounds the spread of the tracker's multi-frame evidence.
        self.tracker.motion_px = (2 * cfg.jitter_px + cfg.platform_px_per_frame
                                  + cfg.target_speed_px_s / self.fps + cfg.target_size_px)
        result = self.tracker.process(frame, index, hint)

        # 3. Pointing command for the next frame (rate limited). Only a
        #    confirmed track is followed; a tentative candidate must not stop
        #    or restart the search.
        tracking = result.state in (LOCKED, COAST) and result.x is not None
        if tracking:
            # Steer on the filtered position; the raw centroid includes jitter.
            filtered = self.tracker.last_position() if result.measured else None
            aim = filtered if filtered is not None else np.array([result.x, result.y])
            error = aim - half
            command = error * self.FOLLOW_GAIN + self.tracker.velocity()
            self._was_tracking = True
        else:
            if self._was_tracking:
                # Track lost: search around the last predicted position.
                predicted = self.camera + (self.tracker.last_position() - half
                                           if self.tracker.last_position() is not None else 0)
                self.search = SpiralSearch(predicted, cfg, 0.9 * float(self.v_max.min()))
                self._was_tracking = False
            if self.finder_result is not None:
                command = np.array(self.finder_result) - self.camera      # slew to the finder's target
            else:
                command = self.search.next_point() - self.camera
        command = np.clip(command, -self.v_max, self.v_max)
        drift = self.platform.step()
        new_camera = self.camera + command + drift
        clipped = np.clip(new_camera, self.cam_min, self.cam_max)
        if cfg.platform_motion == "linear" and (clipped != new_camera).any():
            self.platform.bounce(clipped != new_camera)
        moved = clipped - self.camera
        self.camera = clipped
        # The tracker knows the commanded pan / tilt (encoder feedback) but
        # not the platform disturbance or the jitter.
        self.tracker.compensate_camera_motion(*(moved - drift))

        # 4. Truth and bookkeeping.
        self.scene.step(self.dt)
        self.frame_index += 1
        frame_ms = (time.perf_counter() - frame_started) * 1000.0
        self.metrics.add_scenario_frame(result, frame_ms, tuple(self.scene.motion.pos), tuple(origin + half), truth,
                                        visible)
        self.last_command = command
        self.last_frame, self.last_result, self.last_truth, self.last_origin = frame, result, truth, origin
        return result


# ======================================================================
# Metrics
# ======================================================================

class ScenarioMetrics(BenchmarkMetrics):
    """BenchmarkMetrics plus pointing (tracking) error and camera state per frame."""

    def __init__(self, fps, width, height, fov_deg, ground_truth):
        super().__init__(fps, width, height, fov_deg, ground_truth)
        self.extra_columns = ["true_screen_x_px", "true_screen_y_px", "camera_center_x_px", "camera_center_y_px",
                              "pan_deg", "tilt_deg", "beacon_in_fov", "tracking_error_px"]
        self.extra_rows = []
        self.tracking_errors = []            # (frame, error px) while the beacon is visible
        self.screen_size = None

    def add_scenario_frame(self, result, frame_ms, true_screen, camera_center, truth_cam, visible):
        in_fov = (truth_cam is not None and 0 <= truth_cam[0] < self.width and 0 <= truth_cam[1] < self.height)
        tracking_error = (math.hypot(truth_cam[0] - self.width / 2.0, truth_cam[1] - self.height / 2.0)
                          if truth_cam is not None else None)
        if tracking_error is not None:
            self.tracking_errors.append((result.frame, tracking_error))
        sw, sh = self.screen_size or (0, 0)
        px_x, px_y = self.width / self.fov_deg[0], self.height / self.fov_deg[1]
        self.extra_rows.append([
            f"{true_screen[0]:.3f}", f"{true_screen[1]:.3f}", f"{camera_center[0]:.3f}", f"{camera_center[1]:.3f}",
            f"{(camera_center[0] - sw / 2) / px_x:.4f}", f"{(camera_center[1] - sh / 2) / px_y:.4f}",
            int(in_fov), "" if tracking_error is None else f"{tracking_error:.3f}",
        ])
        self.add(result, frame_ms)
        if in_fov and self.live.get("first_in_fov") is None:
            self.live["first_in_fov"] = result.frame

    def summary(self):
        s = super().summary()
        first_lock = s.get("first_lock_frame")
        s["first_in_fov_frame"] = self.live.get("first_in_fov")
        s["acquisition_from_fov_s"] = ((first_lock - s["first_in_fov_frame"]) / self.fps
                                       if first_lock is not None and s["first_in_fov_frame"] is not None else None)
        s["search_time_s"] = (s["first_in_fov_frame"] / self.fps if s["first_in_fov_frame"] is not None else None)
        errors = np.array([e for frame, e in self.tracking_errors if first_lock is not None and frame >= first_lock])
        s["tracking_error_samples"] = int(errors.size)
        if errors.size:
            s["tracking_error_mean_px"] = float(errors.mean())
            s["tracking_error_rms_px"] = float(np.sqrt(np.mean(errors ** 2)))
            s["tracking_error_p95_px"] = float(np.percentile(errors, 95))
            s["tracking_error_max_px"] = float(errors.max())
            s["tracking_within_10px_pct"] = float((errors <= 10.0).mean() * 100)
        return s
