"""
Live graphics card usage in the main window: for every card Chatbox Studio uses (or is set
to use), its memory (Chatbox Studio / other programs / free) and a graph of its load and
memory over the last few minutes.
"""

from __future__ import annotations

import threading
from collections import deque

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

GREY = "#8b949e"
LOAD = "#3fb950"            # whole-card load
MEMC = "#d29922"            # memory line in the graph
HISTORY = 90                # samples kept (3 minutes at one every 2 s)


def gb(mb: float) -> str:
    return f"{mb / 1024:.1f}"


class Bar(QWidget):
    """Segments drawn left to right: [(fraction, colour)], over a faint track."""

    def __init__(self, height=10):
        super().__init__()
        self.setFixedHeight(height)
        self.parts: list = []

    def set_parts(self, parts):
        self.parts = parts
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = self.rect()
        rad = r.height() / 2
        p.setBrush(QColor(127, 127, 127, 55))
        p.drawRoundedRect(r, rad, rad)
        # draw from the end so earlier segments sit on top with rounded ends
        x = 0.0
        stops = []
        for frac, col in self.parts:
            x = min(1.0, x + max(0.0, frac))
            stops.append((x, col))
        for x, col in reversed(stops):
            w = int(r.width() * x)
            if w > 0:
                p.setBrush(QColor(col))
                p.drawRoundedRect(0, 0, max(w, int(rad * 2)), r.height(), rad, rad)
        p.end()


class Graph(QWidget):
    """Load (filled area) and memory use (line) over time, newest on the right, 0-100 %."""

    def __init__(self, accent: str):
        super().__init__()
        self.setFixedHeight(54)
        self.accent = accent
        self.load: deque = deque(maxlen=HISTORY)
        self.ours: deque = deque(maxlen=HISTORY)
        self.mem: deque = deque(maxlen=HISTORY)

    def add(self, load, ours, mem):
        self.load.append(load)
        self.ours.append(ours)
        self.mem.append(mem)
        self.update()

    def _path(self, values, w, h, closed):
        n = HISTORY - 1
        start = HISTORY - len(values)
        path = QPainterPath()
        pts = [(w * (start + i) / n, h - h * max(0.0, min(100.0, v)) / 100)
               for i, v in enumerate(values) if v is not None]
        if len(pts) < 2:
            return None
        if closed:
            path.moveTo(pts[0][0], h)
            for x, y in pts:
                path.lineTo(x, y)
            path.lineTo(pts[-1][0], h)
            path.closeSubpath()
        else:
            path.moveTo(*pts[0])
            for x, y in pts[1:]:
                path.lineTo(x, y)
        return path

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 1, -1, -1)
        w, h = r.width(), r.height()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(127, 127, 127, 30))
        p.drawRoundedRect(r, 4, 4)
        grid = QColor(127, 127, 127, 50)
        p.setPen(QPen(grid, 1, Qt.DotLine))
        for frac in (0.25, 0.5, 0.75):
            p.drawLine(0, int(h * frac), w, int(h * frac))
        p.translate(r.topLeft())
        load = [v for v in self.load]
        path = self._path(load, w, h, True)
        if path is not None:
            col = QColor(LOAD)
            p.setPen(QPen(col, 1.4))
            col.setAlpha(70)
            p.setBrush(col)
            p.drawPath(path)
        if any(v is not None for v in self.ours):
            path = self._path([v if v is not None else 0 for v in self.ours], w, h, True)
            if path is not None:
                col = QColor(self.accent)
                p.setPen(Qt.NoPen)
                col.setAlpha(150)
                p.setBrush(col)
                p.drawPath(path)
        path = self._path(list(self.mem), w, h, False)
        if path is not None:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(MEMC), 1.6))
            p.drawPath(path)
        p.end()


