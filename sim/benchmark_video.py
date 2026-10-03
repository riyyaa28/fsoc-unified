"""Generate Benchmark-2 style test videos with exact ground truth.

Each video is a 30 fps .mp4 covering the whole screen: a dark monochrome
background, a moving square beacon spot and user-selected image noise
(salt & pepper, Gaussian, Poisson), optional camera jitter and optional
occlusion intervals for re-acquisition tests. A CSV beside the video gives
the true beacon centroid for every frame (frame, time_s, x, y, visible).

The beacon is rendered with exact area coverage, so its centroid can sit
between pixels and the ground truth is sub-pixel accurate. Pixel centres are
at integer coordinates (the same convention the tracker reports in).

Usage:
    python -m sim.benchmark_video out.mp4 --size 1920x1080 --pattern figure8 \
        --noise sp,gaussian,poisson --occlude 4.0:5.0
"""

import argparse
import csv
import math
import os

import cv2
import numpy as np


def _coverage(center, size, count):
    """Fraction of each pixel [i-0.5, i+0.5] covered by [center-size/2, center+size/2]."""
    edges = np.arange(count, dtype=np.float64)
    lo = np.maximum(edges - 0.5, center - size / 2.0)
    hi = np.minimum(edges + 0.5, center + size / 2.0)
    return np.clip(hi - lo, 0.0, 1.0)


def beacon_path(pattern, t, width, height, speed=1.0):
    cx, cy = width / 2.0, height / 2.0
    rx, ry = width * 0.32, height * 0.32
    w = 0.35 * speed
    if pattern == "circular":
        return cx + rx * math.cos(w * t), cy + ry * math.sin(w * t)
    if pattern == "figure8":
        return cx + rx * math.sin(w * t), cy + ry * 0.8 * math.sin(2 * w * t)
    if pattern == "straight":
        # Back and forth along a diagonal.
        phase = (w * t / math.pi) % 2.0
        u = phase if phase <= 1.0 else 2.0 - phase
        return width * (0.15 + 0.7 * u), height * (0.2 + 0.6 * u)
    if pattern == "random":
        # Smooth pseudo-random wander (sum of incommensurate sinusoids).
        x = cx + rx * (0.6 * math.sin(0.41 * w * t + 1.3) + 0.4 * math.sin(1.07 * w * t + 0.2))
        y = cy + ry * (0.6 * math.sin(0.53 * w * t + 2.1) + 0.4 * math.sin(0.89 * w * t + 4.0))
        return x, y
    raise ValueError(f"unknown pattern {pattern!r}")


def generate(path, width=1920, height=1080, fps=30, seconds=10.0, pattern="figure8",
             beacon_size=10, beacon_level=200, background=40, noise=("sp", "gaussian", "poisson"),
             sp_fraction=0.10, gaussian_sigma=20.0, poisson_peak=60.0, jitter=0,
             occlusions=(), speed=1.0, seed=1):
    rng = np.random.default_rng(seed)
    frames = int(round(seconds * fps))
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height), False)
    if not writer.isOpened():
        raise RuntimeError(f"could not open video writer for {path}")
    gt_path = os.path.splitext(path)[0] + "_gt.csv"
    # Gentle vignette so the background is not perfectly flat.
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    base = background * (1.0 - 0.25 * (((xx - width / 2) / width) ** 2 + ((yy - height / 2) / height) ** 2))
    with open(gt_path, "w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(["frame", "time_s", "x", "y", "visible"])
        for index in range(frames):
            t = index / fps
            x, y = beacon_path(pattern, t, width, height, speed)
            shift_x = shift_y = 0
            if jitter:
                shift_x, shift_y = (int(v) for v in rng.integers(-jitter, jitter + 1, 2))
                x, y = x + shift_x, y + shift_y
            visible = not any(start <= t < end for start, end in occlusions)
            frame = base.copy()
            if visible:
                cov_x = _coverage(x, beacon_size, width)
                cov_y = _coverage(y, beacon_size, height)
                xs, ys = np.nonzero(cov_x)[0], np.nonzero(cov_y)[0]
                if xs.size and ys.size:
                    patch = np.outer(cov_y[ys], cov_x[xs])
                    region = frame[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
                    region += patch * (beacon_level - region)
            if "poisson" in noise:
                frame = rng.poisson(np.clip(frame, 0, None) * (poisson_peak / 255.0)) * (255.0 / poisson_peak)
            if "gaussian" in noise:
                frame = frame + rng.normal(0.0, gaussian_sigma, frame.shape)
            frame = np.clip(frame, 0, 255).astype(np.uint8)
            if "sp" in noise and sp_fraction > 0:
                mask = rng.random(frame.shape)
                frame[mask < sp_fraction / 2] = 0
                frame[mask > 1 - sp_fraction / 2] = 255
            writer.write(frame)
            out.writerow([index, f"{t:.4f}", f"{x:.3f}" if visible else "", f"{y:.3f}" if visible else "",
                          int(visible)])
    writer.release()
    return gt_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--size", default="1920x1080", help="WIDTHxHEIGHT (default 1920x1080)")
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--pattern", default="figure8", choices=["circular", "figure8", "straight", "random"])
    parser.add_argument("--speed", type=float, default=1.0, help="motion speed multiplier")
    parser.add_argument("--beacon-size", type=int, default=10, help="square side in px (5-20)")
    parser.add_argument("--beacon-level", type=int, default=200)
    parser.add_argument("--background", type=int, default=40)
    parser.add_argument("--noise", default="sp,gaussian,poisson", help="comma list of sp,gaussian,poisson or none")
    parser.add_argument("--sp", type=float, default=0.10, help="salt & pepper fraction of pixels")
    parser.add_argument("--sigma", type=float, default=20.0, help="Gaussian noise standard deviation")
    parser.add_argument("--poisson-peak", type=float, default=60.0, help="photons at full white")
    parser.add_argument("--jitter", type=int, default=0, help="camera jitter, +/- px per frame")
    parser.add_argument("--occlude", action="append", default=[], help="START:END seconds with the beacon hidden")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    width, height = (int(v) for v in args.size.lower().split("x"))
    noise = () if args.noise == "none" else tuple(n.strip() for n in args.noise.split(","))
    occlusions = [tuple(float(v) for v in item.split(":")) for item in args.occlude]
    gt = generate(args.output, width, height, args.fps, args.seconds, args.pattern, args.beacon_size,
                  args.beacon_level, args.background, noise, args.sp, args.sigma, args.poisson_peak,
                  args.jitter, occlusions, args.speed, args.seed)
    print(f"video: {args.output}\nground truth: {gt}")


if __name__ == "__main__":
    main()
