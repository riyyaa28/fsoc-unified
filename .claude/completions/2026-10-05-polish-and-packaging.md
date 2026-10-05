# 2026-10-05 - Polish, comment clean-up, Windows packaging

## Done
- Removed the look-alike decoy / designation experiment (decoy styles, initial
  cue, click-to-designate, multi-target identity tracking). Tracker and
  scenario are back to brightness / appearance identification; benchmark
  clips give the same results as before.
- Frame-rate work kept: cv2-based noise chain (Poisson via cv2.remap),
  layout-free image / text widgets, 3D page frozen while the 2D tab is shown,
  background video decoding (vision/frame_reader.py), display frames skipped
  when the loop runs late (tracking never skips), true loop-rate FPS readout.
- Small fixes: YOLO weights resolved beside the app (not the working
  directory); decoder thread stopped on window close; scenario report labels
  its processing rate correctly; spec default platform motion = linear.
- Comments trimmed to the important ones (rationale, units, spec references);
  docstrings kept.
- main.spec: one-folder build "FSOC Control Center", no UPX, ultralytics data
  files bundled. FSOC_SELFTEST=<png> runs a smoke test of a build.

## Notes
- Unused legacy modules remain (sim/scene.py, vision/kalman_tracker.py,
  classical_detector.py, fusion.py, preprocess.py, sim/virtual_camera.py,
  sim/overlay.py, control/, disturbance/, logging/, logging_/).
- Hosting the 3D view in a native window container hid the tab bar; it stays a
  plain child widget.
