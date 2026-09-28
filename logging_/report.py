"""Performance report generation.

``write_report(metrics, config, out_root)`` creates a timestamped folder
under ``out_root`` containing:

- ``report.html``  - human-readable report with pass/fail against the
                      problem-statement specs and charts (open in any
                      browser; print to PDF for submission)
- ``frames.csv``   - per-frame log (timing, state, source, tracking and
                      centroiding error) for benchmark analysis
- ``summary.json`` - every summary figure, machine-readable
"""

import base64
import csv
import html
import io
import json
import os
from datetime import datetime

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from logging_.metrics import (
    SPEC_MIN_FPS,
    SPEC_TRACKING_ERROR_PX,
    STATE_LOCKED,
    STATE_PREDICTING,
    STATE_SEEKING,
)


def write_report(metrics, config, out_root="reports"):
    summary = metrics.summary()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(out_root, f"report_{stamp}")
    suffix = 1
    while os.path.exists(out_dir):
        suffix += 1
        out_dir = os.path.join(out_root, f"report_{stamp}_{suffix}")
    os.makedirs(out_dir)

    _write_csv(metrics.frames, os.path.join(out_dir, "frames.csv"))
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"config": config, "summary": summary}, f, indent=2)

    report_path = os.path.join(out_dir, "report.html")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(_render_html(summary, config, metrics.frames))
    return os.path.abspath(report_path)


# ----------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------

CSV_FIELDS = [
    "frame", "t_s", "frame_interval_ms", "processing_ms", "state", "source",
    "tracking_error_px", "centroid_error_px", "confidence", "target_in_fov",
    "beacon_hidden", "pattern", "disturbances",
]


