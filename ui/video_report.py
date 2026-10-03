"""Technical report for a video run (Benchmark 2: PTZ camera bypassed).

Shared by the dashboard's GENERATE REPORT button, the automatic end-of-video
export and the command-line runner (benchmark_video.py). Tone is neutral:
measured values are shown next to the reference figures from the problem
statement, without pass/fail judgements.
"""

from datetime import datetime

from ui.report_pdf import TechnicalReport
from vision.video_tracker import COAST, LOCKED, SEARCH, TENTATIVE, VideoBeaconTracker

TRACKING_EVENT_LIMIT = 300


def _fmt(value, digits, unit=""):
    if value is None:
        return "--"
    return f"{value:.{digits}f}{unit}"


def tracking_events(metrics):
    """Track state changes as (video time, event, details) rows."""
    rows = []
    previous = None
    for r in metrics.results:
        if r.state == previous:
            continue
        t = f"{r.time_s:8.3f} s"
        position = f" at ({r.x:.1f}, {r.y:.1f}) px" if r.x is not None else ""
        if r.state == TENTATIVE and previous in (None, SEARCH):
            rows.append((t, "DETECTION", f"Beacon candidate detected{position}, frame {r.frame} ({r.source})"))
        elif r.state == LOCKED and previous in (None, SEARCH, TENTATIVE):
            rows.append((t, "LOCK", f"Track confirmed{position}, frame {r.frame}"))
        elif r.state == LOCKED and previous == COAST:
            rows.append((t, "LOCK", f"Measurement resumed{position}, frame {r.frame}"))
        elif r.state == COAST:
            rows.append((t, "COAST", f"No measurement from frame {r.frame}; following Kalman prediction"))
        elif r.state == SEARCH and previous is not None:
            rows.append((t, "SEARCH", f"Track dropped at frame {r.frame}; whole-frame search"))
        previous = r.state
    return rows


