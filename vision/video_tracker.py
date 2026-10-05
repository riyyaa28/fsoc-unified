"""Coarse-pointing pipeline for pre-recorded video (PTZ bypassed).

Benchmark 2 of the problem statement feeds 30 fps .mp4 files that cover the
whole screen, with image noise and a moving beacon spot, straight into the
coarse pointing system. The virtual PTZ camera is bypassed, so every frame is
treated as the camera image and the job is to report, for each frame, where
the beacon centroid is.

Pipeline per frame
------------------
1. Monochrome conversion (the reference camera is a monochrome FPA).
2. Search
   * SEARCH state: whole-frame acquisition on a down-sampled copy.
   * TRACK states: a small window around the Kalman-predicted position,
     at full resolution. This keeps per-frame cost low on large frames.
3. Detection: 3x3 median filter (removes salt & pepper noise), then a box
   matched filter the size of the beacon (averages down Gaussian / Poisson
   noise). The peak is accepted when its robust SNR - (peak - median) /
   (1.4826 * MAD) of the filter response - clears a threshold.
   If whole-frame acquisition finds nothing, the YOLO detector (if supplied)
   is asked for a candidate, which must still pass the local SNR test.
4. Sub-pixel centroid: background-subtracted, intensity-weighted centroid of
   the thresholded spot, in original video pixel coordinates.
5. Constant-velocity Kalman filter (units: pixels, one step per frame) for
   gating, the next search window and coasting through short dropouts.

Track states
------------
SEARCH    -> nothing tracked; whole-frame acquisition every frame
TENTATIVE -> first detection; needs CONFIRM_FRAMES consistent detections
LOCKED    -> measured this frame, track confirmed
COAST     -> missed this frame; reporting the prediction for up to
             MAX_COAST_FRAMES before dropping back to SEARCH
"""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import cv2
import numpy as np

SEARCH, TENTATIVE, LOCKED, COAST = "SEARCH", "TENTATIVE", "LOCKED", "COAST"


@dataclass
class FrameResult:
    frame: int
    time_s: float
    state: str
    measured: bool                 # a centroid was measured in this frame
    x: float = None                # reported position (measured, or predicted when coasting)
    y: float = None
    source: str = ""               # MATCHED / YOLO / PREDICTED
    snr: float = 0.0
    size_px: float = 0.0           # estimated beacon width
    processing_ms: float = 0.0


@dataclass
class _Detection:
    x: float
    y: float
    snr: float
    size: float
    source: str = "MATCHED"
    contrast: float = 0.0          # matched-filter level above background
    level: float = 0.0             # absolute brightness of the spot's core
    similarity: float = None       # match to the learned beacon appearance


def _matched_response(image, k):
    """Box matched filter with local background removed: mean over the k x k
    beacon footprint minus the mean over a surrounding (4k+1) box. A smooth
    background (e.g. a sky gradient) then gives ~0 everywhere instead of
    peaking wherever the sky is brightest."""
    spot = cv2.boxFilter(image, cv2.CV_32F, (k, k))
    surround = 4 * k + 1
    return spot - cv2.boxFilter(image, cv2.CV_32F, (surround, surround))


