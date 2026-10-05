import cv2
import numpy as np


class BeaconKalmanTracker:
    """
    Constant-velocity Kalman tracker in WORLD coordinates.

    Important for a PTZ camera: detections are measured in the moving
    camera image, but the camera itself moves. Tracking in image/local
    coordinates makes the target appear almost stationary whenever PTZ
    keeps it centered, so there is little/no velocity to predict.

    This tracker therefore receives world-frame measurements and predicts
    the beacon's future WORLD position while it is hidden.
    """

    def __init__(self, init_offset=(0, 0), max_coast_frames=None):
        self.kf = cv2.KalmanFilter(4, 2)

        self.kf.measurementMatrix = np.array(
            [[1, 0, 0, 0],
             [0, 1, 0, 0]],
            dtype=np.float32,
        )

        # State = [world_x, world_y, vx, vy].
        # One update corresponds to one dashboard frame.
        self.kf.transitionMatrix = np.array(
            [[1, 0, 1, 0],
             [0, 1, 0, 1],
             [0, 0, 1, 0],
             [0, 0, 0, 1]],
            dtype=np.float32,
        )

        self.kf.processNoiseCov = np.eye(4, dtype=np.float32) * 0.03
        self.kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 1.0

        self.init_offset = init_offset
        self.initialized = False
        self.max_coast_frames = None if max_coast_frames is None else int(max_coast_frames)
        self.coast_count = 0

        self.previous_measurement = None
        self.velocity_initialized = False

    def reset(self):
        self.kf.statePre = np.zeros((4, 1), dtype=np.float32)
        self.kf.statePost = np.zeros((4, 1), dtype=np.float32)
        self.kf.errorCovPre = np.eye(4, dtype=np.float32)
        self.kf.errorCovPost = np.eye(4, dtype=np.float32)
        self.initialized = False
        self.coast_count = 0
        self.previous_measurement = None
        self.velocity_initialized = False

    def _confidence_from_covariance(self):
        trace = float(np.trace(self.kf.errorCovPost[:2, :2]))
        confidence = 1.0 / (1.0 + trace / 50.0)
        return float(np.clip(confidence, 0.0, 1.0))

    def _initialize(self, measurement):
        x, y = measurement
        ox, oy = self.init_offset
        x = float(x) + float(ox)
        y = float(y) + float(oy)

        state = np.array([[x], [y], [0.0], [0.0]], dtype=np.float32)
        self.kf.statePre = state.copy()
        self.kf.statePost = state.copy()
        self.initialized = True
        self.coast_count = 0
        self.previous_measurement = (x, y)
        self.velocity_initialized = False

    def update(self, measurement):
        """
        measurement: (world_x, world_y) or None.

        With a measurement, correct the filter. Without one, perform a
        pure prediction step.
        """
        if not self.initialized:
            if measurement is None:
                return None, 0.0
            self._initialize(measurement)
            return (
                int(round(self.kf.statePost[0, 0])),
                int(round(self.kf.statePost[1, 0])),
            ), 1.0

        if measurement is not None:
            mx, my = float(measurement[0]), float(measurement[1])

            # Initial velocity from the first two measurements.
            if self.previous_measurement is not None:
                vx = mx - self.previous_measurement[0]
                vy = my - self.previous_measurement[1]

                if not self.velocity_initialized:
                    self.kf.statePost[2, 0] = np.float32(vx)
                    self.kf.statePost[3, 0] = np.float32(vy)
                    self.kf.statePre[2, 0] = np.float32(vx)
                    self.kf.statePre[3, 0] = np.float32(vy)
                    self.velocity_initialized = True

            self.kf.predict()

            corrected = self.kf.correct(
                np.array([[np.float32(mx)], [np.float32(my)]], dtype=np.float32)
            )

            self.previous_measurement = (mx, my)
            self.coast_count = 0

            pos = (
                int(round(corrected[0, 0])),
                int(round(corrected[1, 0])),
            )

            return pos, self._confidence_from_covariance()

        # No optical measurement: pure future prediction.
        prediction = self.kf.predict()
        self.coast_count += 1

        # None: keep predicting for as long as the beacon is hidden.
        if (
            self.max_coast_frames is not None
            and self.coast_count > self.max_coast_frames
        ):
            return None, 0.0

        pos = (
            int(round(prediction[0, 0])),
            int(round(prediction[1, 0])),
        )

        return pos, self._confidence_from_covariance()

    def predict(self):
        """Return one future world-position prediction."""
        if not self.initialized:
            return None, 0.0
        return self.update(None)
