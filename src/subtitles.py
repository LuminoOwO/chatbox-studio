"""
Subtitle window: what you say (and its translation) as big text on a plain background, in
a window of its own - for OBS (Window Capture, or a chroma key on a green background),
a second screen, or anything else that shows a window.

Right-click the window for its settings. Double-click: full screen. The settings live in
settings.extra["subtitles"].
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSpinBox, QWidget,
)

from version import APP_NAME

# The window title OBS's Window Capture remembers - keep it the same.
TITLE = f"{APP_NAME} - Subtitles"

DEFAULTS = {
    "open": False,
    "show": "translation",       # translation | original | both
    "typed": True,               # typed messages too
    "font": "",                  # "" = the app's font
    "size": 44,
    "bold": True,
    "color": "#ffffff",
    "original_color": "#bfc6cf",
    "outline": 4,                # px, 0 = none
    "outline_color": "#000000",
    "background": "#000000",
    "align": "bottom",           # bottom | center | top
    "sentences": 2,              # how many recent sentences stay
    "clear_after": 10.0,         # s without new speech until it's empty; 0 = never
    "on_top": False,
    "geometry": None,            # [x, y, w, h]
}
BACKGROUNDS = [("Black", "#000000"), ("Green (chroma key)", "#00ff00"),
               ("Blue (chroma key)", "#0000ff"), ("Magenta (chroma key)", "#ff00ff")]
SHOW = [("Translation", "translation"), ("What you said", "original"),
        ("Both (what you said above)", "both")]
ALIGN = [("Bottom", "bottom"), ("Middle", "center"), ("Top", "top")]


def config(extra: dict) -> dict:
    cfg = extra.get("subtitles")
    if not isinstance(cfg, dict):
        cfg = {}
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    extra["subtitles"] = cfg
    return cfg


def wrap(text: str, fm: QFontMetricsF, width: float) -> list[str]:
    """Word wrap; words wider than the line (or text without spaces, e.g. Japanese) are
    broken between characters."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            cand = f"{line} {word}" if line else word
            if fm.horizontalAdvance(cand) <= width:
                line = cand
                continue
            if line:
                out.append(line)
                line = ""
            while fm.horizontalAdvance(word) > width and len(word) > 1:
                cut = len(word)
                while cut > 1 and fm.horizontalAdvance(word[:cut]) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    return [x for x in out if x.strip()]