def write_video_benchmark_report(path, metrics, info, events=None, complete=True):
    """Write the PDF.

    metrics: vision.video_tracker.BenchmarkMetrics
    info:    dict with name, width, height, fps, frames and ground_truth (path or None)
    events:  optional session log rows (time, event, details) from the dashboard
    """
    s = metrics.summary()
    now = datetime.now()
    report_id = now.strftime("FSOC-%Y%m%d-%H%M%S")
    has_truth = s["has_ground_truth"]
    frames = s["frames"]
    video_s = s["video_duration_s"]
    total = info.get("frames")
    clip_s = total / info["fps"] if total and info.get("fps") else None
    coverage = f"{frames} of {total} frames" if total else f"{frames} frames"
    fov_w, fov_h = metrics.fov_deg

    report = TechnicalReport(
        path, "Video Tracking Report",
        "Benchmark video processed with the PTZ camera bypassed - centroiding and tracking performance",
        report_id, now.strftime("%Y-%m-%d %H:%M:%S"), f"{video_s:.1f} s of video",
    )
    report.key_figures([
        ("CENTROID RMSE", _fmt(s.get("rmse_px"), 2, " px") if has_truth else "no truth file"),
        ("LOCK RETENTION", _fmt(s.get("lock_retention_pct"), 1, " %")),
        ("ACQUISITION", _fmt(s.get("acquisition_time_s"), 2, " s")),
        ("PROCESSING RATE", _fmt(s.get("processing_fps"), 0, " fps")),
    ])

    # 1 ----------------------------------------------------------------
    report.heading(1, "Run Overview")
    if not frames:
        report.paragraph("No frames had been processed when this report was generated.")
    else:
        text = (
            f"{info.get('name', 'The video')} ({info.get('width')} x {info.get('height')} px, "
            f"{_fmt(info.get('fps'), 2)} fps) was fed directly into the coarse pointing pipeline with the "
            f"virtual PTZ camera bypassed. {coverage} ({video_s:.1f} s of video) were processed"
            f"{' - the complete clip' if complete else ''}. "
            f"The beacon was locked in {_fmt(s.get('lock_retention_pct'), 1, ' %')} of "
            f"{'frames in which it was visible' if has_truth else 'frames'}; acquisition took "
            f"{_fmt(s.get('acquisition_time_s'), 3, ' s')} and {len(s['reacquisition_events'])} "
            f"re-acquisition(s) were recorded. "
        )
        if has_truth:
            text += (f"Against the ground-truth file, the centroid RMSE was {_fmt(s.get('rmse_px'), 3, ' px')} "
                     f"(maximum {_fmt(s.get('max_error_px'), 2, ' px')}) over {s['error_samples']} measured frames. ")
        else:
            text += ("No ground-truth file was supplied, so the per-frame centroid log is provided for "
                     "comparison against the reference values. ")
        text += (f"Mean processing time was {_fmt(s.get('processing_ms_mean'), 2, ' ms')} per frame "
                 f"({_fmt(s.get('processing_fps'), 0, ' fps')}).")
        report.paragraph(text)

    # 2 ----------------------------------------------------------------
    report.heading(2, "Video Source and Configuration")
    report.table([("PARAMETER", 1), ("VALUE", 1.7)], [
        ["File", info.get("name", "--")],
        ["Resolution", f"{info.get('width')} x {info.get('height')} px"],
        ["Frame rate", _fmt(info.get("fps"), 2, " fps")],
        ["Clip length", f"{total} frames ({clip_s:.2f} s)" if clip_s else "--"],
        ["Frames processed", coverage],
        ["Ground truth", info.get("ground_truth") or "None supplied"],
        ["Camera", "PTZ bypassed - each video frame is the camera image"],
        ["Field of view", f"{fov_w:g} x {fov_h:g} deg ({fov_w / metrics.width * 3600:.2f} x "
                          f"{fov_h / metrics.height * 3600:.2f} arcsec per px)"],
        ["Pre-processing", "Monochrome conversion, 3 x 3 median filter (salt & pepper removal)"],
        ["Detection", "Box matched filter with robust SNR test; threshold scales with search area"],
        ["AI assistance", f"YOLO acquisition fallback ({s['yolo_frames']} frame(s) used it)"],
        ["Centroiding", "Background-subtracted intensity-weighted centroid, sub-pixel"],
        ["Tracking", f"Constant-velocity Kalman filter; lock confirmed after "
                     f"{VideoBeaconTracker.CONFIRM_FRAMES} detections; coasts up to "
                     f"{VideoBeaconTracker.MAX_COAST_FRAMES} frames"],
    ])

    # 3 ----------------------------------------------------------------
    report.heading(3, "Centroiding Accuracy")
    if has_truth and s.get("error_samples"):
        report.note(f"Centroid error = measured centroid minus ground truth, over {s['error_samples']} frames "
                    "with both a measurement and a visible beacon. The reference column lists the figure "
                    "from the problem statement for context.")
        report.table([("METRIC", 1.4), ("MEASURED", 1), ("REFERENCE", 1.1)], [
            ["RMSE (radial)", _fmt(s["rmse_px"], 3, " px"), "10 px tracking error"],
            ["RMSE x / y", f"{s['rmse_x_px']:.3f} / {s['rmse_y_px']:.3f} px", "--"],
            ["Mean error", _fmt(s["mean_error_px"], 3, " px"), "10 px tracking error"],
            ["Median error", _fmt(s["median_error_px"], 3, " px"), "--"],
            ["95th percentile error", _fmt(s["p95_error_px"], 3, " px"), "--"],
            ["Maximum error", _fmt(s["max_error_px"], 3, " px"), "10 px tracking error"],
            ["Bias x / y", f"{s['bias_x_px']:+.3f} / {s['bias_y_px']:+.3f} px", "--"],
            ["Frames within 10 px", _fmt(s["within_10px_pct"], 2, " %"), "--"],
        ])
    else:
        report.note("No ground-truth file was loaded, so centroiding error cannot be computed here. "
                    "Every measured centroid is written to the centroid log (Section 8) in original video "
                    "pixel coordinates for comparison against reference values. Load a ground-truth file "
                    "(frame, x, y) to have RMSE and error statistics computed automatically.")

    # 4 ----------------------------------------------------------------
    report.heading(4, "Tracking Performance")
    events_list = s["reacquisition_events"]
    report.table([("METRIC", 1.4), ("MEASURED", 1), ("REFERENCE", 1.1)], [
        ["Acquisition time", _fmt(s.get("acquisition_time_s"), 3, " s"), "2 s"],
        ["Re-acquisition time (mean)", _fmt(s.get("reacquisition_mean_s"), 3, " s"), "1 s"],
        ["Re-acquisition time (max)", _fmt(s.get("reacquisition_max_s"), 3, " s"), "1 s"],
        ["Re-acquisition events", str(len(events_list)), "--"],
        ["Lock retention", _fmt(s.get("lock_retention_pct"), 2, " %"), "95 %"],
        ["Target loss", _fmt(s.get("target_loss_pct"), 2, " %"), "5 %"],
        ["Processing time (mean / p95)",
         f"{_fmt(s.get('processing_ms_mean'), 2)} / {_fmt(s.get('processing_ms_p95'), 2, ' ms')}", "50 ms (20 FPS)"],
        ["Processing time (max)", _fmt(s.get("processing_ms_max"), 2, " ms"), "--"],
        ["Processing rate", _fmt(s.get("processing_fps"), 1, " fps"), "20 FPS"],
        ["End-to-end rate (incl. decode)", _fmt(s.get("frame_fps"), 1, " fps"), "30 Hz camera update"],
    ])
    report.note("Acquisition is measured from the beacon's first appearance (frame 0 without ground truth) "
                "to the first locked frame. Re-acquisition is measured from the beacon becoming visible "
                "again (ground truth) or first being re-detected (no ground truth) to the next locked frame, "
                "so time spent hidden is excluded. Lock retention and target loss cover the frames from the "
                "first lock onwards in which the beacon is visible. "
                + ("With ground truth, a frame counts as locked only when the centroid is within 10 px."
                   if has_truth else ""))

    # 5 ----------------------------------------------------------------
    if events_list:
        report.heading(5, "Re-acquisition Events")
        report.table([("LOST AT", 1), ("RE-LOCKED AT", 1), ("OUTAGE", 1), ("RE-ACQUISITION", 1)], [
            [f"frame {e['lost_frame']} ({e['lost_frame'] / metrics.fps:.2f} s)",
             f"frame {e['relock_frame']} ({e['relock_frame'] / metrics.fps:.2f} s)",
             f"{e['outage_s']:.3f} s", f"{e['reacquisition_s']:.3f} s"]
            for e in events_list
        ])
        next_section = 6
    else:
        next_section = 5

    # 6 ----------------------------------------------------------------
    report.heading(next_section, "Time-Series Analysis")
    report.note("Per-frame values against video time. Dashed lines mark reference values.")
    results = metrics.results
    # Same definition as the headline figure: frames where the beacon is not
    # visible (ground truth) are left out.
    locked_running, locked, visible = [], 0, 0
    first_lock = s.get("first_lock_frame")
    for r in results:
        if first_lock is None or r.frame < first_lock or (has_truth and metrics.truth(r.frame) is None):
            continue
        visible += 1
        locked += int(metrics.is_locked(r))
        locked_running.append((r.time_s, locked / visible * 100))
    first_chart = (
        ("CENTROID ERROR", "px vs ground truth",
         [(r.time_s, e[2]) for r in results for e in [metrics.centroid_error(r)] if e], "#e0603f", 10.0)
        if has_truth else
        ("DETECTION SNR", "robust SNR", [(r.time_s, r.snr) for r in results if r.measured], "#e0603f", None)
    )
    report.chart_grid([
        first_chart,
        ("BEACON POSITION X", "px", [(r.time_s, r.x) for r in results if r.measured], "#1b9fc4", None),
        ("LOCK RETENTION (RUNNING)", "%", locked_running, "#138a52", 95.0),
        ("PROCESSING TIME", "ms / frame", [(r.time_s, r.processing_ms) for r in results], "#7a5cd6", 50.0),
    ], "video time")

    # 7 ----------------------------------------------------------------
    report.heading(next_section + 1, "Tracking Events")
    rows = tracking_events(metrics)
    report.note(f"{len(rows)} track state change(s), timed in video seconds."
                + (f" The first {TRACKING_EVENT_LIMIT} are listed." if len(rows) > TRACKING_EVENT_LIMIT else ""))
    if rows:
        report.table([("VIDEO TIME", 0.8), ("EVENT", 0.8), ("DETAILS", 3.4)],
                     [list(row) for row in rows[:TRACKING_EVENT_LIMIT]], size=8)
    if events:
        report.note("Session log from the dashboard:")
        report.table([("TIME", 0.8), ("EVENT", 0.8), ("DETAILS", 3.4)], [list(row) for row in events[-200:]], size=8)

    # 8 ----------------------------------------------------------------
    report.heading(next_section + 2, "Centroid Log Format")
    report.note("The per-frame centroid log (_centroids.csv) is written beside this report. "
                "Coordinates are original video pixels with pixel centres at integer values, origin at the "
                "top-left of the frame.")
    report.table([("COLUMN", 1), ("MEANING", 3)], [
        ["frame, time_s", "Frame index (0-based) and video time"],
        ["state", "SEARCH, TENTATIVE, LOCKED or COAST"],
        ["measured", "1 if a centroid was measured in this frame"],
        ["x_px, y_px", "Measured centroid (or Kalman prediction while coasting)"],
        ["source, snr", "MATCHED filter / YOLO / PREDICTED, and detection SNR"],
        ["beacon_size_px", "Estimated beacon width"],
        ["boresight_dx/dy_px", "Offset of the beacon from the image centre"],
        ["az/el_error_deg", "Same offset in degrees using the configured FOV"],
        ["processing_ms", "Pipeline time for the frame"],
        ["gt_x/y_px, error_*", "Ground truth and centroid error, when a truth file is loaded"],
    ])

    # 9 ----------------------------------------------------------------
    report.heading(next_section + 3, "Observations")
    if frames:
        report.bullet(f"{s['measured_frames']} of {frames} frames produced a centroid measurement.")
        if has_truth and s.get("error_samples"):
            report.bullet(f"Centroid error ranged up to {s['max_error_px']:.2f} px, with "
                          f"{s['within_10px_pct']:.1f} % of measured frames within 10 px.")
        if events_list:
            report.bullet(f"{len(events_list)} re-acquisition(s); the longest took {s['reacquisition_max_s']:.3f} s "
                          f"after the beacon was back in view.")
        if s.get("unrecovered_loss"):
            report.bullet("The track was not locked at the end of the processed segment.")
        report.bullet(f"Processing averaged {_fmt(s.get('processing_ms_mean'), 2, ' ms')} per frame, "
                      f"with a 95th percentile of {_fmt(s.get('processing_ms_p95'), 2, ' ms')}.")
    else:
        report.bullet("No frames have been processed yet.")
    report.finish()
    return s