class CardRow(QWidget):
    def __init__(self, accent: str):
        super().__init__()
        self.accent = accent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 4)
        lay.setSpacing(3)
        head = QHBoxLayout()
        self.name = QLabel()
        f = self.name.font()
        f.setBold(True)
        self.name.setFont(f)
        self.temp = QLabel(objectName="hint")
        head.addWidget(self.name, 1)
        head.addWidget(self.temp)
        lay.addLayout(head)
        self.mem = Bar()
        lay.addWidget(self.mem)
        self.mem_txt = QLabel(objectName="hint")
        lay.addWidget(self.mem_txt)
        self.graph = Graph(accent)
        lay.addWidget(self.graph)
        self.load_txt = QLabel(objectName="hint", wordWrap=True)
        lay.addWidget(self.load_txt)
        self.models_txt = QLabel(objectName="hint", wordWrap=True)
        lay.addWidget(self.models_txt)

    def show_usage(self, name: str, models: list, u):
        """models: [(label, loaded)]"""
        self.graph.accent = self.accent
        self.name.setText(name)
        names = [m if ok else f"{m} - not loaded yet" for m, ok in models]
        self.models_txt.setText("Runs: " + ", ".join(names) if models else
                                "Not used by Chatbox Studio")
        tip = ["Chatbox Studio: " + (", ".join(names) if models else "nothing on this card")]
        if u is None or not u.ok:
            self.temp.setText("")
            self.mem.set_parts([])
            self.mem_txt.setText("Memory use can't be read for this card.")
            self.graph.setVisible(False)
            self.load_txt.setVisible(False)
            self.setToolTip("\n".join(tip))
            return
        total = max(1, u.total_mb)
        self.temp.setText(f"{u.temp} °C" if u.temp is not None else "")
        self.mem.set_parts([(u.ours_mb / total, self.accent), (u.others_mb / total, GREY)])
        free = max(0, u.total_mb - u.used_mb)
        self.mem_txt.setText(f"Chatbox {gb(u.ours_mb)} · others {gb(u.others_mb)} · "
                             f"{gb(free)} free of {u.total_mb / 1024:.0f} GB")
        self.graph.setVisible(True)
        self.load_txt.setVisible(True)
        ours = u.ours_util
        self.graph.add(u.util, ours, u.used_mb * 100 / total)
        load = f"<span style='color:{LOAD}'>■</span> Load " + (
            f"{u.util} %" if u.util is not None else "n/a") + (
            f" (<span style='color:{self.accent}'>■</span> ours {ours} %)"
            if ours is not None else "")
        self.load_txt.setText(f"{load} &nbsp; <span style='color:{MEMC}'>━</span> Memory "
                              f"{u.used_mb * 100 // total} %")
        self.graph.setToolTip("The last 3 minutes: load (green area"
                              + (", Chatbox Studio's share in colour" if ours is not None else "")
                              + ") and memory use (line), 0-100 %")
        tip.append(f"Memory in use: {gb(u.used_mb)} of {gb(u.total_mb)} GB")
        if u.ours_mb:
            tip.append(f"   Chatbox Studio  {u.ours_mb / 1024:.2f} GB")
        tip += [f"   {n}  {v / 1024:.2f} GB" for n, v in u.procs[:8]]
        rest = u.others_mb - sum(v for _n, v in u.procs)
        if rest > 64:
            tip.append(f"   desktop, drivers and others  {rest / 1024:.2f} GB")
        self.setToolTip("\n".join(tip))


class _Relay(QObject):
    done = Signal(object)


class GpuMonitor(QWidget):
    """Polls every 2 s while the window is shown and not minimised."""

    def __init__(self, accent: str = "#58a6ff", show_all: bool = False):
        super().__init__()
        self.accent = accent
        self.show_all = show_all              # also cards Chatbox Studio doesn't use
        self.engine = None
        self._gpus = None
        self._busy = False
        self.rows: dict = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        lay.addLayout(self.rows_box)
        self.empty = QLabel("Everything runs on the CPU - no graphics card in use.",
                            objectName="hint", wordWrap=True)
        lay.addWidget(self.empty)
        self.relay = _Relay()
        self.relay.done.connect(self._show)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)

    def start(self, engine, interval_ms: int = 2000):
        self.engine = engine
        self.timer.start(interval_ms)
        QTimer.singleShot(500, self.poll)

    def stop(self):
        self.timer.stop()

    def _gpu_list(self):
        if self._gpus is None:
            import devices
            self._gpus = devices.available_gpus()
        return self._gpus

    def set_show_all(self, on: bool):
        self.show_all = on
        QTimer.singleShot(0, self.poll)

    def poll(self):
        win = self.window()
        if self._busy or self.engine is None or not self.isVisible() or win.isMinimized():
            return
        try:
            places = self.engine.configured_places()
        except Exception:
            try:
                places = [(lb, sp, True) for lb, sp in self.engine.model_places()]
            except Exception:
                places = []
        used: dict = {}
        for label, spec, loaded in places:
            if spec != "cpu":
                used.setdefault(spec, []).append((label, loaded))
        if not used and not self.show_all:
            self._show(({}, {}, []))
            return
        self._busy = True

        def work():
            res = {}
            gl = []
            try:
                import vram
                gl = self._gpu_list()
                res = vram.measure([g for g in gl if self.show_all or
                                    f"{g.vendor}:{g.index}" in used])
            except Exception:
                pass
            self._busy = False
            self.relay.done.emit((used, res, gl))
        threading.Thread(target=work, daemon=True, name="gpu-monitor").start()

    def _show(self, data):
        used, res, gl = data
        names = {f"{g.vendor}:{g.index}": g.name.replace("NVIDIA GeForce ", "")
                 .replace("AMD Radeon ", "Radeon ") for g in gl}
        keys = list(used)                    # speech model's card first
        if self.show_all:
            keys += [k for k in names if k not in used]
        for key in list(self.rows):
            if key not in keys:
                self.rows.pop(key).deleteLater()
        for i, key in enumerate(keys):
            row = self.rows.get(key)
            if row is None:
                row = self.rows[key] = CardRow(self.accent)
            self.rows_box.insertWidget(i, row)     # keeps the order
            row.show_usage(names.get(key, key), used.get(key, []), res.get(key))
        self.empty.setVisible(not keys)
