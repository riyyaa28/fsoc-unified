import os
import sys

import torch
from ultralytics import YOLO

# Bundled files live beside main.py (source) or in PyInstaller's _MEIPASS.
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# YOLO runs beside the real-time tracking loop (on a worker thread in the
# GUI); two threads keep it from taking every CPU core away from that loop.
torch.set_num_threads(2)


class YoloBeaconDetector:
    def __init__(self, weights_path="beacon_yolo.pt", conf_threshold=0.25):
        # A relative path means the bundled model, wherever the app is started from.
        if not os.path.isabs(weights_path):
            weights_path = os.path.join(APP_DIR, weights_path)
        self.model = YOLO(weights_path)
        self.conf_threshold = conf_threshold

    def detect(self, frame):
        results = self.model.predict(
            frame, imgsz=320, verbose=False, conf=self.conf_threshold
        )[0]
        best_box, best_conf = None, 0.0
        for box in results.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            if cls_id == 0 and conf > best_conf:
                x1, y1, x2, y2 = box.xyxy[0]
                best_box = (int((x1 + x2) / 2), int((y1 + y2) / 2))
                best_conf = conf
        if best_box is None:
            return None, 0.0
        return best_box, best_conf