def _write_csv(frames, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for fr in frames:
            row = []
            for key in CSV_FIELDS:
                value = fr[key]
                if key == "disturbances":
                    value = " ".join(f"{k}={v}" for k, v in value.items())
                elif isinstance(value, float):
                    value = "" if not np.isfinite(value) else f"{value:.3f}"
                elif value is None:
                    value = ""
                row.append(value)
            writer.writerow(row)


# ----------------------------------------------------------------------
# Charts
# ----------------------------------------------------------------------

INK = "#1b2733"
MUTED = "#6b7a88"
GRID = "#e3e8ee"
SERIES = "#1f6fb2"
SERIES_2 = "#d9822b"
SPEC = "#c0392b"
LOSS_BAND = "#f4c7c3"
OCCLUSION_BAND = "#dde3ea"


def _figure(height=2.6):
    fig = Figure(figsize=(9, height), dpi=110)
    FigureCanvasAgg(fig)
    return fig


def _style(ax):
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.xaxis.label.set_color(INK)
    ax.yaxis.label.set_color(INK)
    ax.xaxis.label.set_size(9)
    ax.yaxis.label.set_size(9)


def _png(fig):
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _state_bands(ax, frames):
    """Shade loss (SEEKING after first lock) and occlusion windows."""
    spans = []
    start, current = None, None
    held = False
    for f in frames:
        state = f["state"]
        if state == STATE_LOCKED:
            held = True
        kind = None
        if state == STATE_PREDICTING:
            kind = "occlusion"
        elif state == STATE_SEEKING and held:
            kind = "loss"
        if kind != current:
            if current is not None:
                spans.append((current, start, f["t_s"]))
            start, current = f["t_s"], kind
    if current is not None:
        spans.append((current, start, frames[-1]["t_s"]))
    for kind, t0, t1 in spans:
        ax.axvspan(
            t0, max(t1, t0 + 0.01),
            color=LOSS_BAND if kind == "loss" else OCCLUSION_BAND,
            linewidth=0, zorder=0,
        )
    return {kind for kind, _, _ in spans}


def _error_chart(frames):
    fig = _figure()
    ax = fig.add_subplot(111)
    _style(ax)
    kinds = _state_bands(ax, frames)
    # NaN outside locked frames breaks the line instead of bridging gaps.
    t = [f["t_s"] for f in frames]
    e = [
        f["tracking_error_px"] if f["state"] == STATE_LOCKED else np.nan
        for f in frames
    ]
    ax.plot(t, e, color=SERIES, linewidth=1.2, label="Tracking error (locked)")
    ax.axhline(SPEC_TRACKING_ERROR_PX, color=SPEC, linestyle="--", linewidth=1,
               label=f"Spec {SPEC_TRACKING_ERROR_PX:g} px")
    ax.set_xlabel("Run time (s)")
    ax.set_ylabel("Error (px)")
    ax.set_ylim(bottom=0)
    handles, labels = ax.get_legend_handles_labels()
    if "loss" in kinds:
        handles.append(_patch(LOSS_BAND))
        labels.append("Target lost")
    if "occlusion" in kinds:
        handles.append(_patch(OCCLUSION_BAND))
        labels.append("Beacon hidden")
    ax.legend(handles, labels, fontsize=8, frameon=False, loc="upper right",
              ncol=len(labels))
    return _png(fig)


def _patch(color):
    from matplotlib.patches import Patch
    return Patch(color=color, linewidth=0)


def _timing_chart(frames):
    fig = _figure(height=3.2)
    ax1 = fig.add_subplot(211)
    ax2 = fig.add_subplot(212, sharex=ax1)
    for ax in (ax1, ax2):
        _style(ax)

    timed = [f for f in frames if f["frame_interval_ms"]]
    t = np.array([f["t_s"] for f in timed])
    fps = np.array([1000.0 / f["frame_interval_ms"] for f in timed])
    if fps.size >= 15:
        # 15-frame moving average: the raw per-frame rate is very spiky.
        # "valid" avoids the fake drop "same" produces at both ends.
        kernel = np.ones(15) / 15
        fps = np.convolve(fps, kernel, mode="valid")
        t = t[7:7 + fps.size]
    ax1.plot(t, fps, color=SERIES, linewidth=1.2, label="Frame rate (15-frame avg)")
    ax1.axhline(SPEC_MIN_FPS, color=SPEC, linestyle="--", linewidth=1,
                label=f"Spec {SPEC_MIN_FPS:g} FPS")
    ax1.set_ylabel("FPS")
    ax1.set_ylim(bottom=0)
    ax1.legend(fontsize=8, frameon=False, loc="lower right", ncol=2)
    ax1.tick_params(labelbottom=False)

    ax2.plot([f["t_s"] for f in frames], [f["processing_ms"] for f in frames],
             color=SERIES_2, linewidth=1.0)
    ax2.set_ylabel("Processing (ms)")
    ax2.set_xlabel("Run time (s)")
    ax2.set_ylim(bottom=0)
    return _png(fig)


def _histogram_chart(frames):
    errors = np.array([
        f["tracking_error_px"] for f in frames
        if f["state"] == STATE_LOCKED and f["tracking_error_px"] is not None
        and np.isfinite(f["tracking_error_px"])
    ])
    if errors.size == 0:
        return None
    fig = _figure(height=2.4)
    ax = fig.add_subplot(111)
    _style(ax)
    upper = max(SPEC_TRACKING_ERROR_PX * 2, float(np.percentile(errors, 99)))
    bins = np.linspace(0, upper, 40)
    ax.hist(np.clip(errors, 0, upper), bins=bins, color=SERIES, edgecolor="white",
            linewidth=0.5)
    ax.axvline(SPEC_TRACKING_ERROR_PX, color=SPEC, linestyle="--", linewidth=1)
    ax.set_xlabel("Tracking error (px)")
    ax.set_ylabel("Frames")
    return _png(fig)


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------

def _fmt(value, digits=2, unit=""):
    if value is None:
        return "&ndash;"
    if isinstance(value, float):
        if not np.isfinite(value):
            return "&ndash;"
        text = f"{value:.{digits}f}"
    else:
        text = str(value)
    return f"{text}&nbsp;{unit}" if unit else text


def _verdict(passed):
    if passed is None:
        return '<span class="badge na">NO DATA</span>'
    if passed:
        return '<span class="badge pass">PASS</span>'
    return '<span class="badge fail">FAIL</span>'


def _rows(pairs):
    return "\n".join(
        f"<tr><th>{html.escape(k)}</th><td>{v}</td></tr>" for k, v in pairs
    )


def _render_html(s, config, frames):
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    esc = html.escape

    if not frames:
        body = '<p class="empty">No frames were recorded. Press START in the ' \
               '2D BORE-SIGHT tab, let the simulation run, then generate the ' \
               'report again.</p>'
        return _page(generated, body)

    criteria = s["criteria"]
    passed = [c["passed"] for c in criteria if c["passed"] is not None]
    overall = (
        "NO DATA" if not passed else
        f"{sum(passed)} / {len(passed)} specifications met"
    )

    criteria_rows = "\n".join(
        f"<tr><th>{esc(c['name'])}</th><td>{esc(c['spec'])}</td>"
        f"<td class='num'>{_fmt(c['value'], 2, c['unit'])}</td>"
        f"<td>{_verdict(c['passed'])}</td></tr>"
        for c in criteria
    )

    run_rows = _rows([
        ("Simulation duration", _fmt(s["duration_s"], 1, "s")),
        ("Frames processed", _fmt(s["frames"])),
        ("Average frame rate", _fmt(s.get("fps_mean"), 1, "FPS")),
        ("Minimum frame rate (1st percentile)", _fmt(s.get("fps_min"), 1, "FPS")),
        ("Mean frame interval", _fmt(s.get("frame_interval_ms_mean"), 1, "ms")),
        ("Processing time per frame (mean)", _fmt(s["processing_ms_mean"], 1, "ms")),
        ("Processing time per frame (95th pct)", _fmt(s["processing_ms_p95"], 1, "ms")),
        ("Processing time per frame (max)", _fmt(s["processing_ms_max"], 1, "ms")),
        ("Processing capacity (1 / mean time)",
         _fmt(s.get("processing_fps_capacity"), 1, "FPS")),
    ])

    reacq_list = ", ".join(f"{v:.2f}" for v in s["reacquisition_times_s"]) or "&ndash;"
    track_rows = _rows([
        ("Acquisition time", _fmt(s.get("acquisition_time_s"), 2, "s")),
        ("Lock retention rate", _fmt(s.get("lock_retention_pct"), 1, "%")),
        ("Target loss", _fmt(s.get("target_loss_pct"), 1, "%")),
        ("Frames locked / hidden / seeking",
         f"{s['frames_locked']} / {s['frames_predicting']} / {s['frames_seeking']}"),
        ("Loss events", _fmt(s["loss_events"])),
        ("Unrecovered at end of run",
         "no" if not s["unrecovered_losses"]
         else f"yes ({_fmt(s['unrecovered_loss_s'], 2, 's')} lost)"),
        ("Re-acquisition time (mean)", _fmt(s.get("reacquisition_s_mean"), 2, "s")),
        ("Re-acquisition time (max)", _fmt(s.get("reacquisition_s_max"), 2, "s")),
        ("Re-acquisition times", f"{reacq_list} s" if s["reacquisition_times_s"]
         else reacq_list),
        ("Beacon-hidden (occlusion) tests",
         f"{s['occlusion_events']} ({_fmt(s['occlusion_s_total'], 1, 's')} total)"),
    ])

    def error_block(prefix):
        return [
            ("Mean", _fmt(s[f"{prefix}_mean_px"], 2, "px")),
            ("RMS (RMSE)", _fmt(s[f"{prefix}_rmse_px"], 2, "px")),
            ("95th percentile", _fmt(s[f"{prefix}_p95_px"], 2, "px")),
            ("Maximum", _fmt(s[f"{prefix}_max_px"], 2, "px")),
            (f"Frames within {SPEC_TRACKING_ERROR_PX:g} px",
             _fmt(s[f"{prefix}_within_spec_pct"], 1, "%")),
            ("Samples", _fmt(s[f"{prefix}_samples"])),
        ]

    error_rows = "\n".join(
        f"<tr><th>{esc(label)}</th><td class='num'>{a}</td><td class='num'>{b}</td></tr>"
        for (label, a), (_, b) in zip(
            error_block("tracking_error"), error_block("centroid_error")
        )
    )

    source_rows = _rows([
        (src.upper(), _fmt(pct, 1, "%")) for src, pct in s["source_pct"].items()
    ])

    dist = s["disturbance_max_levels"]
    level_names = {0: "off", 1: "low", 2: "medium", 3: "high"}
    scenario_rows = _rows(
        [(k, esc(str(v))) for k, v in config.items()]
        + [("Motion patterns used", esc(", ".join(s["patterns"])))]
        + [(f"Max {k} disturbance", level_names.get(v, str(v)))
           for k, v in dist.items()]
    )

    hist = _histogram_chart(frames)
    hist_html = (
        f'<img alt="Histogram of tracking error" src="data:image/png;base64,{hist}">'
        if hist else '<p class="empty">No locked frames.</p>'
    )

    body = f"""
<section>
  <h2>Result against specifications</h2>
  <p class="overall">{esc(overall)}</p>
  <table class="criteria">
    <thead><tr><th>Metric</th><th>Specification</th><th>Measured</th><th>Result</th></tr></thead>
    <tbody>{criteria_rows}</tbody>
  </table>
</section>

<section class="grid2">
  <div>
    <h2>Run and processing</h2>
    <table>{run_rows}</table>
  </div>
  <div>
    <h2>Acquisition and lock</h2>
    <table>{track_rows}</table>
  </div>
</section>

<section>
  <h2>Pointing accuracy</h2>
  <table class="errors">
    <thead><tr><th></th><th>Tracking error</th><th>Centroiding error</th></tr></thead>
    <tbody>{error_rows}</tbody>
  </table>
  <p class="note"><b>Tracking error</b>: distance from the true beacon centroid to
  the camera boresight (image centre) in each locked frame.
  <b>Centroiding error</b>: distance from the tracker's estimated beacon position to
  the true centroid.</p>
  <figure><img alt="Tracking error over time"
    src="data:image/png;base64,{_error_chart(frames)}">
    <figcaption>Tracking error over time. Shaded areas are periods where the
    target was lost or deliberately hidden.</figcaption></figure>
  <figure>{hist_html}
    <figcaption>Distribution of tracking error in locked frames.</figcaption></figure>
</section>

<section>
  <h2>Frame rate and processing time</h2>
  <figure><img alt="Frame rate and processing time over time"
    src="data:image/png;base64,{_timing_chart(frames)}"></figure>
</section>

<section class="grid2">
  <div>
    <h2>Detection source</h2>
    <table>{source_rows}</table>
    <p class="note">Share of frames by the source that produced the track.
    SIM-ASSIST and WORLD-GUIDE frames use simulator ground truth, not the
    optical detectors.</p>
  </div>
  <div>
    <h2>Scenario</h2>
    <table>{scenario_rows}</table>
  </div>
</section>

<section>
  <h2>Definitions</h2>
  <ul class="note">
    <li>Times are measured on the real clock while the simulation is running;
    pauses are excluded.</li>
    <li><b>Acquisition time</b>: from the first processed frame to the first
    locked frame.</li>
    <li><b>Lock retention rate</b>: locked frames as a share of frames since the
    first acquisition. Periods where the beacon was hidden on purpose
    (HIDE BEACON) are excluded. <b>Target loss</b> = 100% &minus; lock retention.</li>
    <li><b>Re-acquisition time</b>: from the first frame without a track to the
    next locked frame, for each loss.</li>
    <li><b>Processing time</b>: time spent inside one simulation tick
    (render, disturbances, detection, tracking, camera control, display).</li>
    <li>The per-frame data behind this report is in <code>frames.csv</code>; all
    summary values are in <code>summary.json</code>.</li>
  </ul>
</section>
"""
    return _page(generated, body, started=s["started_at"])


def _page(generated, body, started=None):
    started_line = f" &middot; run started {html.escape(started)}" if started else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FSOC Tracking Performance Report</title>
<style>
  :root {{
    --ink: #1b2733; --muted: #5d6b78; --line: #dfe5eb; --soft: #f5f7f9;
    --pass: #1e7a46; --pass-bg: #e3f4ea; --fail: #b42318; --fail-bg: #fdecea;
    --na: #5d6b78; --na-bg: #eef1f4; --accent: #1f6fb2;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #fff; color: var(--ink);
         font: 14px/1.5 "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
  main {{ max-width: 1000px; margin: 0 auto; padding: 32px 24px 48px; }}
  header {{ border-bottom: 2px solid var(--ink); padding-bottom: 12px; margin-bottom: 8px; }}
  h1 {{ margin: 0; font-size: 22px; letter-spacing: 0.2px; }}
  .meta {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  h2 {{ font-size: 15px; margin: 28px 0 10px; text-transform: uppercase;
        letter-spacing: 0.8px; color: var(--accent); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line);
            vertical-align: top; }}
  th {{ font-weight: 600; }}
  table:not(.criteria):not(.errors) th {{ font-weight: 500; color: var(--muted); width: 58%; }}
  thead th {{ background: var(--soft); color: var(--muted); font-weight: 600;
              font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
  td.num {{ font-variant-numeric: tabular-nums; }}
  .overall {{ font-size: 16px; font-weight: 600; margin: 0 0 10px; }}
  .badge {{ display: inline-block; padding: 1px 8px; border-radius: 10px;
            font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }}
  .badge.pass {{ color: var(--pass); background: var(--pass-bg); }}
  .badge.fail {{ color: var(--fail); background: var(--fail-bg); }}
  .badge.na {{ color: var(--na); background: var(--na-bg); }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 32px; }}
  figure {{ margin: 16px 0 0; }}
  figure img {{ width: 100%; height: auto; display: block; }}
  figcaption, .note {{ color: var(--muted); font-size: 12.5px; }}
  .note li {{ margin-bottom: 4px; }}
  .empty {{ color: var(--muted); padding: 24px 0; }}
  code {{ font-size: 12px; background: var(--soft); padding: 1px 4px; border-radius: 3px; }}
  @media (max-width: 760px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  @media print {{
    main {{ padding: 0; max-width: none; }}
    section {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>
<main>
<header>
  <h1>FSOC Coarse Alignment &mdash; Tracking Performance Report</h1>
  <div class="meta">Generated {html.escape(generated)}{started_line}</div>
</header>
{body}
</main>
</body>
</html>
"""
