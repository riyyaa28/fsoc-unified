import numpy as np
import random

SKY_GRADIENTS = {
    "day": [(0.0, (235, 206, 135)), (1.0, (235, 206, 135))],
    "dusk": [(0.0, (235, 206, 135)), (0.5, (128, 0, 0)), (1.0, (10, 5, 0))],
    "night": [(0.0, (30, 15, 0)), (1.0, (0, 0, 0))],
}


def render_sky(width, height, mode="dusk"):
    stops = SKY_GRADIENTS[mode]
    positions = np.array([s[0] for s in stops])
    colors = np.array([s[1] for s in stops], dtype=np.float32)
    ys = np.linspace(0, 1, height)
    idx = np.clip(
        np.searchsorted(positions, ys, side="right") - 1, 0, len(positions) - 2
    )
    p0, p1 = positions[idx], positions[idx + 1]
    c0, c1 = colors[idx], colors[idx + 1]
    local_t = ((ys - p0) / (p1 - p0 + 1e-9))[:, None]
    row_colors = c0 + (c1 - c0) * local_t
    sky = np.tile(row_colors[:, None, :], (1, width, 1)).astype(np.uint8)
    return sky


def random_sky_mode():
    return random.choice(list(SKY_GRADIENTS.keys()))
