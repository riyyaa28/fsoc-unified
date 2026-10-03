# FSOC Coarse Alignment Control Center - Unified

One desktop app, two tabs:

- **2D BORE-SIGHT** - the full `FSOC_FINAL` PyQt5 dashboard (live sim, YOLO +
  classical detection, Kalman tracking, disturbances, plots). Code is
  unmodified from `FSOC_FINAL/ui/dashboard.py`.
- **3D AIRSPACE** - the `fsoc-tracker-clean` Three.js scene, rendered locally
  in a `QWebEngineView`. `app.js` / `style.css` are unmodified; `index.html`
  only had the now-redundant "2D BORE-SIGHT" web tab and its controls removed,
  since tab-switching is now handled by PyQt instead of the page itself.

## What changed vs. the two original projects

- `main.py` is new: it builds one `QMainWindow` with a `QTabWidget` holding
  both tabs, and closes the dashboard's performance logger on exit (its
  `closeEvent` no longer fires automatically now that it's a child widget,
  not a top-level window).
- `ui/web3d/index.html`: removed the `<nav class="view-tabs">` buttons, the
  `two-d/` stylesheet + script tags, and the 2D-only decoy-beacon controls.
  `app.js` and `style.css` are byte-for-byte the originals.
- `requirements.txt`: added `PyQtWebEngine` (needed for the 3D tab).
- `main.spec`: added `ui/web3d`, `beacon_yolo.pt`, and `yolo12n.pt` to
  `datas`, and added the `QtWebEngine*` hidden imports PyInstaller needs to
  bundle the embedded browser.
- Everything else (`sim/`, `vision/`, `control/`, `disturbance/`, `logging_/`)
  is `FSOC_FINAL`'s code, untouched, since the 2D tab is FSOC_FINAL's pipeline.

## One thing you need to do before packaging for offline use

`ui/web3d/index.html` loads Three.js from a CDN:

```html
"three": "https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js",
"three/addons/": "https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/"
```

That's fine while you're developing with an internet connection, but a
packaged .exe should not depend on a CDN being reachable. Vendor the two
files it actually uses before you build the installer:

1. Download these two files into `ui/web3d/vendor/`:
   - `https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js`
   - `https://cdn.jsdelivr.net/npm/three@0.180.0/examples/jsm/controls/OrbitControls.js`
2. In `ui/web3d/index.html`, change the importmap to point at the local files:
   ```html
   "three": "./vendor/three.module.js",
   "three/addons/": "./vendor/"
   ```
3. No spec changes needed - `('ui/web3d', 'ui/web3d')` in `main.spec`
   already covers the vendor folder once it's inside `ui/web3d`.

