# FSOC Coarse Alignment Mission Control

AI-based virtual camera tracking for the coarse alignment of mobile free-space optical communication (FSOC) terminals.

A Python simulation (virtual scene, PTZ camera, beacon detection, Kalman tracking, disturbances) runs in a FastAPI/WebSocket server. A React + Three.js interface shows the 2D camera view, the 3D world, live metrics and reports, all fed by the same telemetry.

**Live web app:** https://fsoc-coarse-alignment-tracker.onrender.com

---

## Ways to run the app

| | Desktop app (`.exe`) | Web app (online) | From source |
|---|---|---|---|
| **Best for** | Everyday use, demos, offline | Quick access from any device | Development |
| **Needs** | Windows 10/11 (64-bit) | A browser + internet | Python 3.12+, Node.js 20+ |
| **Internet** | Not needed | Required | Only to install packages |
| **Install** | None: double-click | None (optional "Install app") | See below |

---

## 1. Desktop app (Windows `.exe`)

The whole app (server, simulation and interface) is packed into one file: [`release/FSOC_Tracker.exe`](release/FSOC_Tracker.exe).

### Run it

1. **Download** [FSOC_Tracker.exe](https://github.com/shruti12719/FSOC-coarse-alignment-and-tracking-system-/raw/main/release/FSOC_Tracker.exe) (about 80 MB).
   You can also use the **Get the app → Download for Windows** button on the website.
2. **Move it into its own folder**, for example `Desktop\FSOC Tracker\`. The app saves its data next to itself.
3. **Double-click `FSOC_Tracker.exe`.**
   - Windows may show **"Windows protected your PC"** because the file is not code-signed. Click **More info → Run anyway**.
   - The first launch takes 10–30 seconds while it unpacks; later launches are faster.
4. The app opens in its own window. **Close the window to exit**; this stops everything.

No Python, Node.js, internet connection or installation is required. To share the app, send the `.exe` file. It is too large for email, so use Google Drive, a pen drive or similar.

### Where your data is saved

A `FSOC_data` folder is created next to the `.exe`:

```
FSOC_data/
├── reports/     PDF, CSV and JSON performance reports
├── scenarios/   saved scenarios
├── uploads/     videos uploaded to the Video Benchmark
└── logs/        app.log (useful for troubleshooting)
```

### Good to know

- Each computer runs its own independent copy; data is not shared between computers.
- If port 8011 is busy (for example, the app is already open), it automatically uses the next free port.
- The window uses Microsoft Edge WebView2, which is built into Windows 10 and 11. If it is missing, the app opens in your default browser instead.
- YOLO detection is not included in the `.exe` (to keep it small). The classical detector is used instead, and the interface says so.

---

## 2. Web app (online)

Open **https://fsoc-coarse-alignment-tracker.onrender.com** in any modern browser on a laptop, desktop, tablet or phone.

- **First load can take 30–60 seconds.** The free hosting plan puts the app to sleep after 15 minutes without visitors, and the first visit wakes it up.
- **Install it as an app:** a pop-up offers **Install web app** (Chrome, Edge, Android) or, on iPhone and iPad, **Share → Add to Home Screen**. The installed app opens in its own window but still needs internet.
- **Live updates pause** when the tab is in the background for 1 minute or untouched for 15 minutes, to save hosting bandwidth. Click anywhere to resume.
- **Online files are temporary:** reports, scenarios and uploaded videos are deleted whenever the server restarts. Download the reports you want to keep, or use the desktop app.

---

## 3. Run from source (development)

### Prerequisites

- [Python 3.12+](https://www.python.org/downloads/)
- [Node.js 20+](https://nodejs.org/) (only needed to build the interface)
- Git

### Steps

```powershell
# 1. Get the code
git clone https://github.com/shruti12719/FSOC-coarse-alignment-and-tracking-system-.git
cd FSOC-coarse-alignment-and-tracking-system-

# 2. Create a virtual environment and install the Python packages
python -m venv .venv
.\.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 3. Build the web interface
cd web
npm ci
npm run build
cd ..

# 4. Start the server
python -m server
```

Open **http://127.0.0.1:8011** in your browser. Press `Ctrl+C` in the terminal to stop.

To open the app in a desktop window instead, the same way the `.exe` does, run `python launcher.py` (needs `pip install pywebview`).

### Live reload while editing the interface

Run the server (`python -m server`) in one terminal and the Vite dev server in another:

```powershell
cd web
npm run dev
```

Open **http://127.0.0.1:5173**. API and WebSocket calls are forwarded to the Python server on port 8011.

### Optional: YOLO detection

```powershell
pip install ultralytics
```

With `ultralytics` installed, the YOLO setting uses the trained model `beacon_yolo.pt`. Without it, the classical ring-signature detector is used.

---

## Using the app

1. **Mission Control:** press **Start**, pick a beacon and press **Connect**. Use **Hide beacon** to test re-acquisition; the Kalman filter predicts the beacon's position while it is hidden.
2. **3D Simulation:** the same state shown as a 3D world with beacon trails and the camera's line of sight.
3. **Video Benchmark:** upload an MP4, AVI, MOV or MKV video to run the same tracking core on real footage.
4. **Scenario:** load presets, adjust them and save your own scenarios.
5. **Settings:** camera, disturbances (haze, fog, rain, low light, turbulence), decoys and tracking parameters.
6. **Performance:** live metrics and **PDF / CSV / JSON** report export.

---

## Rebuilding the `.exe`

After changing the code, run this from the project folder with the virtual environment active:

```powershell
cd web
npm ci
npm run build
cd ..
pip install pyinstaller pywebview
python -m PyInstaller fsoc_app.spec
```

The new file is `dist\FSOC_Tracker.exe`. Copy it to `release\FSOC_Tracker.exe` and commit it to update the download link.

To troubleshoot the `.exe`, set `FSOC_DEBUG=1` before starting it. Every request is then logged to `FSOC_data\logs\app.log`:

```powershell
$env:FSOC_DEBUG = "1"; .\FSOC_Tracker.exe
```

---

## Deploying to Render

The repository includes a `Dockerfile` and `render.yaml`. The Docker build compiles the React interface and serves it from the FastAPI server on one port. The health check path is `/api/health`.

- **New service:** in the Render dashboard choose **New → Blueprint** and select this repository, or use the Render CLI:

  ```bash
  render services create --name fsoc-coarse-alignment-tracker --type web_service \
    --repo https://github.com/shruti12719/FSOC-coarse-alignment-and-tracking-system- \
    --branch main --runtime docker --plan free --region singapore \
    --health-check-path /api/health
  ```

  In Git Bash on Windows, put `MSYS_NO_PATHCONV=1` in front of the command so `/api/health` is not turned into a Windows path.

- **Redeploy after pushing.** This is needed unless the repository is connected to Render through GitHub:

  ```bash
  render deploys create <service-id>
  ```

### Staying within the free plan

The free plan has monthly limits on outbound bandwidth, instance hours (750) and build minutes. Going over the bandwidth limit suspends the workspace for the rest of the month.

- Live telemetry is the main bandwidth cost: about 0.2 GB per hour for each tab watching a running simulation. Hidden and idle tabs stop streaming automatically.
- Use the desktop app for long demos or presentations.
- Keep only one free service in the workspace, and don't use keep-alive "pinger" services.
- Check **Billing → Usage** in the Render dashboard regularly.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8011` (Render sets `10000`) | Server port |
| `HOST` | `0.0.0.0` | Server bind address (`python -m server`) |
| `FSOC_STREAM_HZ` | `0` (no limit); `10` in the Dockerfile | Maximum telemetry updates per second sent to each browser |
| `FSOC_DEBUG` | not set | Desktop app: log every request to `FSOC_data/logs/app.log` |

---

## Project structure

```
├── server/          FastAPI app: REST API, WebSocket telemetry, reports, video benchmark
├── sim/             Virtual scene, sky, beacon trajectories, virtual PTZ camera rendering
├── vision/          Classical and YOLO detectors, fusion, Kalman tracker
├── control/         PTZ controller
├── disturbance/     Atmospheric and sensor disturbance models
├── logging_/        Session logging
├── web/             React + Three.js interface (Vite)
├── release/         FSOC_Tracker.exe, the ready-to-run Windows app
├── launcher.py      Desktop launcher (starts the server and opens the app window)
├── fsoc_app.spec    PyInstaller build definition for the .exe
├── beacon_yolo.pt   Trained YOLO beacon model (optional)
├── Dockerfile       Container build used by Render
└── render.yaml      Render Blueprint
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Windows protected your PC" | Click **More info → Run anyway**. The `.exe` is not code-signed. |
| The `.exe` window stays white | Wait up to 30 s on the first launch. If it stays white, check `FSOC_data\logs\app.log`. |
| The website takes a long time to open | The free server is waking up. Wait 30–60 s and reload. |
| "Live updates paused" | The tab was hidden or idle. Click anywhere to resume. |
| "Backend unavailable" when running from source | Start the server with `python -m server` and open http://127.0.0.1:8011. |
| Blank page when running from source | Build the interface first: `cd web && npm ci && npm run build`. |
| Port 8011 already in use | Close the other copy, or run `$env:PORT = "8020"; python -m server`. |

---

## Preview

<img width="1768" height="906" alt="Mission Control" src="https://github.com/user-attachments/assets/6dfd2d29-b33d-4908-af46-23d5807cf465" />
<img width="1782" height="904" alt="3D Simulation" src="https://github.com/user-attachments/assets/659dcf10-7636-4521-8f9f-d661eb6253db" />
<img width="1756" height="886" alt="Video Benchmark" src="https://github.com/user-attachments/assets/5258c2a8-0648-4b82-a42e-747674ae2a3e" />
<img width="1758" height="683" alt="Scenario and Settings" src="https://github.com/user-attachments/assets/4bd1d82f-2716-4956-bfe6-4e4553b95605" />
<img width="1774" height="769" alt="Performance" src="https://github.com/user-attachments/assets/f7503710-72ec-4bd3-b627-9b051ab2995e" />

---

## Features

- Uses the original `VirtualScene`, `VirtualPTZCamera`, `BeaconKalmanTracker`, `DisturbanceManager`, classical detector, optional YOLO detector and PTZ control code from the 2D project.
- One WebSocket state drives both the image-forming 2D camera and the Three.js world.
- Hide beacon → pure world-frame Kalman prediction → the beacon reappears at the predicted position before optical re-acquisition.
- Start, pause, reset, trajectory selection, 0–10 decoys, disturbance controls, live analytics, saved scenarios, CSV export and PDF report export.
- Video benchmark: runs the same tracking core on uploaded footage.
