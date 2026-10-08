import numpy as np
import cv2
import math
import time
import random
from sim.sky import render_sky

BEACON_REAL_DIAMETER_M = 0.2
FOCAL_LENGTH_PX = 800

class VirtualScene:
    def __init__(self, width=640, height=480, pattern="circular", sky_mode="dusk", n_decoys=4):
        self.width, self.height = width, height
        self.pattern = pattern
        self.sky_mode = sky_mode
        self.t = 0
        self._last_time = time.time()
        self.angular_speed = 0.4
        self.n_decoys = n_decoys
        self._decoys = self._generate_decoys()

        self.available_patterns = ["circular", "figure8", "straight", "random"]
        self.pattern_switch_timer = random.uniform(4, 9)
        self.transition_time = 1.0
        self._transitioning = False
        self._transition_elapsed = 0.0
        self._transition_start_pos = None
        self._last_raw_pos = (width // 2, height // 2)
        self.auto_rotate = False   # only rotates through patterns when explicitly enabled

        self._rx, self._ry = float(width // 2), float(height // 2)
        self._vx, self._vy = 0.0, 0.0

        self._straight_x = float(width // 2)
        self._straight_dir = 1

    def _generate_decoys(self):
        decoys = []
        for _ in range(self.n_decoys):
            decoys.append({
                "x": random.uniform(20, self.width - 20),
                "y": random.uniform(20, self.height - 20),
                "vx": random.uniform(-15, 15),
                "vy": random.uniform(-15, 15),
                "brightness": random.randint(120, 255),
                "size": random.choice([2, 3, 4, 5, 8]),
            })
        return decoys

    def regenerate_decoys(self, n_decoys=None):
        if n_decoys is not None:
            self.n_decoys = n_decoys
        self._decoys = self._generate_decoys()

    def _update_decoys(self, dt):
        margin = 15
        for d in self._decoys:
            d["x"] += d["vx"] * dt
            d["y"] += d["vy"] * dt
            if d["x"] < margin or d["x"] > self.width - margin:
                d["vx"] *= -1
            if d["y"] < margin or d["y"] > self.height - margin:
                d["vy"] *= -1
            d["x"] = float(np.clip(d["x"], margin, self.width - margin))
            d["y"] = float(np.clip(d["y"], margin, self.height - margin))

    def _get_dt(self):
        now = time.time()
        dt = min(now - self._last_time, 0.1)
        self._last_time = now
        self._last_dt = dt
        return dt

    def _raw_pattern_position(self, dt):
        cx, cy = self.width // 2, self.height // 2
        if self.pattern == "circular":
            r = 150
            self.t += self.angular_speed * dt
            x = cx + r * math.cos(self.t)
            y = cy + r * math.sin(self.t)
        elif self.pattern == "figure8":
            self.t += self.angular_speed * dt
            x = cx + 150 * math.sin(self.t)
            y = cy + 75 * math.sin(2 * self.t)
        elif self.pattern == "straight":
            speed = 60
            self._straight_x += speed * dt * self._straight_dir
            margin = 20
            if self._straight_x < margin or self._straight_x > self.width - margin:
                self._straight_dir *= -1
                self._straight_x = float(np.clip(self._straight_x, margin, self.width - margin))
            x, y = self._straight_x, cy
        elif self.pattern == "random":
            accel_per_sec = 40.0
            max_speed = 300.0
            self._vx += np.random.uniform(-accel_per_sec, accel_per_sec) * dt
            self._vy += np.random.uniform(-accel_per_sec, accel_per_sec) * dt
            speed = math.hypot(self._vx, self._vy)
            if speed > max_speed:
                self._vx *= max_speed / speed
                self._vy *= max_speed / speed
            self._rx += self._vx * dt
            self._ry += self._vy * dt
            margin = 20
            if self._rx < margin or self._rx > self.width - margin:
                self._vx *= -1
            if self._ry < margin or self._ry > self.height - margin:
                self._vy *= -1
            self._rx = np.clip(self._rx, margin, self.width - margin)
            self._ry = np.clip(self._ry, margin, self.height - margin)
            x, y = self._rx, self._ry
        else:
            x, y = cx, cy
        return float(x), float(y)

    def set_pattern(self, new_pattern):
        """Manually switch patterns (e.g. from the dropdown). Blends smoothly
        from the beacon's current position into the new pattern's trajectory,
        instead of teleporting — same mechanism the automatic timer uses."""
        if new_pattern == self.pattern or new_pattern not in self.available_patterns:
            return
        self.pattern = new_pattern
        self._transition_start_pos = self._last_raw_pos
        self._transitioning = True
        self._transition_elapsed = 0.0
        self.pattern_switch_timer = random.uniform(4, 9)

    def enable_auto_rotate(self):
        self.auto_rotate = True
        self.pattern_switch_timer = random.uniform(4, 9)

    def disable_auto_rotate(self):
        self.auto_rotate = False

    def get_beacon_position(self, dt):
        self.pattern_switch_timer -= dt
        if self.auto_rotate and self.pattern_switch_timer <= 0 and not self._transitioning:
            choices = [p for p in self.available_patterns if p != self.pattern]
            self.pattern = random.choice(choices)
            self._transition_start_pos = self._last_raw_pos
            self._transitioning = True
            self._transition_elapsed = 0.0
            self.pattern_switch_timer = random.uniform(4, 9)

        raw_x, raw_y = self._raw_pattern_position(dt)
        self._last_raw_pos = (raw_x, raw_y)

        if self._transitioning:
            self._transition_elapsed += dt
            frac = min(self._transition_elapsed / self.transition_time, 1.0)
            bx = self._transition_start_pos[0] + (raw_x - self._transition_start_pos[0]) * frac
            by = self._transition_start_pos[1] + (raw_y - self._transition_start_pos[1]) * frac
            if frac >= 1.0:
                self._transitioning = False
            return int(bx), int(by)
        return int(raw_x), int(raw_y)

    def get_beacon_scale(self, dt):
        if not hasattr(self, "_scale"):
            self._scale = 1.0
            self._scale_v = 0.0
        scale_accel = 0.12
        max_scale_speed = 0.45
        min_scale, max_scale = 0.5, 1.8
        self._scale_v += np.random.uniform(-scale_accel, scale_accel) * dt
        self._scale_v = float(np.clip(self._scale_v, -max_scale_speed, max_scale_speed))
        self._scale += self._scale_v * dt
        if self._scale < min_scale or self._scale > max_scale:
            self._scale_v *= -1
        self._scale = float(np.clip(self._scale, min_scale, max_scale))
        return self._scale

    def estimate_distance_m(self):
        if not hasattr(self, "last_core_diameter_px") or self.last_core_diameter_px <= 0:
            return None
        return (BEACON_REAL_DIAMETER_M * FOCAL_LENGTH_PX) / self.last_core_diameter_px

    def render(self):
        dt = self._get_dt()
        self._update_decoys(dt)
        frame = render_sky(self.width, self.height, mode=self.sky_mode).copy()

        for d in self._decoys:
            cv2.circle(frame, (int(d["x"]), int(d["y"])), d["size"],
                       (d["brightness"], d["brightness"], d["brightness"]), -1)

        x, y = self.get_beacon_position(dt)
        scale = self.get_beacon_scale(dt)
        self.last_scale = scale
        core_r = max(1, round(4 * scale))
        halo1_r = max(core_r + 1, round(8 * scale))
        halo2_r = max(halo1_r + 1, round(14 * scale))
        self.last_core_diameter_px = core_r * 2
        cv2.circle(frame, (x, y), core_r, (255, 255, 255), -1)
        cv2.circle(frame, (x, y), halo1_r, (200, 200, 200), 1)
        cv2.circle(frame, (x, y), halo2_r, (80, 80, 80), 1)
        return frame, (x, y)