(I couldn't fetch these files myself while building this - no network access
in this environment - so this step is on you, but it's copy-paste simple.)

## Run it

```bash
pip install -r requirements.txt --break-system-packages
python main.py
```

## Scenarios (Benchmark 1: virtual PTZ camera)

The 2D BORE-SIGHT tab simulates the problem-statement setup
(`sim/scenario.py`): a 2000 x 2000 px screen, a 640 x 480 monochrome camera
with a 4 x 3 deg FOV at 30 Hz starting at the screen centre, pan / tilt limited
to the configured deg/s, and a square 10 x 10 px beacon starting at a random
location. Simulation time advances exactly 1/30 s per frame.

- **SCENARIO...** opens every parameter (camera, target, motion, disturbances,
  duration, seed, occlusions) with **SAVE / LOAD** of scenario `.json` files.
  **APPLY & RESET** starts a fresh run.
- Live controls: motion pattern (straight, circular, figure 8, random, spiral,
  sinusoidal, or Auto switching), decoy count, HIDE BEACON, and the
  disturbance panel (salt & pepper %, Gaussian sigma, Poisson, jitter +/- px,
  atmosphere clear / haze / fog / rain / low light with strength, platform
  motion type and px/frame).
- Acquisition: by default a wide-field finder (the whole screen at 1/4
  resolution, same noise model) cues the narrow camera, which is what makes
  the 2 s acquisition target reachable for a random start anywhere on the
  screen; "scan" uses a spiral search with the narrow camera only.
- Left view: the whole screen with the camera footprint (green = locked,
  amber = acquiring / coasting, red = searching). Right: the camera image
  with the measured centroid and the true position (magenta).
- When a timed scenario (duration > 0) ends, or on **GENERATE REPORT**, four
  files are written to `~/Downloads/fsoc-benchmark/`:
  - `scenario_<name>_<time>_frames.csv` - per frame: measured centroid, true
    position, **centroiding error**, tracking error from boresight, track
    state, camera pan / tilt, processing time
  - `..._summary.csv` - simulation duration, FPS, acquisition /
    re-acquisition time, mean / max / RMS tracking error, centroid RMSE, lock
    retention, target loss, processing time
  - `..._report.pdf` - the technical scenario report
  - `..._scenario.json` - the exact scenario including the seed used, so the
    run can be repeated bit-for-bit

Headless / batch:

```bash
python run_scenario.py scenario.json --seconds 60
python run_scenario.py --set motion=figure8 --set salt_pepper_pct=10 --set jitter_px=20 --seconds 30
```

## Video input (Benchmark 2: PTZ camera bypassed)

The coarse pointing system can take a pre-recorded video (e.g. a 30 fps
.mp4 covering the whole screen) as its camera input instead of the virtual
PTZ camera.

### In the app (2D BORE-SIGHT tab)

1. **UPLOAD VIDEO** and pick the .mp4. Playback starts immediately.
2. Ground truth is optional. A file beside the video named `<video>_gt.csv`,
   `<video>_groundtruth.csv`, `<video>.csv` (or similar) is loaded
   automatically; otherwise use **LOAD GROUND TRUTH**. Accepted layouts:
   CSV or whitespace text, with or without a header, columns
   `frame, x, y` (or `time, x, y`, or just `x, y` per row); 1-based frame
   numbers are detected; blank / NaN / negative coordinates mean the beacon
   is not visible in that frame.
3. The telemetry panel shows the live state, centroid error and RMSE (with
   ground truth), acquisition / re-acquisition time and lock retention. The
   scene view marks the measured centroid (green), the Kalman prediction
   while coasting (amber) and the ground truth (magenta).
4. When the clip ends, three files are written automatically to
   `~/Downloads/fsoc-benchmark/`:
   - `<video>_<time>_centroids.csv` - per-frame centroid in original video
     pixels, track state, SNR, boresight error (px and degrees), processing
     time and, with ground truth, the centroid error
   - `<video>_<time>_summary.csv` - RMSE (radial, x, y), mean / median /
     95th percentile / max error, acquisition and re-acquisition times, lock
     retention, target loss, processing time and FPS
   - `<video>_<time>_report.pdf` - the technical report

   **GENERATE REPORT** writes the same three files at any point mid-run.

### Batch / command line

```bash
python benchmark_video.py video1.mp4 video2.mp4           # ground truth auto-detected
python benchmark_video.py video.mp4 --gt truth.csv --out results/
```

Processes every frame as fast as possible and writes the same three files
per video. Options: `--no-yolo`, `--no-pdf`, `--fov 4x3`, `--beacon-size 10`.

### Pipeline (vision/video_tracker.py)

Monochrome conversion -> 3x3 median filter (salt & pepper) -> box matched
filter with a robust-SNR threshold that scales with the searched area ->
sub-pixel intensity-weighted centroid -> constant-velocity Kalman filter.
Whole-frame acquisition runs on a down-sampled frame, then at full
resolution for small / faint beacons, then (every 5th frame) asks YOLO for a
candidate. While tracking, only a window around the prediction is searched;
the window adapts to camera jitter. Lock is declared after 3 consistent
detections and the track coasts on the prediction for up to 15 frames.

Metric definitions: acquisition = beacon's first appearance (frame 0 without
ground truth) to first locked frame; re-acquisition = beacon visible again
(ground truth) or first re-detected (no ground truth) to next locked frame;
lock retention = locked frames / frames with the beacon visible, from the
first lock onwards (acquisition is reported separately); with ground truth a
frame only counts as locked if the centroid is within 10 px.

### Test videos with ground truth

```bash
python -m sim.benchmark_video test.mp4 --size 1920x1080 --pattern figure8 \
    --noise sp,gaussian,poisson --sp 0.10 --sigma 20 --jitter 20 \
    --beacon-size 10 --occlude 4:5
```

Writes `test.mp4` and `test_gt.csv` (exact sub-pixel centroid per frame).

## Package it as a standalone app

```bash
pyinstaller main.spec
```

The output lands in `dist/main/` (or `dist/main.exe` on Windows depending on
your PyInstaller version/options). Test that exe standalone before shipping -
QtWebEngine bundling is the one place PyInstaller occasionally needs a nudge
(if it complains about missing `QtWebEngineProcess`, add
`--collect-all PyQt5` to the `pyinstaller` command and rebuild).

To turn `dist/` into a real installer with a Start Menu entry and icon, wrap
it with [Inno Setup](https://jrsoftware.org/isinfo.php) (Windows) or
[create-dmg](https://github.com/create-dmg/create-dmg) (macOS).
