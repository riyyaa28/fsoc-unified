"""Headless Benchmark-1 runner: execute a scenario and write its logs.

Runs the scenario as fast as the machine allows (simulation time still
advances 1/update_rate per frame) and writes into the output folder:
    <name>_<time>_frames.csv    per-frame centroid, centroiding error,
                                tracking error, track state, camera pointing
    <name>_<time>_summary.csv   RMSE, acquisition / re-acquisition, lock
                                retention, tracking error, FPS, processing time
    <name>_<time>_report.pdf    technical scenario report
    <name>_<time>_scenario.json the exact scenario (incl. the seed used)

Usage:
    python run_scenario.py scenario.json [--seconds 60] [--out DIR] [--no-yolo]
    python run_scenario.py --set motion=figure8 --set salt_pepper_pct=10 --seconds 30
"""

import argparse
import os
import sys
import time
from sim.scenario import ScenarioConfig, ScenarioRunner


def default_output_dir():
    return os.path.join(os.path.expanduser("~"), "Downloads", "fsoc-benchmark")


def output_base(cfg, out_dir=None):
    out_dir = out_dir or default_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in cfg.name) or "scenario"
    return os.path.join(out_dir, f"scenario_{safe}_{time.strftime('%Y%m%d-%H%M%S')}")


def parse_value(text, default):
    if isinstance(default, bool):
        return text.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(float(text))
    if isinstance(default, float):
        return float(text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", nargs="?", help="scenario JSON (default: built-in defaults)")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a scenario field, e.g. --set target_size_px=5")
    parser.add_argument("--seconds", type=float, help="simulated duration (overrides duration_s)")
    parser.add_argument("--out", help="output folder (default ~/Downloads/fsoc-benchmark)")
    parser.add_argument("--no-yolo", action="store_true")
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()

    cfg = ScenarioConfig.load(args.scenario) if args.scenario else ScenarioConfig()
    defaults = ScenarioConfig()
    for item in args.set:
        key, _, value = item.partition("=")
        if not hasattr(defaults, key):
            parser.error(f"unknown scenario field {key!r}")
        setattr(cfg, key, parse_value(value, getattr(defaults, key)))
    if args.seconds:
        cfg.duration_s = args.seconds
    if not cfg.duration_s:
        cfg.duration_s = 30.0

    yolo = None
    if not args.no_yolo:
        import numpy as np
        from vision.yolo_detector import YoloBeaconDetector
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        yolo = YoloBeaconDetector(weights_path=os.path.join(base_dir, "beacon_yolo.pt"), conf_threshold=0.5)
        yolo.detect(np.zeros((320, 320, 3), np.uint8))

    app = None
    if not args.no_pdf:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv[:1])

    runner = ScenarioRunner(cfg, yolo=yolo)
    started = time.perf_counter()
    while not runner.finished():
        runner.step()
        if runner.frame_index % 60 == 0:
            print(f"\r  t={runner.time_s:6.1f} s  ({runner.frame_index / (time.perf_counter() - started):.0f} fps)",
                  end="", flush=True)
    print()
    base = output_base(runner.cfg, args.out)
    from ui.scenario_report import export_scenario_outputs
    s = export_scenario_outputs(runner, base, write_pdf=not args.no_pdf)

    def show(label, key, fmt="{:.3f}", unit=""):
        value = s.get(key)
        print(f"  {label:<30}{'--' if value is None else fmt.format(value) + unit}")

    print(f"scenario '{cfg.name}' seed {runner.seed}")
    show("frames", "frames", "{}")
    show("processing rate", "processing_fps", "{:.0f}", " fps")
    show("end-to-end rate", "frame_fps", "{:.0f}", " fps")
    show("search time (beacon into FOV)", "search_time_s", "{:.2f}", " s")
    show("acquisition time", "acquisition_time_s", "{:.2f}", " s")
    show("acquisition from FOV entry", "acquisition_from_fov_s", "{:.2f}", " s")
    show("re-acquisition (max)", "reacquisition_max_s", "{:.2f}", " s")
    show("lock retention", "lock_retention_pct", "{:.2f}", " %")
    show("centroid RMSE", "rmse_px", "{:.3f}", " px")
    show("centroid max error", "max_error_px", "{:.3f}", " px")
    show("tracking error mean", "tracking_error_mean_px", "{:.2f}", " px")
    show("tracking error max", "tracking_error_max_px", "{:.2f}", " px")
    show("tracking within 10 px", "tracking_within_10px_pct", "{:.1f}", " %")
    print(f"  outputs: {base}_frames.csv / _summary.csv / _scenario.json" + ("" if args.no_pdf else " / _report.pdf"))
    del app


if __name__ == "__main__":
    main()
