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
