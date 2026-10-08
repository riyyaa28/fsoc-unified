"""Functional disturbance model shared by the 3D simulation and video benchmark.

Every effect here changes real data that the detector consumes:

* line-of-sight disturbances (camera jitter, platform motion) displace the
  camera's actual pointing, so the rendered beacon moves in the image and the
  ground-truth pointing error changes;
* atmospheric conditions change contrast, brightness, visibility and blur of
  the camera frame and attenuate the beacon signal;
* sensor noise (Gaussian, salt & pepper, Poisson shot noise) is added to the
  final 8-bit frame.

The original ``disturbance.effects``/``DisturbanceManager`` used by the
desktop dashboard are intentionally left untouched.
"""

from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

ATMOSPHERES = ("clear", "haze", "fog", "rain", "low_light")
PLATFORM_PATTERNS = ("none", "linear", "circular", "random", "spiral", "figure8")

DEFAULT_DISTURBANCES: dict[str, Any] = {
    "noise": {"gaussian": 0.0, "salt_pepper": 0.0, "poisson": 0.0},   # each 0..1
    "jitter": {"amplitude_px": 0.0},                                   # 0..20 px, per frame
    "platform": {"pattern": "none", "amplitude_px": 0.0, "period_s": 4.0},
    "atmosphere": {"condition": "clear", "intensity": 0.5},            # intensity 0..1
    "turbulence": {"strength": 0.0},                                   # scintillation 0..1
}