class SubtitleWindow(QWidget):
    closed = Signal()
    changed = Signal()             # settings changed (save them)

    def __init__(self, cfg: dict):
        super().__init__(None, Qt.Window)
        self.cfg = cfg
        self.setWindowTitle(TITLE)
        self.setMinimumSize(320, 120)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.entries: deque = deque()      # (time, [(kind, text)])
        self._timer = QTimer(self, interval=500, timeout=self._expire)
        self._timer.start()
        g = cfg.get("geometry")
        if isinstance(g, list) and len(g) == 4:
            self.setGeometry(*[int(x) for x in g])
        else:
            self.resize(1280, 260)
        self.apply()

    # ---- content
    def add(self, original: str, translations: list[str]):
        """One sentence: what was said and its translations (empty when not translated)."""
        show = self.cfg["show"]
        parts = []
        if show in ("original", "both") and original.strip():
            parts.append(("orig" if show == "both" else "main", original.strip()))
        if show in ("translation", "both"):
            trs = [t.strip() for t in translations if t.strip()]
            if not trs and show == "translation":
                trs = [original.strip()] if original.strip() else []   # nothing to translate
            parts += [("main", t) for t in trs]
        if not parts:
            return
        self.entries.append((time.monotonic(), parts))
        while len(self.entries) > max(1, int(self.cfg["sentences"])):
            self.entries.popleft()
        self.update()

    def clear(self):
        self.entries.clear()
        self.update()

    def _expire(self):
        after = float(self.cfg.get("clear_after") or 0)
        if after > 0 and self.entries and time.monotonic() - self.entries[-1][0] > after:
            self.clear()

    def sample(self):
        self.add("This is how your subtitles look.",
                 ["And this is where the translation shows."])

    # ---- look
    def apply(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(self.cfg["on_top"]))
        self.update()

    def _font(self, scale: float = 1.0) -> QFont:
        f = QFont(self.cfg["font"]) if self.cfg["font"] else QFont(self.font())
        f.setPixelSize(max(8, int(int(self.cfg["size"]) * scale)))
        f.setBold(bool(self.cfg["bold"]))
        return f

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(self.cfg["background"]))
        margin = max(12.0, self.width() * 0.03)
        width = self.width() - 2 * margin
        outline = float(self.cfg["outline"])
        rows = []                                   # (font, color, line, metrics)
        heights = []
        for n, (_t, parts) in enumerate(self.entries):
            first = True
            for kind, text in parts:
                font = self._font(0.62 if kind == "orig" else 1.0)
                color = self.cfg["original_color"] if kind == "orig" else self.cfg["color"]
                fm = QFontMetricsF(font)
                for line in wrap(text, fm, width - 2 * outline):
                    rows.append((font, color, line, fm))
                    # a little room between sentences
                    gap = fm.height() * 0.35 if first and n > 0 else 0.0
                    heights.append(fm.height() * 1.08 + gap)
                    first = False
        if not rows:
            return
        avail = self.height() - 2 * margin
        while rows and sum(heights) > avail:        # too much: the oldest lines go first
            rows.pop(0)
            heights.pop(0)
        total = sum(heights)
        align = self.cfg["align"]
        y = (margin if align == "top" else self.height() - margin - total if align == "bottom"
             else (self.height() - total) / 2)
        pen = QPen(QColor(self.cfg["outline_color"]), outline * 2)
        pen.setJoinStyle(Qt.RoundJoin)
        for (font, color, line, fm), h in zip(rows, heights):
            x = (self.width() - fm.horizontalAdvance(line)) / 2
            path = QPainterPath()
            path.addText(QPointF(x, y + h - fm.height() * 1.08 + fm.ascent()), font, line)
            if outline > 0:
                p.strokePath(path, pen)
            p.fillPath(path, QColor(color))
            y += h

    # ---- window
    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Subtitle settings…", self.open_settings)
        m.addAction("Show a sample", self.sample)
        m.addAction("Clear", self.clear)
        m.addSeparator()
        top = m.addAction("Always on top")
        top.setCheckable(True)
        top.setChecked(bool(self.cfg["on_top"]))
        top.toggled.connect(lambda on: self._set("on_top", on))
        full = m.addAction("Full screen (double-click)")
        full.setCheckable(True)
        full.setChecked(self.isFullScreen())
        full.toggled.connect(lambda _on: self._toggle_full())
        m.addSeparator()
        m.addAction("Close", self.close)
        m.exec(e.globalPos())

    def mouseDoubleClickEvent(self, _e):
        self._toggle_full()

    def _toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _set(self, key, value):
        self.cfg[key] = value
        if key == "on_top":
            self.apply()
            self.show()
        self.update()
        self.changed.emit()

    def _remember_geometry(self):
        if not self.isFullScreen():
            g = self.geometry()
            self.cfg["geometry"] = [g.x(), g.y(), g.width(), g.height()]

    def moveEvent(self, e):
        self._remember_geometry()
        super().moveEvent(e)

    def resizeEvent(self, e):
        self._remember_geometry()
        super().resizeEvent(e)

    def closeEvent(self, e):
        self._remember_geometry()
        self.closed.emit()
        super().closeEvent(e)

    def open_settings(self):
        SubtitleSettings(self).exec()


class ColorButton(QPushButton):
    def __init__(self, color: str, on_pick):
        super().__init__()
        self.setFixedWidth(64)
        self.on_pick = on_pick
        self.set(color)
        self.clicked.connect(self._pick)

    def set(self, color: str):
        self.color = color
        self.setStyleSheet(f"background:{color}; border:1px solid #888; min-height:22px")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Pick a colour")
        if c.isValid():
            self.set(c.name())
            self.on_pick(c.name())


