"""Render the virtual camera image at the configured resolution and FOV.

World (field-of-regard) coordinates are the supplied simulator's 640x480
"world pixels".  The camera sees a window of ``fov_world`` world pixels
centred on its actual line of sight and renders it directly at the configured
camera resolution, so camera FOV and resolution are real parameters and the
beacon is drawn at the configured size in *camera* pixels.
"""

from __future__ import annotations

import math
from typing import Iterable

import cv2
import numpy as np

from sim.sky import render_sky

WORLD_W, WORLD_H = 640, 480
_SKY = render_sky(WORLD_W, WORLD_H, mode="dusk")


def world_to_camera(point: tuple[float, float], los: tuple[float, float], fov_world: tuple[float, float], resolution: tuple[int, int]) -> tuple[float, float]:
    sx, sy = resolution[0] / fov_world[0], resolution[1] / fov_world[1]
    return (point[0] - (los[0] - fov_world[0] / 2)) * sx, (point[1] - (los[1] - fov_world[1] / 2)) * sy


def _circle(img: np.ndarray, center: tuple[float, float], radius: float, value: float, thickness: int) -> None:
    shift = 4
    c = (int(round(center[0] * 16)), int(round(center[1] * 16)))
    cv2.circle(img, c, max(1, int(round(radius * 16))), float(value), thickness, cv2.LINE_AA, shift)


def render_camera(
    los: tuple[float, float],
    fov_world: tuple[float, float],
    resolution: tuple[int, int],
    beacons: Iterable[dict],
    decoys: Iterable[dict],
    target_size_px: float,
) -> np.ndarray:
    """beacons: dicts with x, y, gain, wander (dx, dy), blur. decoys: x, y, radius_px, brightness."""
    w, h = int(resolution[0]), int(resolution[1])
    fw, fh = fov_world
    sx, sy = w / fw, h / fh
    x0, y0 = los[0] - fw / 2, los[1] - fh / 2
    # Camera pixel (u, v) samples world (x0 + u/sx, y0 + v/sy).
    matrix = np.float32([[1 / sx, 0, x0], [0, 1 / sy, y0]])
    frame = cv2.warpAffine(_SKY, matrix, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)

    for d in decoys:
        u, v = (d["x"] - x0) * sx, (d["y"] - y0) * sy
        r = d["radius_px"]
        if -r <= u < w + r and -r <= v < h + r:
            b = int(d["brightness"])
            cv2.circle(frame, (int(round(u * 16)), int(round(v * 16))), int(r * 16), (b, b, b), -1, cv2.LINE_AA, 4)

    layer = np.zeros((h, w), np.float32)
    drew = False
    r = max(1.0, target_size_px / 2.0)
    ring = max(1, int(round(target_size_px / 5.0)))   # 2 px rings at the 10 px default
    for b in beacons:
        u = (b["x"] - x0) * sx + b.get("wander", (0.0, 0.0))[0]
        v = (b["y"] - y0) * sy + b.get("wander", (0.0, 0.0))[1]
        if not (-4 * r <= u < w + 4 * r and -4 * r <= v < h + 4 * r):
            continue
        gain = float(b.get("gain", 1.0))
        single = np.zeros_like(layer)
        _circle(single, (u, v), r, 255.0 * gain, -1)
        _circle(single, (u, v), 2.0 * r, 200.0 * gain, ring)
        _circle(single, (u, v), 3.5 * r, 90.0 * gain, ring)
        blur = float(b.get("blur", 0.0))
        if blur > 0.2:
            single = cv2.GaussianBlur(single, (0, 0), blur)
        np.maximum(layer, single, out=layer)
        drew = True
    if drew:
        light = np.clip(layer, 0, 255).astype(np.uint8)
        frame = np.maximum(frame, cv2.merge([light, light, light]))
    return frame


def in_frame(point: tuple[float, float], resolution: tuple[int, int]) -> bool:
    return 0 <= point[0] < resolution[0] and 0 <= point[1] < resolution[1] and all(math.isfinite(v) for v in point)
