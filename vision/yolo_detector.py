from ultralytics import YOLO


class YoloBeaconDetector:
    def __init__(self, weights_path="beacon_yolo.pt", conf_threshold=0.25):
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
