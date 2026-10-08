"""Create a PDF report with tables and plots from actual recorded telemetry."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


class ReportGenerator:
    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = reports_dir

    @staticmethod
    def _chart(rows: list[dict[str, Any]], series: list[tuple[str, str]], title: str, ylabel: str) -> Drawing:
        """Compact vector line plot - data is the recorded session telemetry."""
        drawing = Drawing(17.2 * cm, 6.15 * cm)
        width, height, margin_left, margin_bottom = 17.2 * cm, 6.15 * cm, 1.25 * cm, .85 * cm
        plot_width, plot_height = width - margin_left - .3 * cm, height - margin_bottom - .65 * cm
        drawing.add(Rect(0, 0, width, height, fillColor=colors.white, strokeColor=colors.HexColor("#C9D8E0")))
        drawing.add(String(.28 * cm, height - .38 * cm, title, fontName="Helvetica-Bold", fontSize=8.6, fillColor=colors.HexColor("#173A52")))
        drawing.add(String(.28 * cm, height / 2, ylabel, fontName="Helvetica", fontSize=6.5, fillColor=colors.HexColor("#597282"), angle=90))
        drawing.add(String(width - 3.1 * cm, .25 * cm, "Simulation time (s)", fontName="Helvetica", fontSize=6.5, fillColor=colors.HexColor("#597282")))
        for index in range(5):
            y = margin_bottom + plot_height * index / 4
            drawing.add(Line(margin_left, y, margin_left + plot_width, y, strokeColor=colors.HexColor("#E4EDF1"), strokeWidth=.35))
        drawing.add(Line(margin_left, margin_bottom, margin_left, margin_bottom + plot_height, strokeColor=colors.HexColor("#567384"), strokeWidth=.55))
        drawing.add(Line(margin_left, margin_bottom, margin_left + plot_width, margin_bottom, strokeColor=colors.HexColor("#567384"), strokeWidth=.55))
        all_values = [float(row.get(field, 0) or 0) for row in rows for field, _ in series]
        low, high = (min(all_values), max(all_values)) if all_values else (0.0, 1.0)
        if abs(high - low) < 1e-9:
            high += 1.0
        palette = [colors.HexColor("#1D8FB3"), colors.HexColor("#E28447"), colors.HexColor("#6E73B8"), colors.HexColor("#36966E")]
        for line_index, (field, label) in enumerate(series):
            values = [float(row.get(field, 0) or 0) for row in rows]
            points: list[float] = []
            for index, value in enumerate(values):
                x = margin_left + plot_width * index / max(1, len(values) - 1)
                y = margin_bottom + plot_height * (value - low) / (high - low)
                points.extend((x, y))
            color = palette[line_index % len(palette)]
            if len(points) >= 4:
                drawing.add(PolyLine(points, strokeColor=color, strokeWidth=1.15))
            legend_x = margin_left + line_index * 3.5 * cm
            drawing.add(Line(legend_x, height - .73 * cm, legend_x + .25 * cm, height - .73 * cm, strokeColor=color, strokeWidth=1.5))
            drawing.add(String(legend_x + .32 * cm, height - .81 * cm, label, fontName="Helvetica", fontSize=6.1, fillColor=colors.HexColor("#4B6473")))
        drawing.add(String(.98 * cm, margin_bottom - .1 * cm, f"{low:.2f}", fontName="Helvetica", fontSize=5.7, fillColor=colors.HexColor("#597282")))
        drawing.add(String(.98 * cm, margin_bottom + plot_height - .1 * cm, f"{high:.2f}", fontName="Helvetica", fontSize=5.7, fillColor=colors.HexColor("#597282")))
        return drawing

    # ------------------------------------------------------------------
    @staticmethod
    def _fmt(value: Any, unit: str = "", digits: int = 3) -> str:
        if value is None:
            return "not measured"
        if isinstance(value, float):
            return f"{value:.{digits}f}{(' ' + unit) if unit else ''}"
        return f"{value}{(' ' + unit) if unit else ''}"

    def _sih_table(self, sih: list[dict[str, Any]]) -> Table:
        rows = [["SIH requirement", "Limit", "Measured", "Result"]]
        for item in sih:
            result = "NOT MEASURED" if item["pass"] is None else ("PASS" if item["pass"] else "FAIL")
            rows.append([item["label"], f"{item['op']} {item['limit']} {item['unit']}", self._fmt(item["value"], item["unit"]), result])
        table = Table(rows, colWidths=[5.6 * cm, 3.4 * cm, 4.6 * cm, 3.7 * cm])
        commands = [("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#CBD9E2")), ("FONT", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12314A")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
        for index, item in enumerate(sih, 1):
            color = "#9AA7B0" if item["pass"] is None else ("#1E8E5A" if item["pass"] else "#C0392B")
            commands += [("TEXTCOLOR", (3, index), (3, index), colors.HexColor(color)), ("FONTNAME", (3, index), (3, index), "Helvetica-Bold")]
        table.setStyle(TableStyle(commands))
        return table

    def _document(self, path: Path, heading: str, intro: str) -> tuple[SimpleDocTemplate, list[Any], Any, Any]:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        document = SimpleDocTemplate(str(path), pagesize=A4, title="FSOC Coarse Alignment Performance Report", rightMargin=1.4 * cm, leftMargin=1.4 * cm, topMargin=1.35 * cm, bottomMargin=1.3 * cm)
        styles = getSampleStyleSheet()
        title, h2, body = styles["Title"], styles["Heading2"], styles["BodyText"]
        title.textColor = colors.HexColor("#12314A")
        h2.textColor = colors.HexColor("#146C8B")
        body.fontSize, body.leading = 8.5, 11
        return document, [Paragraph(heading, title), Paragraph(intro, body), Spacer(1, .3 * cm)], h2, body

    def _metrics_rows(self, s: dict[str, Any]) -> list[list[str]]:
        return [
            ["Duration", self._fmt(s.get("duration_s"), "s", 2)],
            ["Frames processed", str(s.get("frames", 0))],
            ["Update / loop rate", self._fmt(s.get("loop_fps"), "FPS", 1)],
            ["Processing time (mean / max)", f"{self._fmt(s.get('processing_ms_mean'), 'ms')} / {self._fmt(s.get('processing_ms_max'), 'ms')}"],
            ["Processing rate", self._fmt(s.get("processing_fps"), "FPS", 1)],
            ["Acquisition time (first)", self._fmt(s.get("acquisition_time_s"), "s")],
            ["All acquisition attempts", ", ".join(f"{v:.2f}s" for v in s.get("acquisition_times_s", [])) or "-"],
            ["Re-acquisition time (mean / max, count)", f"{self._fmt(s.get('reacquisition_time_mean_s'), 's')} / {self._fmt(s.get('reacquisition_time_max_s'), 's')} ({s.get('reacquisition_count', 0)})"],
            ["Tracking error mean / max / RMSE", f"{self._fmt(s.get('error_mean_px'), 'px')} / {self._fmt(s.get('error_max_px'), 'px')} / {self._fmt(s.get('error_rmse_px'), 'px')}"],
            ["Target loss", self._fmt(s.get("target_loss_pct"), "%", 2)],
            ["Lock retention", self._fmt(s.get("lock_retention_pct"), "%", 2)],
        ]

    @staticmethod
    def _dict_rows(prefix: str, value: Any) -> list[list[str]]:
        if isinstance(value, dict):
            rows: list[list[str]] = []
            for key, item in value.items():
                rows += ReportGenerator._dict_rows(f"{prefix}.{key}" if prefix else key, item)
            return rows
        return [[prefix, str(value)]]

    DEFINITIONS = ("Definitions: acquisition = communication start/beacon switch to the first frame that is locked and centred "
                   "within the link tolerance; re-acquisition = lock lost to lock regained, counted from when the target became "
                   "observable again; tracking error = pointing error in camera pixels on post-acquisition locked frames; target loss = "
                   "post-acquisition observable frames without an optical detection; lock retention = post-acquisition observable frames "
                   "with a held lock (detected or Kalman-coasting); processing rate = 1000 / mean per-frame processing time.")

    def generate(self, session_id: str, summary: dict[str, Any], config: dict[str, Any], rows: list[dict[str, Any]], events: list[dict[str, Any]], scenario: dict[str, Any] | None = None, disturbance_labels: list[str] | None = None) -> Path:
        path = self.reports_dir / f"{session_id}.pdf"
        document, story, h2, body = self._document(path, "FSOC COARSE ALIGNMENT PERFORMANCE REPORT", "3D simulation session. Every value below is computed from recorded per-frame telemetry of this session; nothing is hard-coded.")
        scenario = scenario or {}
        comm = config.get("comm", {})
        beacons = config.get("targets", {}).get("beacons", [])
        selected = next((b for b in beacons if b.get("id") == comm.get("target_id")), {})
        session = [["Session ID", session_id], ["Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")], ["Input source", "3D simulation (rendered virtual camera)"],
                   ["Scenario", f"{scenario.get('name', '-')}{' (modified after load)' if scenario.get('modified') else ''}"], ["Selected beacon", f"{comm.get('target_id', '-')} - path {selected.get('pattern', '-')}, speed x{selected.get('speed', '-')}"],
                   ["Active disturbances", ", ".join(disturbance_labels or []) or "none"]]
        story += [Paragraph("Session", h2), self._table(session), Spacer(1, .2 * cm)]
        story += [Paragraph("SIH reference compliance", h2), self._sih_table(summary.get("sih", [])), Spacer(1, .2 * cm)]
        story += [Paragraph("Measured performance", h2), self._table(self._metrics_rows(summary)), Paragraph(self.DEFINITIONS, body), Spacer(1, .2 * cm)]
        extra = [["FOV compliance / LOS availability", f"{self._fmt(summary.get('fov_compliance'), '%', 2)} / {self._fmt(summary.get('los_availability'), '%', 2)}"],
                 ["Communication CONNECTED time share", self._fmt(summary.get("fsoc_link_retention_rate"), "%", 2)], ["Beacon deliberately hidden", self._fmt(summary.get("hidden_time_s"), "s", 2)]]
        story += [self._table(extra), PageBreak()]
        story += [Paragraph("Active camera parameters", h2), self._table(self._dict_rows("", config.get("camera", {}))), Spacer(1, .2 * cm)]
        target_cfg = {k: v for k, v in config.get("targets", {}).items() if k != "beacons"}
        target_rows = self._dict_rows("", target_cfg) + [[b["id"], f"{b['pattern']}, speed x{b['speed']}, centre {b['center_pct']}%, size {b['size']} px"] for b in beacons[: int(target_cfg.get("count", len(beacons)))]]
        story += [Paragraph("Active target parameters", h2), self._table(target_rows), Spacer(1, .2 * cm)]
        story += [Paragraph("Active disturbance parameters", h2), self._table(self._dict_rows("", config.get("disturbances", {}))), Spacer(1, .2 * cm)]
        story += [Paragraph("Tracking parameters", h2), self._table(self._dict_rows("", config.get("tracking", {}))), Spacer(1, .2 * cm),
                  Paragraph("Atmospheric and noise effects are image-domain models (contrast, veil, blur, attenuation, sensor noise), not a full optical-propagation or link-budget model.", body), PageBreak()]
        charts = [("Pointing error vs time (camera px, ground truth)", [("pixel_error", "Pointing error")], "px"),
                  ("Camera angle vs target angle", [("target_azimuth", "Target azimuth"), ("camera_pan", "Camera pan"), ("target_elevation", "Target elevation"), ("camera_tilt", "Camera tilt")], "Degrees"),
                  ("Pan / tilt rate (limited)", [("pan_rate", "Pan rate"), ("tilt_rate", "Tilt rate")], "deg/s"),
                  ("Processing time vs time", [("processing_time_ms", "Processing time")], "ms"),
                  ("Detection confidence vs time", [("confidence", "Confidence")], "0-1")]
        story += [Paragraph("Performance graphs", h2)]
        for title_text, series, ylabel in charts:
            story += [self._chart(rows, series, title_text, ylabel), Spacer(1, .12 * cm)]
        story += [PageBreak(), Paragraph("Session events", h2)]
        event_data = [["Time", "Category", "Event"]] + [[f"{event.get('timestamp', 0):.2f}s", event.get("category", "system"), event.get("message", "")] for event in events[-70:]]
        story.append(self._table(event_data, header=True))
        document.build(story, onFirstPage=self._page, onLaterPages=self._page)
        return path

    def generate_video(self, run_id: str, summary: dict[str, Any], settings: dict[str, Any], rows: list[dict[str, Any]], events: list[dict[str, Any]], scenario: dict[str, Any] | None = None) -> Path:
        path = self.reports_dir / f"{run_id}.pdf"
        document, story, h2, body = self._document(path, "FSOC VIDEO BENCHMARK REPORT", "Uploaded video processed frame-by-frame by the same tracking core as the 3D simulation. The physical PTZ is bypassed; a virtual PTZ boresight starting at the screen centre follows the beacon under the configured pan/tilt speed limits.")
        video = summary.get("video", {})
        info = [["Run ID", run_id], ["Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")], ["Video file", str(video.get("filename", "-"))],
                ["Resolution / frame rate", f"{video.get('width')}x{video.get('height')} @ {video.get('fps')} FPS" + (" (30 FPS evaluator format)" if video.get("is_30fps") else "")],
                ["Frames processed / video duration", f"{summary.get('frames', 0)} / {self._fmt(summary.get('duration_s'), 's', 2)}"], ["Detector", str(video.get("detector_used", "-"))],
                ["Scenario disturbances applied", "yes - " + str((scenario or {}).get("name", "-")) if settings.get("apply_disturbances") else "no (raw video)"],
                ["Pacing", str(settings.get("pacing", "realtime"))]]
        story += [Paragraph("Run", h2), self._table(info), Spacer(1, .2 * cm)]
        story += [Paragraph("SIH reference compliance", h2), self._sih_table(summary.get("sih", [])), Spacer(1, .2 * cm)]
        story += [Paragraph("Measured performance", h2), self._table(self._metrics_rows(summary)), Paragraph(self.DEFINITIONS.replace("pointing error in camera pixels", "distance between the detected beacon centroid and the virtual camera centre"), body), Spacer(1, .2 * cm)]
        story += [Paragraph("Benchmark parameters", h2), self._table(self._dict_rows("", {k: v for k, v in settings.items() if k != "disturbances"})), PageBreak()]
        story += [Paragraph("Performance graphs", h2)]
        for title_text, series, ylabel in [("Tracking error vs time (centroid to virtual camera centre)", [("tracking_error_px", "Tracking error")], "px"),
                                           ("Beacon centroid vs virtual camera centre (x)", [("centroid_x", "Centroid x"), ("camera_centre_x", "Camera centre x")], "px"),
                                           ("Beacon centroid vs virtual camera centre (y)", [("centroid_y", "Centroid y"), ("camera_centre_y", "Camera centre y")], "px"),
                                           ("Processing time per frame", [("processing_ms", "Processing time")], "ms")]:
            story += [self._chart(rows, series, title_text, ylabel), Spacer(1, .12 * cm)]
        story += [PageBreak(), Paragraph("Events", h2)]
        story.append(self._table([["Time", "Category", "Event"]] + [[f"{e.get('timestamp', 0):.2f}s", e.get("category", "video"), e.get("message", "")] for e in events[-70:]], header=True))
        document.build(story, onFirstPage=self._page, onLaterPages=self._page)
        return path

    @staticmethod
    def _table(rows: list[list[Any]], header: bool = False) -> Table:
        columns = max((len(row) for row in rows), default=2)
        widths = [6.3 * cm, 11.0 * cm] if columns == 2 else [2.1 * cm, 3.0 * cm, 12.2 * cm]
        style = getSampleStyleSheet()["BodyText"]
        style.fontSize, style.leading = 7.6, 9.5
        rows = [[Paragraph(str(cell), style) if isinstance(cell, str) and len(cell) > 60 else cell for cell in row] for row in rows]
        table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        commands = [("GRID", (0, 0), (-1, -1), .25, colors.HexColor("#CBD9E2")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("FONT", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 7.6), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EAF3F7")), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        if header:
            commands += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#12314A")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
        table.setStyle(TableStyle(commands))
        return table

    @staticmethod
    def _page(canvas, document) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#B8D6E2"))
        canvas.line(document.leftMargin, A4[1] - .8 * cm, A4[0] - document.rightMargin, A4[1] - .8 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#5B7482"))
        canvas.drawString(document.leftMargin, .65 * cm, "FSOC Coarse Alignment - performance report")
        canvas.drawRightString(A4[0] - document.rightMargin, .65 * cm, f"Page {document.page}")
        canvas.restoreState()

    def load_csv(self, session_id: str) -> list[dict[str, Any]]:
        path = self.reports_dir / f"{session_id}.csv"
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))