def _f(value: Any, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(max(number, low), high)


def sanitize(config: dict[str, Any]) -> dict[str, Any]:
    """Clamp a (possibly partial / legacy) disturbance dict to valid values."""
    out = {key: dict(value) for key, value in DEFAULT_DISTURBANCES.items()}
    noise = config.get("noise") if isinstance(config.get("noise"), dict) else {}
    for key in out["noise"]:
        out["noise"][key] = _f(noise.get(key, 0.0), 0.0, 1.0)
    jitter = config.get("jitter") if isinstance(config.get("jitter"), dict) else {}
    out["jitter"]["amplitude_px"] = _f(jitter.get("amplitude_px", 0.0), 0.0, 20.0)
    platform = config.get("platform") if isinstance(config.get("platform"), dict) else {}
    pattern = str(platform.get("pattern", "none"))
    out["platform"] = {
        "pattern": pattern if pattern in PLATFORM_PATTERNS else "none",
        "amplitude_px": _f(platform.get("amplitude_px", 0.0), 0.0, 20.0),
        "period_s": _f(platform.get("period_s", 4.0), 0.5, 20.0, 4.0),
    }
    atmosphere = config.get("atmosphere") if isinstance(config.get("atmosphere"), dict) else {}
    condition = str(atmosphere.get("condition", "clear"))
    out["atmosphere"] = {
        "condition": condition if condition in ATMOSPHERES else "clear",
        "intensity": _f(atmosphere.get("intensity", 0.5), 0.0, 1.0, 0.5),
    }
    turbulence = config.get("turbulence") if isinstance(config.get("turbulence"), dict) else {}
    out["turbulence"]["strength"] = _f(turbulence.get("strength", 0.0), 0.0, 1.0)
    return out


def active_labels(d: dict[str, Any]) -> list[str]:
    """Human-readable list of disturbances that currently change the data."""
    labels: list[str] = []
    names = {"gaussian": "Gaussian noise", "salt_pepper": "Salt & pepper noise", "poisson": "Poisson noise"}
    for key, name in names.items():
        if d["noise"][key] > 0:
            labels.append(f"{name} {d['noise'][key]:.2f}")
    if d["jitter"]["amplitude_px"] > 0:
        labels.append(f"Camera jitter ±{d['jitter']['amplitude_px']:.0f}px")
    if d["platform"]["pattern"] != "none" and d["platform"]["amplitude_px"] > 0:
        labels.append(f"Platform {d['platform']['pattern']} ±{d['platform']['amplitude_px']:.0f}px")
    if d["atmosphere"]["condition"] != "clear" and d["atmosphere"]["intensity"] > 0:
        labels.append(f"{d['atmosphere']['condition'].replace('_', ' ').title()} {d['atmosphere']['intensity']:.2f}")
    if d["turbulence"]["strength"] > 0:
        labels.append(f"Turbulence {d['turbulence']['strength']:.2f}")
    return labels


class DisturbanceModel:
    """Stateful disturbance generator (random-walk / phase state lives here)."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = np.random.default_rng(seed)
        self.config = sanitize({})
        self._walk = np.zeros(2)
        self._walk_v = np.zeros(2)
        self._rain_cache: dict[tuple[int, int, int], np.ndarray] = {}

    def configure(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = sanitize(config)
        return self.config

    # ------------------------------------------------------------------
    # Line-of-sight disturbances (camera pixels)
    # ------------------------------------------------------------------
    def platform_offset(self, t: float, dt: float) -> tuple[float, float]:
        p = self.config["platform"]
        amp, period, pattern = p["amplitude_px"], p["period_s"], p["pattern"]
        if pattern == "none" or amp <= 0:
            return 0.0, 0.0
        w = 2 * math.pi / period
        if pattern == "linear":
            # Constant-rate sway: triangle wave between -amp and +amp.
            phase = (t / period) % 1.0
            tri = 4 * phase - 1 if phase < 0.5 else 3 - 4 * phase
            return amp * tri, 0.35 * amp * tri
        if pattern == "circular":
            return amp * math.cos(w * t), amp * math.sin(w * t)
        if pattern == "spiral":
            r = amp * (0.3 + 0.7 * (0.5 + 0.5 * math.sin(0.25 * w * t)))
            return r * math.cos(w * t), r * math.sin(w * t)
        if pattern == "figure8":
            return amp * math.sin(w * t), amp * 0.5 * math.sin(2 * w * t)
        # random: bounded, smooth random walk (Ornstein-Uhlenbeck style)
        self._walk_v += self.rng.normal(0.0, amp * 3.0, 2) * dt
        self._walk_v *= 0.96
        self._walk += self._walk_v * dt
        norm = float(np.linalg.norm(self._walk))
        if norm > amp:
            self._walk *= amp / norm
            self._walk_v *= -0.5
        return float(self._walk[0]), float(self._walk[1])

    def jitter_offset(self) -> tuple[float, float]:
        amp = self.config["jitter"]["amplitude_px"]
        if amp <= 0:
            return 0.0, 0.0
        dx, dy = self.rng.uniform(-amp, amp, 2)
        return float(dx), float(dy)

    # ------------------------------------------------------------------
    # Beacon signal (turbulence scintillation + atmospheric attenuation)
    # ------------------------------------------------------------------
    def beacon_signal(self) -> tuple[float, tuple[float, float], float]:
        """Return (intensity gain, wander offset px, blur sigma px) for a beacon."""
        a = self.config["atmosphere"]
        k = a["intensity"] if a["condition"] != "clear" else 0.0
        gain = {"clear": 1.0, "haze": 1.0 - 0.30 * k, "fog": 1.0 - 0.60 * k, "rain": 1.0 - 0.35 * k, "low_light": 1.0 - 0.15 * k}[a["condition"]]
        s = self.config["turbulence"]["strength"]
        wander = (0.0, 0.0)
        blur = 0.0
        if s > 0:
            gain *= float(np.clip(self.rng.lognormal(0.0, 0.45 * s), 0.25, 1.6))
            wander = tuple(float(v) for v in self.rng.normal(0.0, 2.5 * s, 2))
            blur = 1.4 * s
        return float(np.clip(gain, 0.0, 1.6)), wander, blur

    # ------------------------------------------------------------------
    # Whole-frame effects
    # ------------------------------------------------------------------
    def _rain_layer(self, h: int, w: int, k: float) -> np.ndarray:
        """Cached pool of streak layers, randomly rolled per frame (fast)."""
        key = (h, w, int(round(k * 20)))
        pool = self._rain_cache.get(key)
        if pool is None:
            pool = [self._make_rain(h, w, k) for _ in range(6)]
            self._rain_cache = {key: pool}
        layer = pool[int(self.rng.integers(0, len(pool)))]
        return np.roll(layer, (int(self.rng.integers(0, h)), int(self.rng.integers(0, w))), axis=(0, 1))

    def _make_rain(self, h: int, w: int, k: float) -> np.ndarray:
        streaks = np.zeros((h, w), np.uint8)
        count = int(80 + 900 * k)
        xs = self.rng.integers(0, w, count)
        ys = self.rng.integers(0, h, count)
        lengths = self.rng.integers(int(6 + 10 * k), int(14 + 26 * k), count)
        for x, y, length in zip(xs, ys, lengths):
            cv2.line(streaks, (int(x), int(y)), (int(x) - int(length) // 5, int(y) + int(length)), 255, 1, cv2.LINE_AA)
        return streaks

    def apply_atmosphere(self, frame: np.ndarray) -> np.ndarray:
        a = self.config["atmosphere"]
        k, cond = a["intensity"], a["condition"]
        if cond == "clear" or k <= 0:
            return frame
        img = frame.astype(np.float32)
        if cond == "haze":
            t = 1.0 - 0.55 * k                         # transmission
            img = img * t + 175.0 * (1 - t)            # airlight veil
            img = cv2.GaussianBlur(img, (0, 0), 0.4 + 1.0 * k)
        elif cond == "fog":
            t = 1.0 - 0.72 * k
            img = img * t + 205.0 * (1 - t)
            img = cv2.GaussianBlur(img, (0, 0), 0.6 + 1.5 * k)
        elif cond == "rain":
            t = 1.0 - 0.25 * k
            img = img * t + 150.0 * (1 - t)
            img = cv2.GaussianBlur(img, (0, 0), 0.5 + 0.9 * k)
            streaks = self._rain_layer(img.shape[0], img.shape[1], k).astype(np.float32)[..., None] / 255.0
            img = img * (1 - 0.55 * streaks) + 190.0 * 0.55 * streaks
        elif cond == "low_light":
            gain = 1.0 - 0.88 * k
            img = 255.0 * np.power(np.clip(img / 255.0, 0, 1), 1.0 + 0.8 * k) * gain
            # Dark frames need more amplifier gain -> more read noise.
            sigma = 4.0 + 14.0 * k
            noise = np.empty(img.shape[:2], np.float32)
            cv2.randn(noise, 0.0, sigma)
            img = img + noise[..., None]
        return np.clip(img, 0, 255).astype(np.uint8)

    def apply_noise(self, frame: np.ndarray) -> np.ndarray:
        n = self.config["noise"]
        out = frame
        if n["poisson"] > 0:
            # Shot noise: variance proportional to signal (Poisson statistics).
            # Normal approximation of the Poisson distribution, used because
            # exact per-pixel Poisson sampling cost ~30 ms/frame at 640x480.
            # Higher setting = fewer photons per grey level = more noise.
            photons = 255.0 / (1.0 + 400.0 * n["poisson"])    # full-well photons
            signal = out.astype(np.float32)
            noise = np.empty(signal.shape, np.float32)
            cv2.randn(noise, 0.0, 1.0)
            out = np.clip(signal + noise * np.sqrt(signal * (255.0 / photons)), 0, 255).astype(np.uint8)
        if n["gaussian"] > 0:
            sigma = 90.0 * n["gaussian"]                    # 0..90 grey levels
            noise = np.empty(out.shape, np.int16)
            cv2.randn(noise, 0, sigma)
            out = np.clip(out.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        if n["salt_pepper"] > 0:
            density = 0.35 * n["salt_pepper"]               # up to 35 % of pixels
            h, w = out.shape[:2]
            mask = self.rng.random((h, w))
            out = out.copy()
            out[mask < density / 2] = 0
            out[(mask >= density / 2) & (mask < density)] = 255
        return out

    def apply_frame(self, frame: np.ndarray) -> np.ndarray:
        return self.apply_noise(self.apply_atmosphere(frame))

    def any_image_effect(self) -> bool:
        c = self.config
        return any(v > 0 for v in c["noise"].values()) or (c["atmosphere"]["condition"] != "clear" and c["atmosphere"]["intensity"] > 0)

    def needs_median(self) -> bool:
        return self.config["noise"]["salt_pepper"] > 0
