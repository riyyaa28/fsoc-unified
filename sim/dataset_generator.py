import cv2
import numpy as np
import random
import os
from sim.sky import render_sky, SKY_GRADIENTS

def generate_frame_with_labels(width=640, height=480):
    mode = random.choice(list(SKY_GRADIENTS.keys()))  # day / dusk / night, randomized per image
    frame = render_sky(width, height, mode=mode).copy()

    # real beacon: bright core + two concentric halo rings — the shape IS the signal now
    bx, by = random.randint(50, width - 50), random.randint(50, height - 50)
    cv2.circle(frame, (bx, by), 4, (255, 255, 255), -1)     # bright core
    cv2.circle(frame, (bx, by), 8, (200, 200, 200), 1)      # inner halo
    cv2.circle(frame, (bx, by), 14, (80, 80, 80), 1)        # faint outer halo
    labels = [(0, bx, by, 30, 30)]  # class 0 = beacon; box sized to cover the full 14px halo radius

    # decoys: plain flat white/grey circles, deliberately NO halo rings — stars, glare, clouds, birds
    n_decoys = random.randint(2, 8)
    for _ in range(n_decoys):
        dx, dy = random.randint(20, width - 20), random.randint(20, height - 20)
        brightness = random.randint(120, 255)          # bright to faint white — can overlap the beacon's own brightness
        size = random.choice([2, 3, 4, 5, 8, 10])       # some overlap the beacon's core size (4) on purpose
        cv2.circle(frame, (dx, dy), size, (brightness, brightness, brightness), -1)
        labels.append((1, dx, dy, size * 2 + 2, size * 2 + 2))  # class 1 = not_beacon

    return frame, labels

def to_yolo_txt(labels, width, height):
    """Converts pixel-space labels to YOLO's normalized (class cx cy w h) format."""
    lines = []
    for cls, cx, cy, w, h in labels:
        lines.append(f"{cls} {cx/width:.6f} {cy/height:.6f} {w/width:.6f} {h/height:.6f}")
    return "\n".join(lines)

def build_dataset(n=1500, out_dir="dataset"):
    for split, count in [("train", int(n * 0.85)), ("val", int(n * 0.15))]:
        img_dir = f"{out_dir}/images/{split}"
        lbl_dir = f"{out_dir}/labels/{split}"
        os.makedirs(img_dir, exist_ok=True)
        os.makedirs(lbl_dir, exist_ok=True)
        for i in range(count):
            frame, labels = generate_frame_with_labels()
            cv2.imwrite(f"{img_dir}/{i:05d}.png", frame)
            with open(f"{lbl_dir}/{i:05d}.txt", "w") as f:
                f.write(to_yolo_txt(labels, 640, 480))
    print(f"Dataset written to {out_dir}/")

if __name__ == "__main__":
    build_dataset(n=100)