def _robust_stats(values):
    """Median and MAD-based sigma of a float array (sub-sampled for speed)."""
    flat = values.ravel()
    if flat.size > 20000:
        flat = flat[:: flat.size // 20000 + 1]
    med = float(np.median(flat))
    sigma = 1.4826 * float(np.median(np.abs(flat - med)))
    return med, sigma


class VideoBeaconTracker:
    CONFIRM_FRAMES = 3             # consistent detections needed to declare lock
    MAX_COAST_FRAMES = 15          # 0.5 s at 30 fps
    ACQUIRE_SNR = 6.0              # minimum whole-frame acquisition threshold
    TRACK_SNR = 4.5                # minimum threshold inside the predicted window
    SNR_MARGIN = 2.5               # required margin above the expected noise maximum
    LOCKED_SNR_MARGIN = 1.5        # smaller margin once locked: the prediction constrains the search
    DECOY_CONTRAST_RATIO = 0.75    # reject candidates dimmer than this fraction of the beacon
    ACQUIRE_MAX_SIDE = 800         # longest side of the down-sampled search image
    MIN_SIGMA = 0.5                # floor for the noise estimate on very clean video
    MIN_BEACON_PX = 5              # smallest beacon in the spec (5-20 px)
    ACQUIRE_MIN_WIDTH = 3.0        # narrower candidates are noise while acquiring
    YOLO_EVERY = 5                 # YOLO fallback on every Nth unsuccessful search frame
    EDGE_MARGIN_FACTOR = 1.0       # while acquiring, ignore candidates this many beacon widths from the edge
    MAX_INNOVATION_RMS = 25.0      # px; covers the spec's +/-20 px jitter with margin
    # Simulation: the beacon's core brightness is known, so it is identified as
    # the brightest spot (contrast alone misranks spots on a sky gradient).
    identify_by_level = False
    expected_level = None          # apparent core brightness of the beacon, if known
    DECOY_LEVEL_RATIO = 0.8        # candidates dimmer than this x expected_level are decoys
    CANDIDATES = 4                 # peaks examined per search when identifying by level / appearance
    # Video: a high-passed template of the locked beacon (its shape, e.g. halo
    # rings) rejects candidates that look different. It is used only once the
    # locked beacon keeps matching it well (in heavy noise it may never be).
    APPEARANCE = True
    APPEARANCE_FRAMES = 2          # locked frames learned before the check starts
    APPEARANCE_RELIABLE = 0.75     # typical beacon similarity needed to start it
    APPEARANCE_MIN = 0.5           # floor of the similarity threshold
    APPEARANCE_MARGIN = 0.15       # threshold = beacon's typical similarity minus this
    APPEARANCE_LEARN = 0.1         # template update rate
    APPEARANCE_ALIGN = 3           # px of misalignment searched when matching
    # Multi-frame integration (track-before-detect) while not locked: see
    # _integrate(). Lets acquisition find a beacon too faint to stand out
    # from the noise in any single frame.
    INTEGRATE = True
    INTEGRATION_GAIN = 0.8         # evidence kept per frame (time constant ~5 frames)
    INTEGRATION_SNR = 4.0          # single-frame beacon SNR the evidence is tuned for
    EVIDENCE_THRESHOLD = 30.0      # log-likelihood evidence needed to accept a candidate
    # Largest image shift between frames the tracker cannot predict (jitter,
    # platform drift, beacon motion), px. None: derived from the beacon size
    # and the measured innovation.
    motion_px = None

    def __init__(self, width, height, fps=30.0, beacon_size=10, yolo=None, yolo_async=False):
        self.width, self.height = int(width), int(height)
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self.beacon_size = float(beacon_size)
        self.yolo = yolo
        self._yolo_skip = 0
        # Asynchronous mode (interactive use): YOLO runs on a worker thread and
        # its answer is verified on a later frame, so it never stalls a frame.
        self._yolo_executor = ThreadPoolExecutor(max_workers=1) if yolo is not None and yolo_async else None
        self._yolo_future = None
        self._camera_shift = np.zeros(2)     # camera motion since the pending YOLO frame
        self._designated = None              # (YOLO beacon position or None, age in frames)
        self._kf = None
        self.state = SEARCH
        self.confirm_count = 0
        self.coast_count = 0
        self.last_detection = None
        # RMS prediction error: widens the gate for jitter the model cannot
        # predict. Kept across track restarts (it describes the video).
        self.innovation_rms = 0.0
        # Learned contrast of the beacon; dimmer spots are decoys. It decays while
        # searching, so a real change (e.g. thicker fog) is re-learned.
        self.beacon_contrast = 0.0
        # Integrated detection-evidence map (acquisition image scale) and the
        # known camera motion since it was last updated.
        self._evidence = None
        self._evidence_shift = np.zeros(2)
        self._evidence_frames = 0
        self._frame_response = None
        self._gray = None
        self._reset_appearance()

    def _reset_appearance(self):
        self._template = None
        self._template_sigma = 2.0
        self._appearance_sim = None    # typical similarity of the accepted beacon
        self._appearance_count = 0     # locked frames learned into the template

    # -- public API --------------------------------------------------------

    def reset(self):
        self._kf = None
        self.state = SEARCH
        self.confirm_count = 0
        self.coast_count = 0
        self.last_detection = None
        self.innovation_rms = 0.0
        self.beacon_contrast = 0.0
        self._evidence = None
        self._reset_appearance()

    def process(self, frame, index, hint=None):
        """Track one frame. hint=(x, y, radius): while acquiring, only accept
        candidates within radius px of (x, y) (e.g. from a wide-field finder)."""
        started = time.perf_counter()
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self._gray = gray
        prediction = self._predict()
        self._frame_response = None
        if self.INTEGRATE and self.state != LOCKED:
            self._integrate(gray)
        else:
            self._evidence = None

        detection = None
        if prediction is not None:
            detection = self._track_window(gray, prediction)
        if detection is None and (self.state in (SEARCH, TENTATIVE) or self.coast_count >= 2):
            # Acquire (or re-acquire after a real dropout) over the whole frame.
            detection = self._acquire(gray)
            if detection is not None and prediction is not None and self.state != SEARCH:
                # A whole-frame hit far from the track starts a new track,
                # which must be confirmed like a first acquisition.
                if math.hypot(detection.x - prediction[0], detection.y - prediction[1]) > self._gate():
                    self._kf = None
                    self.state = SEARCH
                    self.confirm_count = 0

        if (detection is not None and self.state in (SEARCH, TENTATIVE)
                and detection.size < self.ACQUIRE_MIN_WIDTH):
            # Noise peaks are a pixel or two wide; a real beacon is >= 5 px.
            detection = None
        elif detection is not None and self.state in (SEARCH, TENTATIVE) and self._near_edge(detection):
            # Only part of the beacon is in view, so its centroid is biased;
            # confirm it once it is fully inside the frame.
            detection = None
        elif detection is not None and detection.size < 0.4 * self.beacon_size:
            # While tracking, a candidate far narrower than the learned beacon
            # width is a noise blob or a small decoy, not the beacon.
            detection = None
        if (detection is not None and not self.identify_by_level and self.beacon_contrast > 0
                and detection.contrast < self.DECOY_CONTRAST_RATIO * self.beacon_contrast):
            detection = None                 # dimmer than the designated beacon: a decoy
        if (detection is not None and hint is not None and self.state in (SEARCH, TENTATIVE)
                and math.hypot(detection.x - hint[0], detection.y - hint[1]) > hint[2]):
            detection = None                 # not where the finder sees the beacon
        if detection is None and self.state == SEARCH:
            self.beacon_contrast *= 0.98
        if detection is not None and prediction is not None and self._kf is not None:
            residual = math.hypot(detection.x - prediction[0], detection.y - prediction[1])
            # Capped, or one noise blob near the gate edge could grow the gate
            # every frame until it covers the whole frame.
            self.innovation_rms = min(self.MAX_INNOVATION_RMS,
                                      math.sqrt(0.85 * self.innovation_rms ** 2 + 0.15 * residual ** 2))

        result = self._update_state(detection, index)
        result.processing_ms = (time.perf_counter() - started) * 1000.0
        return result

    def _plausible(self, detection):
        """Size / position checks a beacon candidate must pass (see process())."""
        if detection.size < 0.4 * self.beacon_size:
            return False
        if self.state in (SEARCH, TENTATIVE):
            return detection.size >= self.ACQUIRE_MIN_WIDTH and not self._near_edge(detection)
        return True

    def _near_edge(self, detection):
        margin = self.EDGE_MARGIN_FACTOR * self.beacon_size
        return (detection.x < margin or detection.y < margin
                or detection.x > self.width - 1 - margin or detection.y > self.height - 1 - margin)

    def compensate_camera_motion(self, dx, dy):
        """Shift the track by a known camera move (px, image axes).

        With a moving camera the beacon's image position changes by minus the
        camera motion. Applying it to the filter state keeps the filter's
        velocity equal to the beacon's motion on the screen, which the
        pointing controller uses as feed-forward.
        """
        self._camera_shift += (dx, dy)
        self._evidence_shift += (dx, dy)
        if self._kf is None:
            return
        for state in (self._kf.statePost, self._kf.statePre):
            state[0, 0] -= dx
            state[1, 0] -= dy

    def velocity(self):
        """Estimated beacon velocity (px per frame), zero when not tracking."""
        if self._kf is None:
            return np.zeros(2)
        return np.array([float(self._kf.statePost[2, 0]), float(self._kf.statePost[3, 0])])

    def last_position(self):
        """Latest filtered beacon position in image px, or None."""
        if self._kf is None:
            return None
        return np.array([float(self._kf.statePost[0, 0]), float(self._kf.statePost[1, 0])])

    # -- state machine -----------------------------------------------------

    def _update_state(self, detection, index):
        time_s = index / self.fps
        if detection is not None:
            self._correct(detection.x, detection.y)
            self.coast_count = 0
            self.last_detection = detection
            # Spec target size is 5-20 px; adapt slowly within that range.
            self.beacon_size = 0.9 * self.beacon_size + 0.1 * float(np.clip(detection.size, 4, 24))
            if self.state in (LOCKED, COAST):
                self.state = LOCKED
            else:
                self.confirm_count += 1
                self.state = LOCKED if self.confirm_count >= self.CONFIRM_FRAMES else TENTATIVE
            if self.state == LOCKED:
                self._learn_appearance(detection)
            if self.state == LOCKED and detection.contrast > 0:
                self.beacon_contrast = (detection.contrast if self.beacon_contrast <= 0
                                        else 0.9 * self.beacon_contrast + 0.1 * detection.contrast)
            return FrameResult(index, time_s, self.state, True, detection.x, detection.y,
                               detection.source, detection.snr, detection.size)

        self.confirm_count = 0
        if self.state in (LOCKED, COAST) and self.coast_count < self.MAX_COAST_FRAMES and self._kf is not None:
            self.coast_count += 1
            self.state = COAST
            px, py = self._position()
            return FrameResult(index, time_s, COAST, False, px, py, "PREDICTED")

        self.state = SEARCH
        self.coast_count = 0
        self._kf = None
        return FrameResult(index, time_s, SEARCH, False)

    # -- Kalman filter (constant velocity, pixel units, dt = 1 frame) ------

    def _init_kf(self, x, y):
        kf = cv2.KalmanFilter(4, 2)
        kf.transitionMatrix = np.array([[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
        kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
        kf.processNoiseCov = np.diag([0.5, 0.5, 1.0, 1.0]).astype(np.float32)
        kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 0.5
        kf.errorCovPost = np.diag([4.0, 4.0, 100.0, 100.0]).astype(np.float32)
        kf.statePost = np.array([[x], [y], [0], [0]], np.float32)
        self._kf = kf

    def _predict(self):
        if self._kf is None:
            return None
        state = self._kf.predict()
        return float(state[0, 0]), float(state[1, 0])

    def _correct(self, x, y):
        if self._kf is None:
            self._init_kf(x, y)
        else:
            # Measurement noise follows the residuals: smoother output under jitter.
            variance = max(0.5, 0.5 * self.innovation_rms ** 2)
            self._kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * variance
            self._kf.correct(np.array([[x], [y]], np.float32))

    def _position(self):
        state = self._kf.statePre if self.coast_count else self._kf.statePost
        return float(state[0, 0]), float(state[1, 0])

    def _speed(self):
        if self._kf is None:
            return 0.0
        return math.hypot(float(self._kf.statePost[2, 0]), float(self._kf.statePost[3, 0]))

    def _gate(self):
        # Window growth while coasting is capped: longer dropouts are
        # recovered by whole-frame acquisition, which has a stricter threshold.
        return (3 * self.beacon_size + 12 + 2 * self._speed() + 3 * self.innovation_rms
                + 6 * min(self.coast_count, 5))

    def _snr_threshold(self, area, kernel, floor, margin=None):
        """Detection threshold that grows with the size of the searched area.

        The largest of N independent noise samples is about sqrt(2 ln N)
        standard deviations, so a fixed threshold would produce false
        detections in large windows. N ~ area / kernel^2 after box filtering.
        """
        samples = max(area / float(kernel * kernel), 2.0)
        margin = self.SNR_MARGIN if margin is None else margin
        return max(floor, math.sqrt(2.0 * math.log(samples)) + margin)

    # -- detection ---------------------------------------------------------

    def _kernel(self, scale=1.0):
        k = max(3, int(round(self.beacon_size * scale)))
        return k if k % 2 else k + 1

    def _track_window(self, gray, prediction):
        half = int(self._gate() + 2 * self.beacon_size)
        margin = self.LOCKED_SNR_MARGIN if self.state in (LOCKED, COAST) else None
        detection = self._detect_region(gray, prediction, half, self.TRACK_SNR, margin)
        if detection is None:
            return None
        if math.hypot(detection.x - prediction[0], detection.y - prediction[1]) > self._gate():
            return None
        return detection

    def _detect_region(self, gray, center, half, snr_threshold, margin=None):
        """Matched-filter detection in a window of +/-half px around center (full resolution)."""
        cx, cy = int(round(center[0])), int(round(center[1]))
        x0, y0 = max(0, cx - half), max(0, cy - half)
        x1, y1 = min(self.width, cx + half + 1), min(self.height, cy + half + 1)
        k = self._kernel()
        if x1 - x0 < k + 2 or y1 - y0 < k + 2:
            return None
        region = cv2.medianBlur(np.ascontiguousarray(gray[y0:y1, x0:x1]), 3)
        response = _matched_response(region, k)
        med, sigma = _robust_stats(response)
        threshold = self._snr_threshold(region.size, k, snr_threshold, margin)
        return self._best([self._centroid(region, loc, x0, y0, snr, peak)
                           for snr, peak, loc in self._peaks(response, med, sigma, threshold, k)])

    def _peaks(self, response, med, sigma, threshold, k):
        """Strongest response peaks above threshold: (snr, peak, loc). One peak
        normally; the top few when identifying the beacon by brightness."""
        count = self.CANDIDATES if self.identify_by_level or self._appearance_on() else 1
        work = response.copy() if count > 1 else response
        peaks = []
        r = int(1.5 * k) + 1
        for _ in range(count):
            _, peak, _, loc = cv2.minMaxLoc(work)
            snr = (peak - med) / max(sigma, self.MIN_SIGMA)
            if snr < threshold:
                break
            peaks.append((snr, peak, loc))
            x, y = loc
            work[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = -1e9
        return peaks

    def _best(self, detections):
        """Pick the designated beacon among candidate detections."""
        # Drop candidates process() would reject anyway, before ranking: a
        # 1 px salt speck at the frame border reads as level 255 and would
        # otherwise out-rank a dim beacon (e.g. in low light), which is then lost.
        detections = [d for d in detections if d is not None and self._plausible(d)]
        if not detections:
            return None
        if not self.identify_by_level:
            if self._appearance_on():
                return self._most_beacon_like(detections)
            return detections[0]
        if self.expected_level:
            detections = [d for d in detections if d.level >= self.DECOY_LEVEL_RATIO * self.expected_level]
        return max(detections, key=lambda d: d.level) if detections else None

    def _acquisition_response(self, gray):
        """Matched-filter response of the down-sampled search image for this
        frame: (scale, k, response, median, sigma). Computed once per frame."""
        if self._frame_response is None:
            scale = min(1.0, self.ACQUIRE_MAX_SIDE / max(self.width, self.height))
            small = gray if scale >= 1.0 else cv2.resize(gray, None, fx=scale, fy=scale,
                                                         interpolation=cv2.INTER_AREA)
            small = cv2.medianBlur(small, 3)
            k = self._kernel(scale)
            response = _matched_response(small, k)
            self._frame_response = (scale, k, response) + _robust_stats(response)
        return self._frame_response

    def _integrate(self, gray):
        """Track-before-detect evidence map, updated every frame while not locked.

        Each frame's matched-filter SNR map is added to the previous evidence,
        which is first moved by the known camera motion, then spread by a max
        filter over the motion the tracker cannot predict and decayed. A
        beacon keeps adding to the same trail frame after frame; noise peaks
        land somewhere new each time and fade. The evidence SNR therefore
        grows for the beacon (up to 1 / (1 - gain) times its single-frame SNR)
        while noise grows far less, so a beacon too faint to beat the noise
        maximum in any single frame can still be found.
        """
        scale, k, response, med, sigma = self._acquisition_response(gray)
        # Log-likelihood ratio of "beacon of single-frame SNR a" vs "noise":
        # a * snr - a^2 / 2. Noise contributes negative values almost
        # everywhere, so spreading it by the max filter adds little, while
        # the beacon adds about a * (its SNR - a / 2) every frame.
        a = self.INTEGRATION_SNR
        llr = (response - med) * (a / max(sigma, self.MIN_SIGMA)) - 0.5 * a * a
        evidence = self._evidence
        if evidence is None or evidence.shape != llr.shape:
            self._evidence, self._evidence_frames = np.maximum(llr, 0.0), 1
        else:
            dx, dy = -self._evidence_shift * scale
            if abs(dx) >= 0.5 or abs(dy) >= 0.5:
                shift = np.float32([[1, 0, dx], [0, 1, dy]])
                evidence = cv2.warpAffine(evidence, shift, (evidence.shape[1], evidence.shape[0]),
                                          flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REPLICATE)
            motion = self.motion_px if self.motion_px is not None else 2 * self.beacon_size + 3 * self.innovation_rms
            r = max(1, int(round(motion * scale)))
            evidence = cv2.dilate(evidence, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1)))
            evidence *= self.INTEGRATION_GAIN
            evidence += llr
            np.maximum(evidence, 0.0, out=evidence)
            self._evidence = evidence
            self._evidence_frames += 1
        self._evidence_shift = np.zeros(2)

    def _evidence_ready(self):
        return self._evidence is not None and self._evidence_frames >= 3

    def _evidence_at(self, x, y, scale, k):
        """Evidence at image position (x, y): best value within the kernel."""
        ex, ey = int(round(x * scale)), int(round(y * scale))
        h, w = self._evidence.shape
        patch = self._evidence[max(0, ey - k):min(h, ey + k + 1), max(0, ex - k):min(w, ex + k + 1)]
        return float(patch.max()) if patch.size else 0.0

    def _evidence_peaks(self, k):
        """Evidence-map peaks above EVIDENCE_THRESHOLD, strongest first: (value, loc)."""
        work = self._evidence.copy()
        peaks = []
        r = int(1.5 * k) + 1
        for _ in range(self.CANDIDATES):
            _, value, _, loc = cv2.minMaxLoc(work)
            if value < self.EVIDENCE_THRESHOLD:
                break
            peaks.append((value, loc))
            x, y = loc
            work[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = 0.0
        return peaks

    def _acquire_integrated(self, gray, scale, k):
        """Acquire from the evidence map: candidates are its peaks, centroided
        in the current frame when the beacon shows there, else at the peak."""
        verified, unverified = [], []
        for snr, loc in self._evidence_peaks(k):
            x, y = loc[0] / scale, loc[1] / scale
            detection = self._detect_region(gray, (x, y), int(3 * self.beacon_size + 8), self.TRACK_SNR,
                                            self.LOCKED_SNR_MARGIN)
            if detection is not None and math.hypot(detection.x - x, detection.y - y) <= self.beacon_size + k / scale:
                detection.source = "INTEGRATED"
                verified.append(detection)
            else:
                # Brightness measured here in this frame, so the level gate in
                # _best() still applies: the evidence map builds up just as
                # well on a decoy (e.g. one the camera is parked on).
                unverified.append(_Detection(x, y, snr, self.beacon_size, source="INTEGRATED",
                                             level=self._local_level(gray, x, y)))
        detection = self._best(verified)
        if detection is None:
            unverified = [d for d in unverified if self._plausible(d)]
            if self.identify_by_level and self.expected_level:
                unverified = [d for d in unverified if d.level >= self.DECOY_LEVEL_RATIO * self.expected_level]
            elif self._appearance_on():
                unverified = [d for d in unverified if self._most_beacon_like([d]) is not None]
            detection = unverified[0] if unverified else None       # strongest evidence first
        return detection

    # -- appearance model --------------------------------------------------

    def _appearance_on(self):
        return (self.APPEARANCE and not self.identify_by_level and self._template is not None
                and self._appearance_count >= self.APPEARANCE_FRAMES
                and self._appearance_sim is not None and self._appearance_sim >= self.APPEARANCE_RELIABLE)

    def _appearance_threshold(self):
        if self._appearance_sim is None:
            return self.APPEARANCE_MIN
        return max(self.APPEARANCE_MIN, self._appearance_sim - self.APPEARANCE_MARGIN)

    def _highpass(self, x, y, half):
        """Median-filtered, high-passed patch of +/-half px around (x, y); the
        frame is extended by replication at the border."""
        pad = int(3 * self._template_sigma) + 1
        cx, cy = int(round(x)), int(round(y))
        r = half + pad
        x0, y0, x1, y1 = cx - r, cy - r, cx + r + 1, cy + r + 1
        crop = self._gray[max(0, y0):min(self.height, y1), max(0, x0):min(self.width, x1)]
        if crop.size == 0:
            return None
        crop = cv2.copyMakeBorder(np.ascontiguousarray(crop), max(0, -y0), max(0, y1 - self.height),
                                  max(0, -x0), max(0, x1 - self.width), cv2.BORDER_REPLICATE)
        g = cv2.medianBlur(crop, 3).astype(np.float32)
        g -= cv2.GaussianBlur(g, (0, 0), self._template_sigma)
        return g[pad:-pad, pad:-pad]

    def _similarity(self, x, y):
        """Normalised correlation of the spot at (x, y) with the beacon template."""
        half = self._template.shape[0] // 2
        search = self._highpass(x, y, half + self.APPEARANCE_ALIGN)
        if search is None:
            return -1.0
        return float(cv2.matchTemplate(search, self._template, cv2.TM_CCOEFF_NORMED).max())

    def _most_beacon_like(self, detections):
        """The candidate that best matches the beacon template, or None when
        none matches well enough (all decoys)."""
        threshold = self._appearance_threshold()
        best = None
        for d in detections:
            d.similarity = self._similarity(d.x, d.y)
            if d.similarity >= threshold and (best is None or d.similarity > best.similarity):
                best = d
        return best

    def _learn_appearance(self, detection):
        """Create / update the beacon template from a locked measurement."""
        if not self.APPEARANCE or self.identify_by_level or self._gray is None:
            return
        if self._template is None:
            # Wide enough to take in a halo around the core.
            self._template_sigma = max(2.0, 0.4 * self.beacon_size)
            self._template = self._highpass(detection.x, detection.y, int(np.clip(2.5 * self.beacon_size, 10, 40)))
            self._appearance_count = 1
            return
        gating = self._appearance_on()
        similarity = detection.similarity
        if similarity is None:
            similarity = self._similarity(detection.x, detection.y)
        if gating and similarity < self._appearance_threshold():
            return
        self._appearance_sim = (similarity if self._appearance_sim is None
                                else 0.9 * self._appearance_sim + 0.1 * similarity)
        if not gating or similarity >= self._appearance_threshold() + 0.5 * self.APPEARANCE_MARGIN:
            # Learning: running mean over the first frames (averages out the
            # noise), then a slow update that follows appearance changes.
            patch = self._highpass(detection.x, detection.y, self._template.shape[0] // 2)
            if patch is not None and patch.shape == self._template.shape:
                rate = max(self.APPEARANCE_LEARN, 1.0 / (self._appearance_count + 1))
                self._template = (1 - rate) * self._template + rate * patch
                self._appearance_count += 1

    def _local_level(self, gray, x, y):
        """Core brightness at (x, y): 90th percentile of the median-filtered
        beacon-sized patch, as _centroid() measures it for a detected spot."""
        half = max(2, int(round(0.5 * self.beacon_size)))
        cx, cy = int(round(x)), int(round(y))
        x0, y0 = max(0, cx - half - 1), max(0, cy - half - 1)
        patch = gray[y0:min(self.height, cy + half + 2), x0:min(self.width, cx + half + 2)]
        if patch.shape[0] < 3 or patch.shape[1] < 3:
            return 0.0
        patch = cv2.medianBlur(np.ascontiguousarray(patch), 3)[1:-1, 1:-1]
        return float(np.percentile(patch, 90))

    def _acquire(self, gray):
        scale, k, response, med, sigma = self._acquisition_response(gray)
        peaks = self._peaks(response, med, sigma, self._snr_threshold(response.size, k, self.ACQUIRE_SNR), k)
        detection = self._best([self._detect_region(gray, (loc[0] / scale, loc[1] / scale),
                                                    int(3 * self.beacon_size + 8), self.TRACK_SNR)
                                for _, _, loc in peaks])
        if detection is None:
            detection = self._acquire_full_resolution(gray)
        if self._evidence_ready():
            # Once enough frames are integrated, a single-frame hit must also be
            # backed by the evidence map (noise peaks are not); failing that,
            # acquire from the evidence map itself.
            if detection is not None and self._evidence_at(detection.x, detection.y, scale, k) < \
                    self.EVIDENCE_THRESHOLD:
                detection = None
            if detection is None:
                detection = self._acquire_integrated(gray, scale, k)
        designated = self._designate(gray)
        if designated is not False:
            return self._at_designated(gray, detection, designated)
        return detection if detection is not None else self._acquire_yolo(gray)

    def _designate(self, gray):
        """Where YOLO sees the beacon (full-resolution px), None if it sees
        none, or False when not applicable.

        Until the beacon's appearance is learned nothing tells it apart from
        decoys, so the strongest spot would be taken. YOLO is trained to tell
        the beacon (halo rings) from plain decoys, so it designates which spot
        to lock. Its answer is reused for YOLO_EVERY frames to bound the cost.
        """
        if (self.yolo is None or self._yolo_executor is not None or self.identify_by_level
                or self._appearance_on()):
            return False
        self._yolo_skip = (self._yolo_skip + 1) % self.YOLO_EVERY
        if self._yolo_skip == 1 or self._designated is None:
            scale = min(1.0, 640.0 / max(self.width, self.height))
            small = gray if scale >= 1.0 else cv2.resize(gray, None, fx=scale, fy=scale,
                                                         interpolation=cv2.INTER_AREA)
            try:
                position, _ = self.yolo.detect(cv2.cvtColor(small, cv2.COLOR_GRAY2BGR))
            except Exception:
                position = None
            self._designated = (None if position is None else (position[0] / scale, position[1] / scale), 0)
        else:
            self._designated = (self._designated[0], self._designated[1] + 1)
        return self._designated[0]

    def _at_designated(self, gray, detection, designated):
        """The candidate at YOLO's beacon position: the acquired detection if it
        is there, else a local search there. No YOLO beacon: unchanged."""
        if designated is None:
            return detection
        tolerance = 2 * self.beacon_size + 10 + 3 * self._designated[1]
        if detection is not None and math.hypot(detection.x - designated[0],
                                                detection.y - designated[1]) <= tolerance:
            return detection
        detection = self._detect_region(gray, designated, int(3 * self.beacon_size + 8 + tolerance), self.TRACK_SNR)
        if detection is None or math.hypot(detection.x - designated[0], detection.y - designated[1]) > tolerance:
            return None
        detection.source = "YOLO"
        return detection

    def _acquire_full_resolution(self, gray):
        """Second acquisition pass for small or faint beacons.

        Down-sampling shrinks a 5 px beacon to ~2 px on large frames, where it
        falls below the threshold. This pass uses the full-resolution frame and
        a kernel matched to the smallest specified beacon (5 px): a larger
        beacon still fills it, so no contrast is lost either way.
        """
        k = self.MIN_BEACON_PX
        filtered = cv2.medianBlur(gray, 3)
        response = _matched_response(filtered, k)
        med, sigma = _robust_stats(response)
        peaks = self._peaks(response, med, sigma, self._snr_threshold(filtered.size, k, self.ACQUIRE_SNR), k)
        if not peaks:
            return None
        saved = self.beacon_size
        self.beacon_size = float(k)
        detection = self._best([self._detect_region(gray, loc, int(3 * k + 8), self.TRACK_SNR)
                                for _, _, loc in peaks])
        self.beacon_size = saved if detection is None else max(float(k), min(saved, detection.size))
        return detection

    def _acquire_yolo(self, gray):
        """AI fallback: ask YOLO for a candidate, then verify and centroid it locally."""
        if self.yolo is None:
            return None
        # YOLO costs tens of ms on a CPU; while the beacon is simply absent
        # (e.g. occluded) running it every frame would only slow things down.
        scale = min(1.0, 640.0 / max(self.width, self.height))
        if self._yolo_executor is not None:
            return self._acquire_yolo_async(gray, scale)
        self._yolo_skip = (self._yolo_skip + 1) % self.YOLO_EVERY
        if self._yolo_skip != 1:
            return None
        small = gray if scale >= 1.0 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        try:
            position, _ = self.yolo.detect(cv2.cvtColor(small, cv2.COLOR_GRAY2BGR))
        except Exception:
            return None
        return self._verify_yolo(gray, position, scale, extra=0)

    def _acquire_yolo_async(self, gray, scale):
        future = self._yolo_future
        if future is not None and future.done():
            self._yolo_future = None
            try:
                position, _ = future.result()
            except Exception:
                position = None
            if position is None:
                return None
            # Move the answer by the camera motion since that frame, and search
            # a wider window because the beacon itself has moved on as well.
            px = position[0] / scale - self._camera_shift[0]
            py = position[1] / scale - self._camera_shift[1]
            return self._verify_yolo(gray, (px * scale, py * scale), scale, extra=40)
        if future is None:
            self._yolo_skip = (self._yolo_skip + 1) % self.YOLO_EVERY
            if self._yolo_skip == 1:
                small = gray if scale >= 1.0 else cv2.resize(gray, None, fx=scale, fy=scale,
                                                             interpolation=cv2.INTER_AREA)
                self._camera_shift = np.zeros(2)
                self._yolo_future = self._yolo_executor.submit(
                    self.yolo.detect, cv2.cvtColor(small, cv2.COLOR_GRAY2BGR))
        return None

    def _verify_yolo(self, gray, position, scale, extra):
        """Accept a YOLO candidate only if the local matched filter confirms it."""
        if position is None:
            return None
        detection = self._detect_region(gray, (position[0] / scale, position[1] / scale),
                                        int(3 * self.beacon_size + 8 + extra), self.TRACK_SNR)
        if detection is not None:
            detection.source = "YOLO"
        return detection

    def _centroid(self, region, loc, x0, y0, snr, response_peak):
        """Intensity-weighted sub-pixel centroid of the spot around loc (region coords).

        response_peak is the background-subtracted matched-filter value at
        loc, i.e. the spot's mean contrast over its footprint. It sets the
        threshold instead of the single brightest pixel, which may be a
        surviving salt-noise pixel.
        """
        # Window comfortably larger than the beacon so the size estimate below
        # cannot shrink the window that measures it.
        half = max(int(1.5 * self.beacon_size), 8)
        px, py = loc
        wx0, wy0 = max(0, px - half), max(0, py - half)
        wx1, wy1 = min(region.shape[1], px + half + 1), min(region.shape[0], py + half + 1)
        window = region[wy0:wy1, wx0:wx1].astype(np.float32)
        # Local background and noise from the window's border ring, so a sky
        # gradient across the search area does not bias the centroid.
        ring = np.concatenate([window[0], window[-1], window[1:-1, 0], window[1:-1, -1]])
        background, pixel_sigma = _robust_stats(ring)
        contrast = max(float(response_peak), 1.0)
        threshold = background + max(2.5 * pixel_sigma, 0.4 * contrast)
        mask = window > threshold
        if not mask.any():
            return _Detection(x0 + px, y0 + py, snr, self.beacon_size, contrast=contrast,
                              level=float(window.max()))
        # Keep the blob at the matched-filter peak (the beacon core); failing
        # that, the blob carrying the most signal energy - not an isolated
        # noise pixel that happens to be brighter.
        count, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
        if count > 2:
            at_peak = labels[py - wy0, px - wx0]
            if at_peak:
                mask = labels == at_peak
            else:
                energy = np.bincount(labels.ravel(), weights=np.where(mask, window - background, 0).ravel())
                energy[0] = -1
                mask = labels == int(np.argmax(energy))
        weights = np.where(mask, window - background, 0.0)
        total = float(weights.sum())
        # Core brightness: 90th percentile of the blob (median-filtered, so a
        # stray salt pixel cannot set it; near the peak, so a Gaussian spot's
        # dim flanks do not pull it down).
        level = float(np.percentile(window[mask], 90))
        if total <= 0:
            return _Detection(x0 + px, y0 + py, snr, self.beacon_size, contrast=contrast, level=level)
        ys, xs = np.indices(window.shape, dtype=np.float32)
        cx = float((weights * xs).sum() / total) + wx0 + x0
        cy = float((weights * ys).sum() / total) + wy0 + y0
        # Width from the area above half of the spot's peak (FWHM-style).
        size = math.sqrt(float((weights > 0.5 * float(weights.max())).sum()))
        return _Detection(cx, cy, snr, size, contrast=contrast, level=level)


# ======================================================================
# Ground truth
# ======================================================================

GROUND_TRUTH_SUFFIXES = ("_gt.csv", "_groundtruth.csv", "_ground_truth.csv", "_truth.csv",
                         "_labels.csv", ".csv", ".txt")


def find_ground_truth(video_path):
    """Look for a ground-truth file saved beside the video (same file stem)."""
    import os
    stem = os.path.splitext(video_path)[0]
    for suffix in GROUND_TRUTH_SUFFIXES:
        candidate = stem + suffix
        if os.path.isfile(candidate):
            return candidate
    return None


def load_ground_truth(path, fps=30.0):
    """Parse a ground-truth centroid file into {frame_index: (x, y) or None}.

    Accepts CSV / whitespace text with or without a header. Recognised
    headers (case-insensitive): frame / frame_idx / index / idx / f,
    time / time_s / t / timestamp, x / cx / gt_x / centroid_x / x_px,
    y / cy / gt_y / centroid_y / y_px, visible / present / valid.
    Without a header, rows are read as "frame, x, y" (3+ columns) or "x, y"
    (2 columns, row number = frame). A 1-based frame column is converted
    to 0-based. Empty, NaN or negative coordinates mean "beacon absent".
    """
    import csv
    import re

    with open(path, newline="", encoding="utf-8-sig") as handle:
        text = handle.read()
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError("ground-truth file is empty")
    delimiter = "," if "," in lines[0] else ";" if ";" in lines[0] else "\t" if "\t" in lines[0] else None
    rows = [next(csv.reader([line], delimiter=delimiter)) if delimiter else line.split() for line in lines]
    rows = [[cell.strip() for cell in row] for row in rows]

    def is_number(value):
        try:
            float(value)
            return True
        except ValueError:
            return value.lower() in ("", "nan", "none", "null", "-")

    header = None
    if not all(is_number(cell) for cell in rows[0]):
        header = [re.sub(r"[^a-z_]", "", cell.lower()) for cell in rows[0]]
        rows = rows[1:]

    def column(*names):
        if header is None:
            return None
        for name in names:
            if name in header:
                return header.index(name)
        return None

    if header is not None:
        frame_col = column("frame", "frame_idx", "frameindex", "frame_index", "frame_no", "frameno",
                           "frame_number", "index", "idx", "f", "n")
        time_col = column("time", "time_s", "t", "timestamp", "seconds", "sec")
        x_col = column("x", "cx", "gt_x", "centroid_x", "x_px", "xc", "x_center", "center_x",
                       "beacon_x", "target_x", "true_x", "u")
        y_col = column("y", "cy", "gt_y", "centroid_y", "y_px", "yc", "y_center", "center_y",
                       "beacon_y", "target_y", "true_y", "v")
        visible_col = column("visible", "present", "valid", "detected", "in_view")
        if x_col is None or y_col is None:
            raise ValueError(f"could not find x / y columns in header: {rows and header}")
    else:
        width = len(rows[0])
        frame_col, time_col, visible_col = (0, None, None) if width >= 3 else (None, None, None)
        x_col, y_col = (1, 2) if width >= 3 else (0, 1)

    def number(row, col):
        if col is None or col >= len(row):
            return None
        try:
            value = float(row[col])
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    parsed = []
    for row_index, row in enumerate(rows):
        if frame_col is not None and number(row, frame_col) is not None:
            frame = int(round(number(row, frame_col)))
        elif time_col is not None and number(row, time_col) is not None:
            frame = int(round(number(row, time_col) * fps))
        else:
            frame = row_index
        x, y = number(row, x_col), number(row, y_col)
        visible = number(row, visible_col)
        present = x is not None and y is not None and x >= 0 and y >= 0 and (visible is None or visible > 0)
        parsed.append((frame, (x, y) if present else None))

    if frame_col is not None and parsed and min(frame for frame, _ in parsed) == 1:
        parsed = [(frame - 1, value) for frame, value in parsed]
    return dict(parsed)


# ======================================================================
# Metrics
# ======================================================================

LOCK_ERROR_PX = 10.0               # spec: tracking error <= 10 px


@dataclass
class BenchmarkMetrics:
    """Accumulates per-frame results and computes the Benchmark 2 figures."""
    fps: float
    width: int
    height: int
    fov_deg: tuple = (4.0, 3.0)
    ground_truth: dict = None
    results: list = field(default_factory=list)
    frame_ms: list = field(default_factory=list)     # end-to-end time incl. decode / display
    wall_started: float = field(default_factory=time.perf_counter)
    wall_elapsed: float = 0.0
    live: dict = field(default_factory=lambda: {
        "visible": 0, "locked": 0, "first_visible": None, "first_lock": None,
        "outage_start": None, "outage_back": None, "last_reacquisition_s": None,
        "reacquisitions": 0, "error_sq": 0.0, "errors": 0, "last_error": None,
    })

    def add(self, result, frame_ms=None):
        self.results.append(result)
        if frame_ms is not None:
            self.frame_ms.append(frame_ms)
        self.wall_elapsed = time.perf_counter() - self.wall_started
        self._update_live(result)

    def _update_live(self, r):
        """Running values for the live display (same definitions as summary())."""
        live = self.live
        visible = self.ground_truth is None or self.truth(r.frame) is not None
        locked = self.is_locked(r)
        error = self.centroid_error(r)
        live["last_error"] = error[2] if error else None
        if error:
            live["error_sq"] += error[2] ** 2
            live["errors"] += 1
        if visible and live["first_visible"] is None:
            live["first_visible"] = r.frame
        if live["first_lock"] is None:
            if not locked:
                return
            live["first_lock"] = r.frame
        if visible:
            live["visible"] += 1
            live["locked"] += int(locked)
        if not locked:
            if live["outage_start"] is None:
                live["outage_start"] = r.frame
            if live["outage_back"] is None and (visible if self.ground_truth is not None else r.measured):
                live["outage_back"] = r.frame
        elif live["outage_start"] is not None:
            back = live["outage_back"] if live["outage_back"] is not None else r.frame
            live["last_reacquisition_s"] = (r.frame - back) / self.fps
            live["reacquisitions"] += 1
            live["outage_start"] = live["outage_back"] = None

    def live_values(self):
        live = self.live
        acquisition = None
        if live["first_lock"] is not None and live["first_visible"] is not None:
            acquisition = (live["first_lock"] - live["first_visible"]) / self.fps
        return {
            "acquisition_s": acquisition,
            "reacquisition_s": live["last_reacquisition_s"],
            "lock_retention_pct": live["locked"] / live["visible"] * 100 if live["visible"] else None,
            "rmse_px": math.sqrt(live["error_sq"] / live["errors"]) if live["errors"] else None,
            "error_px": live["last_error"],
        }

    # -- per-frame helpers ------------------------------------------------

    def truth(self, frame):
        if self.ground_truth is None:
            return None
        return self.ground_truth.get(frame)

    def centroid_error(self, result):
        """(ex, ey, e) of a measured centroid against ground truth, else None."""
        truth = self.truth(result.frame)
        if truth is None or not result.measured:
            return None
        ex, ey = result.x - truth[0], result.y - truth[1]
        return ex, ey, math.hypot(ex, ey)

    def boresight(self, result):
        """Offset of the reported position from the image centre, in px and degrees."""
        if result.x is None:
            return None
        dx, dy = result.x - self.width / 2.0, result.y - self.height / 2.0
        return dx, dy, dx * self.fov_deg[0] / self.width, dy * self.fov_deg[1] / self.height

    def is_locked(self, result):
        """Locked on the beacon this frame (and, with ground truth, within 10 px of it)."""
        if result.state != LOCKED:
            return False
        if self.ground_truth is None:
            return True
        error = self.centroid_error(result)
        return error is not None and error[2] <= LOCK_ERROR_PX

    # -- summary ----------------------------------------------------------

    def summary(self):
        results = self.results
        n = len(results)
        has_truth = self.ground_truth is not None
        processing = np.array([r.processing_ms for r in results], dtype=float) if n else np.array([])
        s = {
            "frames": n,
            "video_duration_s": n / self.fps if self.fps else 0.0,
            "wall_time_s": self.wall_elapsed,
            "has_ground_truth": has_truth,
            "measured_frames": sum(1 for r in results if r.measured),
            "yolo_frames": sum(1 for r in results if r.source == "YOLO"),
        }
        if n:
            s["processing_ms_mean"] = float(processing.mean())
            s["processing_ms_p95"] = float(np.percentile(processing, 95))
            s["processing_ms_max"] = float(processing.max())
            s["processing_fps"] = 1000.0 / s["processing_ms_mean"] if s["processing_ms_mean"] > 0 else None
            s["throughput_fps"] = n / self.wall_elapsed if self.wall_elapsed > 0 else None
        if self.frame_ms:
            frame_ms = np.array(self.frame_ms, dtype=float)
            s["frame_ms_mean"] = float(frame_ms.mean())
            s["frame_fps"] = 1000.0 / s["frame_ms_mean"] if s["frame_ms_mean"] > 0 else None

        locked = [self.is_locked(r) for r in results]
        # Acquisition: from the beacon's first appearance (frame 0 without truth)
        # to the first locked frame.
        first_visible = next((r.frame for r in results if not has_truth or self.truth(r.frame) is not None), None)
        first_lock = next((r.frame for r, ok in zip(results, locked) if ok), None)

        # Lock retention / target loss: frames from the first lock onwards in
        # which the beacon is visible (acquisition is reported separately).
        visible = [r for r in results if first_lock is not None and r.frame >= first_lock
                   and (not has_truth or self.truth(r.frame) is not None)]
        s["beacon_visible_frames"] = len(visible)
        s["locked_frames"] = sum(1 for r in visible if self.is_locked(r))
        s["lock_retention_pct"] = s["locked_frames"] / len(visible) * 100 if visible else None
        s["target_loss_pct"] = 100 - s["lock_retention_pct"] if s["lock_retention_pct"] is not None else None
        s["first_visible_frame"] = first_visible
        s["first_lock_frame"] = first_lock
        s["acquisition_time_s"] = ((first_lock - first_visible) / self.fps
                                   if first_lock is not None and first_visible is not None else None)

        # Re-acquisition: each outage after the first lock ends at the next
        # locked frame. The clock starts when the beacon is back in view
        # (ground truth) or, without truth, when the detector first sees it
        # again - so time the beacon spends hidden is not counted.
        events = []
        start = None
        for i, r in enumerate(results):
            if first_lock is None or r.frame <= first_lock:
                continue
            if not locked[i]:
                if start is None:
                    start = i
                continue
            if start is not None:
                outage = results[start:i]
                if has_truth:
                    back = next((o.frame for o in outage if self.truth(o.frame) is not None), r.frame)
                else:
                    back = next((o.frame for o in outage if o.measured), r.frame)
                events.append({
                    "lost_frame": results[start].frame,
                    "relock_frame": r.frame,
                    "outage_s": (r.frame - results[start].frame) / self.fps,
                    "reacquisition_s": (r.frame - back) / self.fps,
                })
                start = None
        s["reacquisition_events"] = events
        s["unrecovered_loss"] = start is not None
        times = [e["reacquisition_s"] for e in events]
        s["reacquisition_mean_s"] = float(np.mean(times)) if times else None
        s["reacquisition_max_s"] = float(np.max(times)) if times else None

        errors = [e for e in (self.centroid_error(r) for r in results) if e is not None]
        s["error_samples"] = len(errors)
        if errors:
            ex = np.array([e[0] for e in errors])
            ey = np.array([e[1] for e in errors])
            er = np.array([e[2] for e in errors])
            s.update({
                "rmse_px": float(np.sqrt(np.mean(er ** 2))),
                "rmse_x_px": float(np.sqrt(np.mean(ex ** 2))),
                "rmse_y_px": float(np.sqrt(np.mean(ey ** 2))),
                "mean_error_px": float(er.mean()),
                "median_error_px": float(np.median(er)),
                "p95_error_px": float(np.percentile(er, 95)),
                "max_error_px": float(er.max()),
                "bias_x_px": float(ex.mean()),
                "bias_y_px": float(ey.mean()),
                "within_10px_pct": float((er <= LOCK_ERROR_PX).mean() * 100),
            })
        return s

    # -- export -----------------------------------------------------------

    CSV_HEADER = ["frame", "time_s", "state", "measured", "x_px", "y_px", "source", "snr",
                  "beacon_size_px", "boresight_dx_px", "boresight_dy_px", "az_error_deg",
                  "el_error_deg", "processing_ms", "gt_x_px", "gt_y_px", "error_x_px",
                  "error_y_px", "error_px"]

    def write_csv(self, path):
        import csv

        def f(value, digits=3):
            return "" if value is None else f"{value:.{digits}f}"

        extra_columns = getattr(self, "extra_columns", None) or []
        extra_rows = getattr(self, "extra_rows", None) or []
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.CSV_HEADER + extra_columns)
            for i, r in enumerate(self.results):
                bore = self.boresight(r) or (None,) * 4
                truth = self.truth(r.frame) or (None, None)
                error = self.centroid_error(r) or (None,) * 3
                writer.writerow([
                    r.frame, f(r.time_s, 4), r.state, int(r.measured), f(r.x), f(r.y), r.source,
                    f(r.snr, 1) if r.measured else "", f(r.size_px, 1) if r.measured else "",
                    f(bore[0]), f(bore[1]), f(bore[2], 5), f(bore[3], 5), f(r.processing_ms, 2),
                    f(truth[0]), f(truth[1]), f(error[0]), f(error[1]), f(error[2]),
                ] + (list(extra_rows[i]) if i < len(extra_rows) else []))

    def write_summary(self, path, extra=None):
        import csv
        summary = self.summary()
        rows = dict(extra or {})
        for key, value in summary.items():
            if key == "reacquisition_events":
                rows["reacquisition_event_count"] = len(value)
                for i, event in enumerate(value, 1):
                    rows[f"reacquisition_{i}"] = (
                        f"lost frame {event['lost_frame']}, relocked frame {event['relock_frame']}, "
                        f"outage {event['outage_s']:.3f} s, re-acquisition {event['reacquisition_s']:.3f} s"
                    )
            else:
                rows[key] = value
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["metric", "value"])
            for key, value in rows.items():
                writer.writerow([key, f"{value:.4f}" if isinstance(value, float) else value])
        return summary
