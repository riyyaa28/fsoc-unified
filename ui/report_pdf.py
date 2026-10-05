"""A4 technical-report writer for the 2D / video tracking tab.

Mirrors the layout of the 3D tab's report (ui/web3d/app.js) so both reports
read as one family: cover band, key-figure strip, numbered sections, tables,
vector line charts and page furniture. Drawing is recorded per page and
replayed on finish() so every footer can show "Page i of N".
"""

import math

from PyQt5.QtCore import QMarginsF, QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter,
    QPainterPath, QPdfWriter, QPen,
)

PAGE_W, PAGE_H = 595.0, 842.0
MARGIN = 50.0
CONTENT_W = PAGE_W - MARGIN * 2
TOP = 72.0
BOTTOM = PAGE_H - 60.0

INK = "#1a2733"
MUTED = "#6b7c8a"
NOTE = "#4a5b68"
ACCENT = "#0b4f6c"
RULE = "#1b9fc4"
BORDER = "#c5d2dc"
ROW_ALT = "#f3f7fa"
REFERENCE = "#8fa3b3"


class TechnicalReport:
    def __init__(self, path, title, subtitle, report_id, generated, duration_text):
        self.path = path
        self.title = title
        self.subtitle = subtitle
        self.report_id = report_id
        self.generated = generated
        self.duration_text = duration_text
        self.pages = []
        self.ops = None
        self.y = TOP
        self._metrics = {}
        self._new_page()
        self._cover()

    # -- primitives (all coordinates in points, origin top-left) -----------

    @staticmethod
    def _font(size, bold=False, mono=False):
        font = QFont("Consolas" if mono else "Arial")
        font.setPointSizeF(size)
        font.setBold(bold)
        font.setHintingPreference(QFont.PreferNoHinting)
        return font

    def _fm(self, size, bold=False, mono=False):
        key = (size, bold, mono)
        if key not in self._metrics:
            self._metrics[key] = QFontMetricsF(self._font(size, bold, mono))
        return self._metrics[key]

    def text_width(self, value, size=10, bold=False, mono=False):
        # Screen metrics are in pixels at the screen DPI; measure at 10x size
        # to avoid pixel rounding, then convert to points.
        fm = self._fm(size * 10, bold, mono)
        return fm.horizontalAdvance(str(value)) * 7.2 / self._screen_dpi()

    @staticmethod
    def _screen_dpi():
        from PyQt5.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        return screen.logicalDotsPerInch() if screen is not None else 96.0

    def text(self, x, y, value, size=10, bold=False, mono=False, color=INK, align="left"):
        value = str(value)
        if align != "left":
            width = self.text_width(value, size, bold, mono)
            x -= width if align == "right" else width / 2
        self.ops.append(("text", x, y, value, size, bold, mono, color))

    def rect(self, x, y, w, h, fill=None, stroke=None):
        self.ops.append(("rect", x, y, w, h, fill, stroke))

    def line(self, x1, y1, x2, y2, color=BORDER, width=0.6, dashed=False):
        self.ops.append(("line", x1, y1, x2, y2, color, width, dashed))

    def polyline(self, points, color, width=1.0):
        self.ops.append(("poly", points, color, width))

    def wrap(self, value, size=10, bold=False, mono=False, max_width=CONTENT_W):
        words = str(value).split()
        lines, current = [], ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if current and self.text_width(candidate, size, bold, mono) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
            while self.text_width(current, size, bold, mono) > max_width and len(current) > 4:
                cut = len(current)
                while cut > 1 and self.text_width(current[:cut], size, bold, mono) > max_width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        if current:
            lines.append(current)
        return lines or [""]

    # -- layout ------------------------------------------------------------

    def _new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        self.y = TOP

    def ensure_space(self, height):
        if self.y + height > BOTTOM:
            self._new_page()

    def _cover(self):
        self.rect(0, 0, PAGE_W, 170, fill="#071a2c")
        self.rect(0, 170, PAGE_W, 4, fill=RULE)
        self.text(MARGIN, 62, "FSOC COARSE ALIGNMENT SYSTEM", 11, bold=True, color="#35d8ff")
        self.text(MARGIN, 94, self.title, 24, bold=True, color="#ffffff")
        self.text(MARGIN, 116, self.subtitle, 10, color="#a9c3d6")
        self.text(MARGIN, 148, f"Report ID: {self.report_id}", 8.5, mono=True, color="#a9c3d6")
        self.text(MARGIN + 190, 148, f"Generated: {self.generated}", 8.5, mono=True, color="#a9c3d6")
        self.text(PAGE_W - MARGIN, 148, f"Duration: {self.duration_text}", 8.5, mono=True,
                  color="#a9c3d6", align="right")
        self.y = 200

    def key_figures(self, figures):
        cell_w = CONTENT_W / len(figures)
        self.rect(MARGIN, self.y, CONTENT_W, 46, fill=ROW_ALT, stroke=BORDER)
        for index, (label, value) in enumerate(figures):
            cx = MARGIN + cell_w * index
            if index:
                self.line(cx, self.y + 6, cx, self.y + 40, "#dbe4ea")
            self.text(cx + 14, self.y + 16, label, 7.5, bold=True, color=MUTED)
            self.text(cx + 14, self.y + 35, value, 13, bold=True, color=ACCENT)
        self.y += 66

    def heading(self, number, title):
        self.ensure_space(110)
        self.y += 12
        self.text(MARGIN, self.y, f"{number}. {title.upper()}", 12, bold=True, color=ACCENT)
        self.y += 6
        self.line(MARGIN, self.y, MARGIN + CONTENT_W, self.y, RULE, 1.0)
        self.y += 18

    def paragraph(self, value, size=10, color=INK, gap=6):
        for line in self.wrap(value, size):
            self.ensure_space(size + 4)
            self.text(MARGIN, self.y, line, size, color=color)
            self.y += size + 4
        self.y += gap

    def note(self, value):
        self.paragraph(value, size=9, color=NOTE)

    def bullet(self, value):
        for index, line in enumerate(self.wrap(value, 10, max_width=CONTENT_W - 16)):
            self.ensure_space(14)
            if index == 0:
                self.text(MARGIN + 4, self.y, "-", 10, bold=True, color=RULE)
            self.text(MARGIN + 16, self.y, line, 10)
            self.y += 14
        self.y += 2

    def table(self, columns, rows, size=9):
        total = sum(weight for _, weight in columns)
        widths = [CONTENT_W * weight / total for _, weight in columns]
        line_h = size + 3

        def header():
            self.ensure_space(40)
            self.rect(MARGIN, self.y - 12, CONTENT_W, 18, fill=ACCENT)
            x = MARGIN
            for (label, _), width in zip(columns, widths):
                self.text(x + 5, self.y, label, 8.5, bold=True, color="#ffffff")
                x += width
            self.y += 18

        header()
        for row_index, row in enumerate(rows):
            cells = [self.wrap(cell, size, max_width=width - 10) for cell, width in zip(row, widths)]
            height = max(len(lines) for lines in cells) * line_h + 6
            if self.y + height - line_h > BOTTOM:
                self._new_page()
                header()
            top = self.y - line_h + 2
            self.rect(MARGIN, top, CONTENT_W, height, fill=ROW_ALT if row_index % 2 else "#ffffff")
            self.line(MARGIN, top + height, MARGIN + CONTENT_W, top + height, "#dbe4ea", 0.4)
            x = MARGIN
            for lines, width in zip(cells, widths):
                for i, line in enumerate(lines):
                    self.text(x + 5, self.y + i * line_h, line, size)
                x += width
            self.y += height
        self.y += 10

    def chart(self, x, top, w, h, title, unit, samples, color, reference=None, x_label="time"):
        """samples: list of (t, value). reference: dashed horizontal line."""
        samples = [(t, v) for t, v in samples if v is not None and math.isfinite(v)]
        self.rect(x, top, w, h, fill="#fbfdfe", stroke=BORDER)
        self.text(x + 8, top + 16, title, 9, bold=True, color=ACCENT)
        self.text(x + w - 8, top + 16, unit, 7, color=MUTED, align="right")
        left, right, ptop, pbottom = x + 36, x + w - 10, top + 26, top + h - 20
        if len(samples) < 2:
            self.text(x + w / 2, top + h / 2, "Not enough samples yet", 8, color=MUTED, align="center")
            return
        values = [v for _, v in samples] + ([reference] if reference is not None else [])
        lo, hi = min(values), max(values)
        if hi - lo < 1e-6:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.1
        lo, hi = max(0.0, lo - pad), hi + pad
        t0, t1 = samples[0][0], samples[-1][0]

        def sx(t):
            return left + (right - left) * (t - t0) / max(t1 - t0, 1e-6)

        def sy(v):
            return pbottom - (pbottom - ptop) * (v - lo) / (hi - lo)

        digits = 0 if hi >= 100 else 1 if hi - lo >= 2 else 2
        for i in range(5):
            v = lo + (hi - lo) * i / 4
            gy = sy(v)
            self.line(left, gy, right, gy, "#e3eaef", 0.4)
            self.text(left - 4, gy + 2.5, f"{v:.{digits}f}", 6.5, color=MUTED, align="right")
        self.line(left, pbottom, right, pbottom, REFERENCE)
        self.line(left, pbottom, left, ptop, REFERENCE)
        self.text(left, pbottom + 11, f"{t0:.0f} s", 6.5, color=MUTED)
        self.text(right, pbottom + 11, f"{t1:.0f} s", 6.5, color=MUTED, align="right")
        self.text((left + right) / 2, pbottom + 11, x_label, 6.5, color=MUTED, align="center")
        if reference is not None:
            self.line(left, sy(reference), right, sy(reference), REFERENCE, 0.7, dashed=True)
        step = max(1, math.ceil(len(samples) / 400))
        self.polyline([(sx(t), sy(v)) for t, v in samples[::step]], color, 1.0)

    def chart_grid(self, charts, x_label):
        """charts: up to four (title, unit, samples, color, reference) in a 2x2 grid."""
        chart_w, chart_h = (CONTENT_W - 14) / 2, 160
        for row_start in range(0, len(charts), 2):
            self.ensure_space(chart_h + 12)
            for col, spec in enumerate(charts[row_start:row_start + 2]):
                title, unit, samples, color, reference = spec
                self.chart(MARGIN + col * (chart_w + 14), self.y, chart_w, chart_h,
                           title, unit, samples, color, reference, x_label)
            self.y += chart_h + 12
        self.y += 8

    # -- output ------------------------------------------------------------

    def finish(self):
        count = len(self.pages)
        for index, ops in enumerate(self.pages):
            self.ops = ops
            if index:
                self.text(MARGIN, 38, f"FSOC Coarse Alignment - {self.title}", 8, color=MUTED)
                self.text(PAGE_W - MARGIN, 38, self.report_id, 8, mono=True, color=MUTED, align="right")
                self.line(MARGIN, 45, PAGE_W - MARGIN, 45, BORDER, 0.5)
            self.line(MARGIN, PAGE_H - 42, PAGE_W - MARGIN, PAGE_H - 42, BORDER, 0.5)
            self.text(MARGIN, PAGE_H - 30, "Generated by FSOC Optical Link Control Center", 7.5, color=MUTED)
            self.text(PAGE_W - MARGIN, PAGE_H - 30, f"Page {index + 1} of {count}", 7.5,
                      color=MUTED, align="right")

        writer = QPdfWriter(self.path)
        writer.setTitle(f"{self.title} {self.report_id}")
        writer.setCreator("FSOC Control Center")
        writer.setResolution(1200)
        writer.setPageLayout(QPageLayout(
            QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)
        ))
        painter = QPainter(writer)
        try:
            # Scale so one unit == one point; fonts are sized for the scaled space.
            scale = writer.resolution() / 72.0
            painter.scale(scale, scale)
            painter.setRenderHint(QPainter.Antialiasing)
            for index, ops in enumerate(self.pages):
                if index:
                    writer.newPage()
                for op in ops:
                    self._replay(painter, op, scale)
        finally:
            painter.end()

    @staticmethod
    def _replay(painter, op, scale):
        kind = op[0]
        if kind == "text":
            _, x, y, value, size, bold, mono, color = op
            font = TechnicalReport._font(size, bold, mono)
            # Point sizes are resolved at the writer's DPI and then scaled
            # again by the painter, so divide the scale back out.
            font.setPointSizeF(size / scale)
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(QPointF(x, y), value)
        elif kind == "rect":
            _, x, y, w, h, fill, stroke = op
            painter.setPen(QPen(QColor(stroke), 0.6) if stroke else Qt.NoPen)
            painter.setBrush(QColor(fill) if fill else Qt.NoBrush)
            painter.drawRect(QRectF(x, y, w, h))
            painter.setBrush(Qt.NoBrush)
        elif kind == "line":
            _, x1, y1, x2, y2, color, width, dashed = op
            pen = QPen(QColor(color), width)
            if dashed:
                pen.setDashPattern([4, 3])
            painter.setPen(pen)
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        elif kind == "poly":
            _, points, color, width = op
            path = QPainterPath(QPointF(*points[0]))
            for point in points[1:]:
                path.lineTo(QPointF(*point))
            pen = QPen(QColor(color), width)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)
