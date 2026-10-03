"""Batch Benchmark-2 runner: process .mp4 files with the PTZ camera bypassed.

For every video it runs the coarse pointing pipeline on every frame, as fast
as the machine allows, and writes into the output folder:
    <stem>_centroids.csv   per-frame centroid, state, boresight error, timing
                           (and centroiding error when ground truth is given)
    <stem>_summary.csv     RMSE, acquisition / re-acquisition, lock retention,
                           FPS, processing time
    <stem>_report.pdf      the technical video tracking report

Ground truth is picked up automatically from a file beside the video with
the same stem (<stem>_gt.csv, <stem>.csv, ...), or given with --gt.

Usage:
    python benchmark_video.py video1.mp4 video2.mp4 [--gt truth.csv] [--out DIR]
                              [--no-yolo] [--fov 4x3] [--beacon-size 10]
"""

import argparse
import os
import sys
import time

import cv2

from vision.video_tracker import (
    BenchmarkMetrics, VideoBeaconTracker, find_ground_truth, load_ground_truth,
)


def default_output_dir():
    return os.path.join(os.path.expanduser("~"), "Downloads", "fsoc-benchmark")


def run_video(path, gt_path=None, out_dir=None, yolo=None, fov=(4.0, 3.0), beacon_size=10,
              write_pdf=True, progress=True):
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    gt_path = gt_path or find_ground_truth(path)
    truth = load_ground_truth(gt_path, fps) if gt_path else None
    tracker = VideoBeaconTracker(width, height, fps, beacon_size, yolo)
    metrics = BenchmarkMetrics(fps, width, height, fov, truth)

    index = 0
    started = time.perf_counter()
    while True:
        frame_started = time.perf_counter()
        ok, frame = capture.read()
        if not ok:
            break
        result = tracker.process(frame, index)
        metrics.add(result, (time.perf_counter() - frame_started) * 1000.0)
        index += 1
        if progress and index % 30 == 0:
            rate = index / (time.perf_counter() - started)
            print(f"\r  frame {index}/{total or '?'}  ({rate:.1f} fps)", end="", flush=True)
    capture.release()
    if progress:
        print()

    out_dir = out_dir or default_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(out_dir, f"{stem}_{stamp}")
    info = {
        "name": os.path.basename(path), "width": width, "height": height, "fps": fps,
        "frames": total or index, "ground_truth": gt_path,
    }
    metrics.write_csv(base + "_centroids.csv")
    summary = metrics.write_summary(base + "_summary.csv", {
        "video": path, "resolution": f"{width}x{height}", "video_fps": fps,
        "ground_truth_file": gt_path or "", "fov_deg": f"{fov[0]}x{fov[1]}",
    })
    pdf = None
    if write_pdf:
        from ui.video_report import write_video_benchmark_report
        pdf = base + "_report.pdf"
        write_video_benchmark_report(pdf, metrics, info, events=None, complete=True)
    return summary, base, pdf


def print_summary(summary):
    def show(label, key, fmt="{:.2f}", unit=""):
        value = summary.get(key)
        print(f"  {label:<28}{'--' if value is None else fmt.format(value) + unit}")

    show("frames", "frames", "{}")
    show("processing rate", "processing_fps", "{:.1f}", " fps")
    show("end-to-end (incl. decode)", "frame_fps", "{:.1f}", " fps")
    show("processing time (mean)", "processing_ms_mean", "{:.2f}", " ms")
    show("acquisition time", "acquisition_time_s", "{:.3f}", " s")
    show("re-acquisition (max)", "reacquisition_max_s", "{:.3f}", " s")
    show("lock retention", "lock_retention_pct", "{:.2f}", " %")
    show("target loss", "target_loss_pct", "{:.2f}", " %")
    if summary.get("has_ground_truth"):
        show("centroid RMSE", "rmse_px", "{:.3f}", " px")
        show("mean / max error", "mean_error_px", "{:.3f}", " px")
        show("max error", "max_error_px", "{:.3f}", " px")
        show("frames within 10 px", "within_10px_pct", "{:.2f}", " %")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("videos", nargs="+")
    parser.add_argument("--gt", help="ground-truth file (only with a single video)")
    parser.add_argument("--out", default=None, help="output folder (default ~/Downloads/fsoc-benchmark)")
    parser.add_argument("--no-yolo", action="store_true", help="skip the YOLO acquisition fallback")
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--fov", default="4x3", help="camera FOV in degrees, WxH (default 4x3)")
    parser.add_argument("--beacon-size", type=float, default=10, help="expected beacon width in px")
    args = parser.parse_args()
    if args.gt and len(args.videos) > 1:
        parser.error("--gt can only be used with a single video")
    fov = tuple(float(v) for v in args.fov.lower().split("x"))

    yolo = None
    if not args.no_yolo:
        from vision.yolo_detector import YoloBeaconDetector
        yolo = YoloBeaconDetector(weights_path=resource_path("beacon_yolo.pt"), conf_threshold=0.5)
        # Warm the model up so its one-off start-up cost is not timed as a frame.
        import numpy as np
        yolo.detect(np.zeros((320, 320, 3), np.uint8))

    app = None
    if not args.no_pdf:
        # The PDF writer needs a Qt GUI application for font metrics.
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv[:1])

    for path in args.videos:
        print(f"\n{path}")
        summary, base, pdf = run_video(path, args.gt, args.out, yolo, fov, args.beacon_size,
                                       write_pdf=not args.no_pdf)
        print_summary(summary)
        print(f"  outputs: {base}_centroids.csv, _summary.csv" + (", _report.pdf" if pdf else ""))
    del app


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


if __name__ == "__main__":
    main()
