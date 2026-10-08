"""Deterministic beacon trajectories in field-of-regard ("world") pixels.

The original ``VirtualScene`` drives a single beacon from the wall clock.  The
web simulator needs several independently selectable beacons, a speed
multiplier and reproducible paths, so each beacon owns one ``Trajectory``
advanced with an explicit ``dt``.  Coordinates use the same 640x480 world
frame as ``VirtualScene`` so the rest of the pipeline is unchanged.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

PATTERNS = ("straight", "circular", "figure8", "random", "spiral", "sinusoidal")


@dataclass
class Trajectory:
    pattern: str = "circular"
    center: tuple[float, float] = (320.0, 240.0)
    size: float = 120.0          # characteristic radius / half-length (world px)
    speed: float = 1.0           # user multiplier (1.0 = nominal)
    phase: float = 0.0
    bounds: tuple[float, float] = (640.0, 480.0)
    margin: float = 18.0
    seed: int = 0
    t: float = 0.0
    _rng: random.Random = field(default_factory=random.Random, repr=False)
    _rw: list[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.pattern not in PATTERNS:
            self.pattern = "circular"
        self._rng = random.Random(self.seed)
        self._rw = [self.center[0], self.center[1], 0.0, 0.0]

    # Nominal angular rate 0.4 rad/s matches the supplied VirtualScene.
    @property
    def omega(self) -> float:
        return 0.4 * self.speed

    def reset_random_walk(self) -> None:
        self._rw = [self.center[0], self.center[1], 0.0, 0.0]

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        w, h = self.bounds
        return (min(max(x, self.margin), w - self.margin), min(max(y, self.margin), h - self.margin))

    def position(self) -> tuple[float, float]:
        cx, cy = self.center
        a, t = self.size, self.t
        th = self.omega * t + self.phase
        if self.pattern == "circular":
            x, y = cx + a * math.cos(th), cy + a * math.sin(th)
        elif self.pattern == "figure8":
            x, y = cx + a * math.sin(th), cy + 0.5 * a * math.sin(2 * th)
        elif self.pattern == "straight":
            # Constant-speed back-and-forth sweep (triangle wave), 60 px/s nominal.
            span = 2 * a
            s = (60.0 * self.speed * t + self.phase * a) % (2 * span)
            offset = s if s <= span else 2 * span - s
            x, y = cx - a + offset, cy
        elif self.pattern == "spiral":
            # Radius breathes between 25% and 100% of size while rotating.
            r = a * (0.625 + 0.375 * math.sin(0.25 * th))
            x, y = cx + r * math.cos(th), cy + r * math.sin(th)
        elif self.pattern == "sinusoidal":
            span = 2 * a
            s = (60.0 * self.speed * t + self.phase * a) % (2 * span)
            offset = s if s <= span else 2 * span - s
            x, y = cx - a + offset, cy + 0.45 * a * math.sin(2.2 * th)
        else:  # random walk: stored state, advanced in step()
            x, y = self._rw[0], self._rw[1]
        return self._clamp(x, y)

    def step(self, dt: float) -> tuple[float, float]:
        self.t += dt
        if self.pattern == "random":
            accel, vmax = 60.0 * self.speed, 90.0 * self.speed
            rw = self._rw
            rw[2] += self._rng.uniform(-accel, accel) * dt
            rw[3] += self._rng.uniform(-accel, accel) * dt
            speed = math.hypot(rw[2], rw[3])
            if speed > vmax:
                rw[2], rw[3] = rw[2] * vmax / speed, rw[3] * vmax / speed
            rw[0] += rw[2] * dt
            rw[1] += rw[3] * dt
            # Keep the walk near its assigned centre so it stays trackable.
            if abs(rw[0] - self.center[0]) > self.size * 1.3:
                rw[2] = -abs(rw[2]) if rw[0] > self.center[0] else abs(rw[2])
            if abs(rw[1] - self.center[1]) > self.size:
                rw[3] = -abs(rw[3]) if rw[1] > self.center[1] else abs(rw[3])
            w, h = self.bounds
            if not self.margin < rw[0] < w - self.margin:
                rw[2] *= -1
            if not self.margin < rw[1] < h - self.margin:
                rw[3] *= -1
            rw[0], rw[1] = self._clamp(rw[0], rw[1])
        return self.position()
