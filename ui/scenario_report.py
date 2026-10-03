"""Technical report for a simulated scenario (Benchmark 1).

Shared by the dashboard (GENERATE REPORT and the automatic export at the end
of a timed scenario) and run_scenario.py. Neutral tone: measured values are
listed next to the reference figures from the problem statement.
"""

from datetime import datetime

from ui.report_pdf import TechnicalReport
from ui.video_report import TRACKING_EVENT_LIMIT, _fmt, tracking_events
from vision.video_tracker import VideoBeaconTracker


def write_scenario_report(path, runner, events=None, complete=True):
    cfg = runner.cfg
    metrics = runner.metrics
    s = metrics.summary()
    now = datetime.now()
    report_id = now.strftime("FSOC-%Y%m%d-%H%M%S")
    frames = s["frames"]
    sim_s = s["video_duration_s"]

    report = TechnicalReport(
        path, "Scenario Tracking Report",
        f"Scenario '{cfg.name}' - virtual PTZ camera tracking a beacon on a "
        f"{cfg.screen_width} x {cfg.screen_height} px screen",
        report_id, now.strftime("%Y-%m-%d %H:%M:%S"), f"{sim_s:.1f} s simulated",
    )
    report.key_figures([
        ("ACQUISITION", _fmt(s.get("acquisition_time_s"), 2, " s")),
        ("LOCK RETENTION", _fmt(s.get("lock_retention_pct"), 1, " %")),
        ("TRACKING ERROR", _fmt(s.get("tracking_error_mean_px"), 1, " px")),
        ("FRAME RATE", _fmt(s.get("frame_fps"), 0, " fps")),
    ])

    # 1 ----------------------------------------------------------------
    report.heading(1, "Run Overview")
    if not frames:
        report.paragraph("No frames had been simulated when this report was generated.")
    else:
        text = (
            f"The scenario ran for {sim_s:.1f} s of simulated time ({frames} frames at "
            f"{cfg.update_rate_hz:g} Hz){', the full configured duration' if complete and cfg.duration_s else ''}. "
            f"The camera started at the centre of the screen and the beacon at "
            f"{'a random location' if cfg.initial_target == 'random' else cfg.initial_target}; the beacon entered "
            f"the field of view after {_fmt(s.get('search_time_s'), 2, ' s')} and was locked after "
            f"{_fmt(s.get('acquisition_time_s'), 2, ' s')}. From then on it was locked in "
            f"{_fmt(s.get('lock_retention_pct'), 1, ' %')} of frames in which it was visible. "
            f"Centroiding error against the simulated truth had an RMSE of {_fmt(s.get('rmse_px'), 3, ' px')}, "
            f"and the beacon stayed on average {_fmt(s.get('tracking_error_mean_px'), 2, ' px')} from the camera "
            f"boresight. The loop ran at {_fmt(s.get('frame_fps'), 0, ' fps')} end to end "
            f"(tracker {_fmt(s.get('processing_ms_mean'), 2, ' ms')} per frame)."
        )
        report.paragraph(text)

    # 2 ----------------------------------------------------------------
    report.heading(2, "Scenario Configuration")
    report.table([("PARAMETER", 1), ("VALUE", 2)], cfg.rows() + [["Random seed used", str(runner.seed)]])

    # 3 ----------------------------------------------------------------
    report.heading(3, "Processing Pipeline")
    report.table([("STAGE", 1), ("METHOD", 2.2)], [
        ["Image formation", "Camera window cropped from the screen at the commanded pan / tilt plus jitter; "
                            "atmosphere, then Poisson, Gaussian and salt & pepper noise"],
        ["Pre-processing", "Monochrome, 3 x 3 median filter"],
        ["Detection", "Box matched filter, robust SNR threshold scaled with search area; YOLO acquisition "
                      f"fallback on a worker thread ({s['yolo_frames']} frame(s) used it)"],
        ["Centroiding", "Background-subtracted intensity-weighted centroid, sub-pixel"],
        ["Tracking", f"Constant-velocity Kalman filter with camera-motion compensation; lock after "
                     f"{VideoBeaconTracker.CONFIRM_FRAMES} detections; coasts up to "
                     f"{VideoBeaconTracker.MAX_COAST_FRAMES} frames"],
        ["Pointing", "Proportional control on the filtered position plus velocity feed-forward, "
                     "rate-limited to the pan / tilt speed; spiral search when no track"],
    ])

    # 4 ----------------------------------------------------------------
    report.heading(4, "Performance Metrics")
    report.note("Measured values; the reference column lists the problem-statement figures for context.")
    events_list = s["reacquisition_events"]
    report.table([("METRIC", 1.5), ("MEASURED", 1), ("REFERENCE", 1)], [
        ["Simulation duration", f"{sim_s:.2f} s ({frames} frames)", "--"],
        ["Search time (beacon into FOV)", _fmt(s.get("search_time_s"), 2, " s"), "--"],
        ["Acquisition time", _fmt(s.get("acquisition_time_s"), 3, " s"), "2 s"],
        ["Acquisition after FOV entry", _fmt(s.get("acquisition_from_fov_s"), 3, " s"), "--"],
        ["Re-acquisition time (mean / max)",
         f"{_fmt(s.get('reacquisition_mean_s'), 3)} / {_fmt(s.get('reacquisition_max_s'), 3, ' s')}", "1 s"],
        ["Re-acquisition events", str(len(events_list)), "--"],
        ["Lock retention", _fmt(s.get("lock_retention_pct"), 2, " %"), "95 %"],
        ["Target loss", _fmt(s.get("target_loss_pct"), 2, " %"), "5 %"],
        ["Tracking error (mean / RMS)",
         f"{_fmt(s.get('tracking_error_mean_px'), 2)} / {_fmt(s.get('tracking_error_rms_px'), 2, ' px')}", "10 px"],
        ["Tracking error (95th pct / max)",
         f"{_fmt(s.get('tracking_error_p95_px'), 2)} / {_fmt(s.get('tracking_error_max_px'), 2, ' px')}", "10 px"],
        ["Frames with tracking error <= 10 px", _fmt(s.get("tracking_within_10px_pct"), 1, " %"), "--"],
        ["Frame rate (end to end)", _fmt(s.get("frame_fps"), 1, " fps"), "30 Hz camera update"],
        ["Processing time (mean / p95)",
         f"{_fmt(s.get('processing_ms_mean'), 2)} / {_fmt(s.get('processing_ms_p95'), 2, ' ms')}", "50 ms (20 FPS)"],
    ])
    report.note("Tracking error is the distance between the true beacon position in the camera image and "
                "the image centre (boresight), from the first lock onwards while the beacon is visible; camera "
                "jitter contributes to it directly. Acquisition is timed from the start of the run; lock "
                "retention covers frames from the first lock onwards, and a frame counts as locked only when "
                "the measured centroid is within 10 px of the truth.")

    # 5 ----------------------------------------------------------------
    report.heading(5, "Centroiding Error")
    if s.get("error_samples"):
        report.note(f"Measured centroid minus simulated truth in the camera image, over {s['error_samples']} "
                    "frames with a measurement.")
        report.table([("METRIC", 1.5), ("MEASURED", 1), ("REFERENCE", 1)], [
            ["RMSE (radial)", _fmt(s["rmse_px"], 3, " px"), "10 px"],
            ["RMSE x / y", f"{s['rmse_x_px']:.3f} / {s['rmse_y_px']:.3f} px", "--"],
            ["Mean / median error", f"{s['mean_error_px']:.3f} / {s['median_error_px']:.3f} px", "--"],
            ["95th percentile / max error", f"{s['p95_error_px']:.3f} / {s['max_error_px']:.3f} px", "10 px"],
            ["Bias x / y", f"{s['bias_x_px']:+.3f} / {s['bias_y_px']:+.3f} px", "--"],
            ["Frames within 10 px", _fmt(s["within_10px_pct"], 2, " %"), "--"],
        ])
    else:
        report.note("No centroid measurements yet.")

    next_section = 6
    if events_list:
        report.heading(next_section, "Re-acquisition Events")
        report.table([("LOST AT", 1), ("RE-LOCKED AT", 1), ("OUTAGE", 1), ("RE-ACQUISITION", 1)], [
            [f"{e['lost_frame'] / metrics.fps:.2f} s", f"{e['relock_frame'] / metrics.fps:.2f} s",
             f"{e['outage_s']:.3f} s", f"{e['reacquisition_s']:.3f} s"] for e in events_list
        ])
        next_section += 1

    # charts -----------------------------------------------------------
    report.heading(next_section, "Time-Series Analysis")
    report.note("Per-frame values against simulation time. Dashed lines mark reference values.")
    results = metrics.results
    first_lock = s.get("first_lock_frame")
    locked_running, locked, visible = [], 0, 0
    for r in results:
        if first_lock is None or r.frame < first_lock or metrics.truth(r.frame) is None:
            continue
        visible += 1
        locked += int(metrics.is_locked(r))
        locked_running.append((r.time_s, locked / visible * 100))
    report.chart_grid([
        ("CENTROID ERROR", "px vs truth",
         [(r.time_s, e[2]) for r in results for e in [metrics.centroid_error(r)] if e], "#e0603f", 10.0),
        ("TRACKING ERROR", "px from boresight",
         [(frame / metrics.fps, err) for frame, err in metrics.tracking_errors], "#1b9fc4", 10.0),
        ("LOCK RETENTION (RUNNING)", "%", locked_running, "#138a52", 95.0),
        ("FRAME TIME", "ms end to end", [(r.time_s, ms) for r, ms in zip(results, metrics.frame_ms)],
         "#7a5cd6", 1000.0 / cfg.update_rate_hz),
    ], "simulation time")

    # events -------------------------------------------------------------
    report.heading(next_section + 1, "Tracking Events")
    rows = tracking_events(metrics)
    report.note(f"{len(rows)} track state change(s), timed in simulation seconds."
                + (f" The first {TRACKING_EVENT_LIMIT} are listed." if len(rows) > TRACKING_EVENT_LIMIT else ""))
    if rows:
        report.table([("SIM TIME", 0.8), ("EVENT", 0.8), ("DETAILS", 3.4)],
                     [list(row) for row in rows[:TRACKING_EVENT_LIMIT]], size=8)
    if events:
        report.note("Session log from the dashboard:")
        report.table([("TIME", 0.8), ("EVENT", 0.8), ("DETAILS", 3.4)], [list(row) for row in events[-200:]], size=8)

    # log format -----------------------------------------------------------
    report.heading(next_section + 2, "Performance Log Format")
    report.note("The per-frame log (_frames.csv) and summary (_summary.csv) are written beside this report, "
                "with the exact scenario (_scenario.json, including the seed) so the run can be repeated.")
    report.table([("COLUMN", 1), ("MEANING", 3)], [
        ["frame, time_s, state", "Frame index, simulation time, track state"],
        ["x_px, y_px, snr", "Measured centroid in the camera image (or prediction while coasting), SNR"],
        ["gt_x/y_px, error_*", "True beacon position in the camera image and the centroiding error"],
        ["boresight_dx/dy_px, az/el_error_deg", "Measured offset from the image centre, in px and deg"],
        ["true_screen_x/y_px", "True beacon position on the screen"],
        ["camera_center_x/y_px, pan/tilt_deg", "Camera boresight on the screen, and pan / tilt from centre"],
        ["beacon_in_fov, tracking_error_px", "Beacon inside the camera image; true distance from boresight"],
        ["processing_ms", "Tracker time for the frame"],
    ])

    # observations -----------------------------------------------------------
    report.heading(next_section + 3, "Observations")
    if frames:
        report.bullet(f"{s['measured_frames']} of {frames} frames produced a centroid measurement.")
        if s.get("error_samples"):
            report.bullet(f"Centroiding error reached at most {s['max_error_px']:.2f} px "
                          f"({s['within_10px_pct']:.1f} % of measured frames within 10 px).")
        if s.get("tracking_error_samples"):
            report.bullet(f"The beacon was within 10 px of boresight in {s['tracking_within_10px_pct']:.1f} % "
                          f"of frames after the first lock.")
        if cfg.jitter_px:
            report.bullet(f"Camera jitter of +/- {cfg.jitter_px} px per frame displaces the beacon in the image "
                          "each frame and adds directly to the tracking error.")
        if events_list:
            report.bullet(f"{len(events_list)} re-acquisition(s); the longest took "
                          f"{s['reacquisition_max_s']:.3f} s after the beacon was visible again.")
        report.bullet(f"The loop averaged {_fmt(s.get('frame_ms_mean'), 1, ' ms')} per frame end to end.")
    else:
        report.bullet("No frames have been simulated yet.")
    report.finish()
    return s


def export_scenario_outputs(runner, base, events=None, complete=True, write_pdf=True):
    """Write <base>_frames.csv, _summary.csv, _scenario.json (with the seed) and _report.pdf."""
    from dataclasses import fields

    from sim.scenario import ScenarioConfig

    cfg = runner.cfg
    runner.metrics.write_csv(base + "_frames.csv")
    summary = runner.metrics.write_summary(base + "_summary.csv", {
        "scenario": cfg.name, "seed": runner.seed, "simulated_s": runner.time_s,
        "screen": f"{cfg.screen_width}x{cfg.screen_height}",
        "camera": f"{cfg.camera_width}x{cfg.camera_height}",
    })
    saved = ScenarioConfig(**{f.name: getattr(cfg, f.name) for f in fields(ScenarioConfig)})
    saved.seed = runner.seed
    saved.save(base + "_scenario.json")
    if write_pdf:
        write_scenario_report(base + "_report.pdf", runner, events=events, complete=complete)
    return summary
