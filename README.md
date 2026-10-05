# FSOC Coarse Alignment Mission Control

AI-assisted virtual camera tracking for coarse alignment of free-space optical communication (FSOC) terminals.

The application combines a 2D camera and tracking dashboard with a 3D airspace visualization. Its simulation and vision pipeline includes a virtual PTZ camera, beacon detection, Kalman tracking, and configurable scene and sensor disturbances.

## Preview

Add screenshots to `docs/screenshots/` using these filenames. The images will appear one below another, which stays readable on narrow screens.

### 2D boresight dashboard
<img width="1917" height="1016" alt="image" src="https://github.com/user-attachments/assets/75b55240-772b-43ed-8b00-b4924f898d18" />



### 3D airspace view
<img width="1917" height="1017" alt="image" src="https://github.com/user-attachments/assets/2e626cbc-fb35-46b2-b89e-8fa24bbe8865" />


### Video benchmark

<img width="1917" height="1016" alt="image" src="https://github.com/user-attachments/assets/cb23a069-9d08-40d4-acff-c67e77d3ea83" />


### Scenario configuration

<img width="1000" height="723" alt="image" src="https://github.com/user-attachments/assets/45289474-fe11-4e48-8334-96b56a11e334" />
<img width="1000" height="731" alt="image" src="https://github.com/user-attachments/assets/5d8742cf-a983-436e-8f3b-ae5b7c77bc6f" />
<img width="1000" height="717" alt="image" src="https://github.com/user-attachments/assets/62ff9139-f607-4810-aa8c-0f5577625a35" />
<img width="1001" height="722" alt="image" src="https://github.com/user-attachments/assets/c7d4803e-8e85-4a09-bb3c-26213bf5a57b" />


## Ways to run the app

### Desktop build

- **Best for:** demos and everyday use
- **Platform:** Windows build
- **Internet:** not needed for the 2D dashboard; the 3D view currently loads Three.js from a CDN
- **Start:** run `FSOC Control Center.exe` from the packaged output folder

### From source

- **Best for:** development and experimentation
- **Platform:** Python environment; Windows is the primary supported setup
- **Internet:** needed to install packages and load the 3D CDN modules
- **Start:** run `python main.py`

## 1. Desktop app

The PyInstaller build is a folder distribution. Start `FSOC Control Center.exe` from `dist/FSOC Control Center/` after building. Keep the complete folder together because the executable uses the bundled resources beside it.

## 2. Run from source

### Requirements

- Python 3.13 (the pinned Python dependencies are tested with Python 3.13 on Windows)
- Node.js and npm, for the Three.js dependency used by the 3D view

### Setup

```powershell
# Install the local Three.js dependency
npm install

# Create and activate a Python virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install Python dependencies
python -m pip install -r requirements.txt
```

If PowerShell blocks virtual environment activation, use Command Prompt and run `.venv\Scripts\activate.bat`.

### Start the app

```powershell
python main.py
```

The main window has two tabs: **2D BORE-SIGHT** and **3D AIRSPACE**.

## Using the application

### 2D BORE-SIGHT: virtual camera scenario

Open **SCENARIO...** to configure the camera, target, motion, disturbances, duration, seed, and occlusions. Scenarios can be saved to and loaded from JSON files. Choose **APPLY & RESET** to start a new run.

During a run, configure motion patterns, decoy beacons, beacon visibility, image noise, atmospheric conditions, and platform motion. Acquisition can use a wide-field finder to cue the narrow camera, or scan mode can search with the narrow camera alone. The dashboard displays the scene, camera footprint, camera image, measured centroid, and true beacon position.

When a timed run completes, or when **GENERATE REPORT** is selected, results are saved under `~/Downloads/fsoc-benchmark/`:

- Per-frame CSV with measured and true positions, centroiding and tracking errors, track state, camera pan and tilt, and processing time
- Summary CSV with acquisition and reacquisition times, tracking statistics, lock retention, target loss, and processing metrics
- PDF technical report
- Scenario JSON containing the run configuration and random seed

Run a scenario without opening the GUI:

```bash
python run_scenario.py scenario.json --seconds 60
python run_scenario.py --set motion=figure8 --set salt_pepper_pct=10 --set jitter_px=20 --seconds 30
```

### Video benchmark

In **2D BORE-SIGHT**, choose **UPLOAD VIDEO** to process a recorded video. Ground truth is optional. The app can load a matching file beside the video, or you can choose one with **LOAD GROUND TRUTH**.

Ground-truth files can be CSV or whitespace-delimited text, with or without a header. Supported columns are `frame, x, y`, `time, x, y`, or `x, y`. Blank, NaN, and negative coordinates indicate that the beacon is not visible in that frame.

At the end of playback, the app saves per-frame centroid data, summary metrics, and a PDF report under `~/Downloads/fsoc-benchmark/`. Choose **GENERATE REPORT** to export results before playback ends.

Process video files from the command line:

```bash
python benchmark_video.py video1.mp4 video2.mp4
python benchmark_video.py video.mp4 --gt truth.csv --out results/
```

Options include `--no-yolo`, `--no-pdf`, `--fov 4x3`, and `--beacon-size 10`.

Generate a synthetic video and ground-truth file for benchmarking:

```bash
python -m sim.benchmark_video test.mp4 --size 1920x1080 --pattern figure8 \
  --noise sp,gaussian,poisson --sp 0.10 --sigma 20 --jitter 20 \
  --beacon-size 10 --occlude 4:5
```

### 3D AIRSPACE

The **3D AIRSPACE** tab displays the Three.js airspace scene inside the desktop application. It uses the local `node_modules/three` package; the page currently loads its modules from jsDelivr, so an internet connection is needed to display this tab unless those modules are vendored locally.

## Build a standalone Windows app

Install PyInstaller if needed, then build from the project directory:

```powershell
python -m pip install pyinstaller
pyinstaller main.spec
```

The output is `dist/FSOC Control Center/`. Distribute the whole folder (for example, as a ZIP) and launch `FSOC Control Center.exe` inside it. The build is intentionally a folder distribution because the application bundles large machine-learning dependencies.

## Project structure

```text
.
├── main.py                 # PyQt5 application entry point and tab container
├── ui/
│   ├── dashboard.py        # 2D scenario and video benchmark dashboard
│   └── web3d/              # Three.js airspace view
├── sim/                    # Scenario simulation and synthetic video generation
├── vision/                 # Beacon detection and tracking
├── control/                # Camera and alignment control
├── disturbance/            # Sensor and environment disturbances
├── logging_/               # Run logging
├── benchmark_video.py      # Video benchmark command-line tool
├── run_scenario.py         # Scenario benchmark command-line tool
├── requirements.txt        # Python dependencies
├── package.json            # Node.js dependencies
└── main.spec               # PyInstaller build configuration
```

## Troubleshooting

- **The 3D tab is blank:** Check the internet connection; the Three.js modules are currently loaded from jsDelivr.
- **Python reports a missing module:** Activate the virtual environment and run `python -m pip install -r requirements.txt`.
- **A packaged build fails to start:** Run the executable from inside the complete `dist/FSOC Control Center/` folder. The EXE uses bundled resources beside it.
- **Reports are not where expected:** Look under `~/Downloads/fsoc-benchmark/`, or pass `--out` to a command-line benchmark.
