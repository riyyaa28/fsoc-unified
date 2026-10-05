"""Background video decoding for the video benchmark (PTZ bypassed).

Decoding a 2000 x 2000 frame takes about 15 ms. FrameReader decodes on a
worker thread a few frames ahead, so decoding overlaps tracking and drawing
instead of adding to them (OpenCV releases the GIL while it decodes).

The worker also converts each frame to grey (the tracker's input; about
5 ms per 2000 x 2000 frame), available as .gray after read().

It mirrors the parts of cv2.VideoCapture the dashboard uses: read(), set()
(for seeking), get(), isOpened() and release().
"""

import queue
import threading

import cv2


class FrameReader:
    AHEAD = 4                       # frames decoded in advance

    def __init__(self, capture):
        self.capture = capture
        self._frames = queue.Queue(self.AHEAD)
        self._lock = threading.Lock()           # the capture is shared with set()/get()
        self._stop = threading.Event()
        self._seeked = threading.Event()
        # Bumped on every seek; frames decoded before it are discarded.
        self._generation = 0
        self.gray = None                        # grey version of the last frame read
        self._thread = threading.Thread(target=self._run, name="video-decoder", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                generation = self._generation
                ok, frame = self.capture.read()
            gray = None
            if ok and frame.ndim == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            elif ok:
                gray = frame
            item = (generation, ok, frame, gray)
            while not self._stop.is_set():
                try:
                    self._frames.put(item, timeout=0.1)
                    break
                except queue.Full:
                    if generation != self._generation:
                        break                   # a seek made this frame stale
            if not ok:
                # End of the clip: wait for a seek (or release) before reading on.
                while not self._stop.is_set() and generation == self._generation:
                    self._seeked.wait(0.1)
                self._seeked.clear()

    def read(self):
        """Next frame as (ok, frame), like cv2.VideoCapture.read()."""
        while True:
            try:
                generation, ok, frame, gray = self._frames.get(timeout=1.0)
            except queue.Empty:
                if not self._thread.is_alive():
                    return False, None
                continue
            if generation == self._generation:
                self.gray = gray
                return ok, frame

    def set(self, prop, value):
        """Seek (or set any other property); frames already decoded are dropped."""
        with self._lock:
            self._generation += 1
            result = self.capture.set(prop, value)
            while True:
                try:
                    self._frames.get_nowait()
                except queue.Empty:
                    break
        self._seeked.set()
        return result

    def get(self, prop):
        with self._lock:
            return self.capture.get(prop)

    def isOpened(self):
        return self.capture.isOpened()

    def release(self):
        self._stop.set()
        self._seeked.set()
        self._thread.join(timeout=2.0)
        with self._lock:
            self.capture.release()
