import numpy as np
import cv2


def add_gaussian_noise(frame, sigma=15):
    noise = np.random.normal(0, sigma, frame.shape).astype(np.int16)
    noisy = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return noisy


def add_poisson_noise(frame, peak=20.0):
    """Photon shot noise: each pixel's brightness is treated as an expected
    photon count (scaled so white == ``peak`` photons) and resampled from a
    Poisson distribution. Lower ``peak`` means fewer photons and more noise;
    unlike Gaussian noise, it grows with signal brightness (variance == mean)."""
    photons = frame.astype(np.float32) * (peak / 255.0)
    noisy = np.random.poisson(photons).astype(np.float32) * (255.0 / peak)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def add_fog(frame, intensity=0.3):
    fog_layer = np.full(frame.shape, 200, dtype=np.uint8)
    return cv2.addWeighted(frame, 1 - intensity, fog_layer, intensity, 0)


def add_jitter(frame, max_shift=5):
    dx, dy = np.random.randint(-max_shift, max_shift, 2)
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(frame, M, (frame.shape[1], frame.shape[0]))


def add_rain(frame, intensity=0.3):
    """Streaks of light diagonal lines simulating rain. intensity: 0.0-1.0."""
    h, w = frame.shape[:2]
    rain_layer = frame.copy()
    num_drops = int(intensity * 200)
    for _ in range(num_drops):
        x = np.random.randint(0, w)
        y = np.random.randint(0, h)
        length = np.random.randint(5, 15)
        cv2.line(rain_layer, (x, y), (x, y + length), (180, 180, 180), 1)
    return cv2.addWeighted(frame, 1 - intensity * 0.5, rain_layer, intensity * 0.5, 0)
