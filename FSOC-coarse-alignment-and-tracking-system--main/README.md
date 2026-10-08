# App Final — FSOC Coarse Alignment Mission Control

This web application wraps the supplied `2d final` simulator modules (`sim`, `vision`, `control`, `disturbance`, and `logging_`) in a FastAPI/WebSocket service. The browser does not generate target motion or tracking state: its 2D optical view and 3D world consume the same Python telemetry message.

## How to Run the Application


## Running the Application

### Option 1: Run the Standalone Executable (`.exe`)

1. Open PowerShell or Command Prompt.
2. Go to the output folder:
```powershell
   cd dist\FSOC_Tracker
```
3. Launch the executable:
```powershell
   .\FSOC_Tracker.exe
```

The app opens in your browser. Closing the console window stops the app. Reports and uploaded videos are saved in the `FSOC_data` folder next to the executable.

### Option 2: Rebuild and Package the Executable

1. Rebuild the UI frontend:
```powershell
   cd web
   npm install
   npm run build
   cd ..
```
2. Generate the standalone executable:
```powershell
   python -m PyInstaller fsoc_app.spec
```

The new build is created in `dist\FSOC_Tracker\`. Share the whole folder, not only the `.exe`.

### Option 3: Run from Source (Development)

From the project folder, with the virtual environment set up:

```powershell
.\.venv\Scripts\python.exe -m server
```

Then open http://127.0.0.1:8011 in your browser.

Open `http://127.0.0.1:8011`.


preview of the webapp 
<img width="1768" height="906" alt="image" src="https://github.com/user-attachments/assets/6dfd2d29-b33d-4908-af46-23d5807cf465" />
<img width="1782" height="904" alt="image" src="https://github.com/user-attachments/assets/659dcf10-7636-4521-8f9f-d661eb6253db" />
<img width="1756" height="886" alt="image" src="https://github.com/user-attachments/assets/5258c2a8-0648-4b82-a42e-747674ae2a3e" />
<img width="1758" height="683" alt="image" src="https://github.com/user-attachments/assets/4bd1d82f-2716-4956-bfe6-4e4553b95605" />
<img width="1774" height="769" alt="image" src="https://github.com/user-attachments/assets/f7503710-72ec-4bd3-b627-9b051ab2995e" />



## Included behavior


- Actual `VirtualScene`, `VirtualPTZCamera`, `BeaconKalmanTracker`, `DisturbanceManager`, classical detector, optional YOLO detector, and PTZ control code from the supplied 2D project.
- One WebSocket state for the image-forming 2D camera and Three.js world.
- Hide beacon → pure world-frame Kalman prediction → reappear at the prediction position before optical reacquisition.
- Start, pause, reset, trajectory selection, 0–10 decoys, disturbance controls, live analytics, saved scenarios, CSV export and PDF report export.


## Packaging


The web app is served from one FastAPI port. The original PyInstaller specification was not rebuilt in this pass; package `python -m server` with PyInstaller for the final standalone `.exe` once the required Python environment is fixed.
