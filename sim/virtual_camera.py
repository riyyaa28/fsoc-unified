import numpy as np


class VirtualPTZCamera:
    def __init__(self, frame_w=640, frame_h=480, fov_w=200, fov_h=150):
        self.frame_w, self.frame_h = frame_w, frame_h
        self.fov_w, self.fov_h = fov_w, fov_h
        self.pan_x, self.tilt_y = frame_w // 2, frame_h // 2

    def apply_delta(self, dx, dy):
        self.pan_x = int(
            np.clip(self.pan_x + dx, self.fov_w // 2, self.frame_w - self.fov_w // 2)
        )
        self.tilt_y = int(
            np.clip(self.tilt_y + dy, self.fov_h // 2, self.frame_h - self.fov_h // 2)
        )

    def crop(self, full_frame):
        x0 = self.pan_x - self.fov_w // 2
        y0 = self.tilt_y - self.fov_h // 2
        return full_frame[y0 : y0 + self.fov_h, x0 : x0 + self.fov_w]