class SubtitleSettings(QDialog):
    """Changes show in the subtitle window right away."""

    def __init__(self, win: SubtitleWindow):
        super().__init__(win)
        self.win, cfg = win, win.cfg
        self.setWindowTitle("Subtitle settings")
        form = QFormLayout(self)

        def combo(items, key):
            box = QComboBox()
            for label, val in items:
                box.addItem(label, val)
            i = box.findData(cfg[key])
            box.setCurrentIndex(max(0, i))
            box.currentIndexChanged.connect(lambda _i: self._set(key, box.currentData()))
            return box

        form.addRow("Show", combo(SHOW, "show"))
        typed = QCheckBox("Also messages you type")
        typed.setChecked(bool(cfg["typed"]))
        typed.toggled.connect(lambda on: self._set("typed", on))
        form.addRow("", typed)

        font = QFontComboBox()
        if cfg["font"]:
            font.setCurrentFont(QFont(cfg["font"]))
        font.currentFontChanged.connect(lambda f: self._set("font", f.family()))
        form.addRow("Font", font)
        row = QHBoxLayout()
        size = QSpinBox(minimum=12, maximum=200, suffix=" px")
        size.setValue(int(cfg["size"]))
        size.valueChanged.connect(lambda v: self._set("size", v))
        row.addWidget(size)
        bold = QCheckBox("Bold")
        bold.setChecked(bool(cfg["bold"]))
        bold.toggled.connect(lambda on: self._set("bold", on))
        row.addWidget(bold)
        row.addStretch()
        form.addRow("Size", row)

        row = QHBoxLayout()
        row.addWidget(ColorButton(cfg["color"], lambda c: self._set("color", c)))
        row.addWidget(QLabel("  what you said (with Both):"))
        row.addWidget(ColorButton(cfg["original_color"], lambda c: self._set("original_color", c)))
        row.addStretch()
        form.addRow("Text colour", row)

        row = QHBoxLayout()
        outline = QSpinBox(minimum=0, maximum=20, suffix=" px")
        outline.setValue(int(cfg["outline"]))
        outline.valueChanged.connect(lambda v: self._set("outline", v))
        row.addWidget(outline)
        row.addWidget(ColorButton(cfg["outline_color"], lambda c: self._set("outline_color", c)))
        row.addStretch()
        form.addRow("Outline", row)

        row = QHBoxLayout()
        bg = QComboBox()
        for label, val in BACKGROUNDS:
            bg.addItem(label, val)
        bg.addItem("Other colour…", "")
        i = bg.findData(cfg["background"])
        bg.setCurrentIndex(i if i >= 0 else bg.count() - 1)
        self.bg_btn = ColorButton(cfg["background"], lambda c: self._set("background", c))

        def bg_changed(_i):
            val = bg.currentData()
            if val:
                self.bg_btn.set(val)
                self._set("background", val)
            else:
                self.bg_btn.click()
        bg.currentIndexChanged.connect(bg_changed)
        row.addWidget(bg)
        row.addWidget(self.bg_btn)
        row.addStretch()
        form.addRow("Background", row)

        form.addRow("Position", combo(ALIGN, "align"))
        sent = QSpinBox(minimum=1, maximum=6)
        sent.setValue(int(cfg["sentences"]))
        sent.valueChanged.connect(lambda v: self._set("sentences", v))
        form.addRow("Sentences shown", sent)
        clear = QDoubleSpinBox(minimum=0, maximum=600, decimals=0, suffix=" s")
        clear.setSpecialValueText("never")
        clear.setValue(float(cfg["clear_after"]))
        clear.valueChanged.connect(lambda v: self._set("clear_after", v))
        form.addRow("Empty after", clear)
        hint = QLabel("For OBS: add a <b>Window Capture</b> source and pick "
                      f"“{TITLE}”. With a green background, add a <b>Chroma Key</b> filter "
                      "to it. Or use the black background with the blend mode "
                      "<b>Screen</b> / <b>Add</b>.", objectName="hint", wordWrap=True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        sample = buttons.addButton("Show a sample", QDialogButtonBox.ActionRole)
        sample.clicked.connect(win.sample)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _set(self, key, value):
        self.win._set(key, value)
        if key in ("show", "sentences"):
            self.win.clear()
            self.win.sample()

