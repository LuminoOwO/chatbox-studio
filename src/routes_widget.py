"""Panel for editing translation routes: 'when I speak X, the chatbox gets Y'."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

import languages

# No language pairs are suggested: every language is equal, you pick yours.
PRESETS = [
    ("Only English, no translation", [("en", "en")]),
]


ROOM = "room"


def lang_combo(code: str, room: bool = False) -> QComboBox:
    c = QComboBox()
    if room:
        c.addItem("🎧 Room language", ROOM)
        c.setItemData(0, "Earshot: the language the people around you speak, picked "
                         "automatically. Turn Earshot on in its tab.", Qt.ToolTipRole)
    for lc, name, _ in languages.LANGUAGES:
        c.addItem(name, lc)
    c.setMaxVisibleItems(16)
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(9)
    i = c.findData(code)
    c.setCurrentIndex(max(i, 0))
    return c


class RouteRow(QWidget):
    changed = Signal()
    remove = Signal(object)
    reverse = Signal(object)

    def __init__(self, src: str, dst: str, enabled: bool = True):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.on = QCheckBox(checked=enabled)
        self.on.setToolTip("Switch this route on or off")
        self.on.setAccessibleName("Route on")
        self.src = lang_combo(src)
        self.dst = lang_combo(dst, room=True)
        self.src.setAccessibleName("From language")
        self.dst.setAccessibleName("To language")
        arrow = QLabel("→", objectName="arrow")
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setFixedWidth(18)
        rev = QPushButton("⇄")
        rev.setObjectName("icon")
        rev.setToolTip("Add the opposite direction too")
        rev.setAccessibleName("Add opposite direction")
        rev.setFixedWidth(34)
        rm = QPushButton("✕")
        rm.setObjectName("icon")
        rm.setToolTip("Remove this route")
        rm.setAccessibleName("Remove route")
        rm.setFixedWidth(34)
        lay.addWidget(self.on)
        lay.addWidget(self.src, 1)
        lay.addWidget(arrow)
        lay.addWidget(self.dst, 1)
        lay.addWidget(rev)
        lay.addWidget(rm)
        for w in (self.src, self.dst):
            w.currentIndexChanged.connect(self.changed)
        self.on.toggled.connect(self._on_toggle)
        rev.clicked.connect(lambda: self.reverse.emit(self))
        rm.clicked.connect(lambda: self.remove.emit(self))
        self._on_toggle(enabled)

    def _on_toggle(self, on):
        self.src.setEnabled(on)
        self.dst.setEnabled(on)
        self.changed.emit()

    def data(self) -> dict:
        return {"src": self.src.currentData(), "dst": self.dst.currentData(),
                "enabled": self.on.isChecked()}


class RoutesPanel(QFrame):
    changed = Signal()

    def __init__(self, routes: list[dict]):
        super().__init__(objectName="panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("TRANSLATION ROUTES", objectName="section")
        title.setStyleSheet("padding-top: 0;")
        head.addWidget(title)
        head.addStretch()
        presets = QPushButton("Presets ▾")
        menu = QMenu(presets)
        for label, pairs in PRESETS:
            menu.addAction(label, lambda p=pairs: self.set_routes(
                [{"src": a, "dst": b, "enabled": True} for a, b in p]))
        menu.addAction("Add the other direction of every route", self.add_reverse)
        menu.addAction("Translate everything I say into English", self.all_to_english)
        presets.setMenu(menu)
        add = QPushButton("+ Add route")
        add.clicked.connect(self.add_new)
        head.addWidget(presets)
        head.addWidget(add)
        outer.addLayout(head)

        hint = QLabel("When you speak the left language, the chatbox gets the right one. "
                      "All routes that are on work at the same time. Same language on both "
                      "sides = sent as you said it. “🎧 Room language” follows the people "
                      "around you (Earshot).", objectName="hint", wordWrap=True)
        outer.addWidget(hint)

        self.rows_box = QWidget(objectName="inner")
        self.rows = QVBoxLayout(self.rows_box)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(6)
        self.rows.addStretch()
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.rows_box)
        outer.addWidget(self.scroll)

        self.empty = QLabel("No routes - click “+ Add route” or pick a preset.", objectName="hint")
        outer.addWidget(self.empty)
        self.set_routes(routes, emit=False)

    # ---- rows
    def _rows(self) -> list[RouteRow]:
        return [self.rows.itemAt(i).widget() for i in range(self.rows.count())
                if isinstance(self.rows.itemAt(i).widget(), RouteRow)]

    def _add_row(self, src, dst, enabled=True) -> RouteRow:
        row = RouteRow(src, dst, enabled)
        row.changed.connect(self.changed)
        row.remove.connect(self._remove)
        row.reverse.connect(self._reverse)
        self.rows.insertWidget(self.rows.count() - 1, row)
        self._resize()
        return row

    def _resize(self):
        n = len(self._rows())
        self.scroll.setFixedHeight(min(max(n, 1), 4) * 42)
        self.scroll.setVisible(n > 0)
        self.empty.setVisible(n == 0)

    def set_routes(self, routes: list[dict], emit: bool = True):
        for row in self._rows():
            row.setParent(None)
            row.deleteLater()
        for r in routes:
            self._add_row(r.get("src", "en"), r.get("dst", "en"), r.get("enabled", True))
        self._resize()
        if emit:
            self.changed.emit()

    def add_new(self):
        """A new row starts as English → English; pick the languages in it."""
        self._add_row("en", "en")
        self.changed.emit()

    def add_reverse(self):
        rs = self.routes()
        have = {(r["src"], r["dst"]) for r in rs}
        extra = [{"src": r["dst"], "dst": r["src"], "enabled": True} for r in rs
                 if r["src"] != r["dst"] and r["dst"] != ROOM
                 and (r["dst"], r["src"]) not in have]
        if extra:
            self.set_routes(rs + extra)

    def all_to_english(self):
        rs = self.routes()
        srcs = []
        for r in rs:
            if r["src"] not in srcs and r["src"] != ROOM:
                srcs.append(r["src"])
        self.set_routes([{"src": s, "dst": "en", "enabled": True} for s in srcs or ["en"]])

    def _remove(self, row):
        row.setParent(None)
        row.deleteLater()
        self._resize()
        self.changed.emit()

    def _reverse(self, row):
        d = row.data()
        if (d["dst"], d["src"]) not in {(r["src"], r["dst"]) for r in self.routes()}:
            self._add_row(d["dst"], d["src"])
            self.changed.emit()

    def routes(self) -> list[dict]:
        return [r.data() for r in self._rows()]
