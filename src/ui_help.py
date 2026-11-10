"""Settings window, quick start, guided tour and the "Ready?" checklist."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

import theme


def hint(text: str) -> QLabel:
    lb = QLabel(text, objectName="hint", wordWrap=True)
    lb.setTextFormat(Qt.PlainText)
    return lb


# --------------------------------------------------------------------------- #
# Settings window
# --------------------------------------------------------------------------- #
class SettingsDialog(QDialog):
    """App-wide settings, one page per topic, with a search box."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(820, 620)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)
        side = QVBoxLayout()
        self.search = QLineEdit(placeholderText="Search settings…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search settings")
        self.search.textChanged.connect(self._filter)
        side.addWidget(self.search)
        self.list = QListWidget(objectName="elements")
        self.list.setFixedWidth(220)
        self.list.setAccessibleName("Settings pages")
        side.addWidget(self.list, 1)
        outer.addLayout(side)
        right = QVBoxLayout()
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        row = QHBoxLayout()
        self.note = hint("Changes are saved right away.")
        row.addWidget(self.note, 1)
        row.addWidget(close)
        right.addLayout(row)
        outer.addLayout(right, 1)
        self.list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.pages: dict[str, tuple[int, QWidget]] = {}

    def page(self, key: str, title: str, icon: str, intro: str = "") -> QFormLayout:
        w = QWidget(objectName="inner")
        form = QFormLayout(w)
        form.setContentsMargins(16, 6, 16, 16)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        head = QLabel(f"{icon}  {title}", objectName="title")
        head.setStyleSheet("font-size: 17px;")
        form.addRow(head)
        if intro:
            form.addRow(hint(intro))
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(w)
        frame = QFrame(objectName="panel")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(scroll)
        idx = self.stack.addWidget(frame)
        it = QListWidgetItem(f"{icon}  {title}")
        it.setData(Qt.UserRole, key)
        self.list.addItem(it)
        self.pages[key] = (idx, w)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        return form

    def open_page(self, key: Optional[str] = None):
        if key in self.pages:
            self.list.setCurrentRow(self.pages[key][0])
        self.show()
        self.raise_()
        self.activateWindow()

    def _texts(self, w: QWidget) -> str:
        out = []
        for child in w.findChildren(QWidget):
            if isinstance(child, (QLabel, QAbstractButton)):
                out.append(child.text())
            tip = child.toolTip()
            if tip:
                out.append(tip)
        return " ".join(out).lower()

    def _filter(self, q: str):
        q = q.strip().lower()
        first = None
        for key, (idx, w) in self.pages.items():
            hit = not q or q in self._texts(w)
            item = self.list.item(idx)
            item.setHidden(not hit)
            if hit and first is None:
                first = idx
        if first is not None and (self.list.currentRow() < 0 or
                                  self.list.item(self.list.currentRow()).isHidden()):
            self.list.setCurrentRow(first)


# --------------------------------------------------------------------------- #
# "Ready?" checklist
# --------------------------------------------------------------------------- #
class Checklist(QFrame):
    """What still needs doing before it works, each line with a button that fixes it."""

    def __init__(self):
        super().__init__(objectName="checklist")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(4)
        self.title = QLabel("GETTING STARTED", objectName="section")
        self.lay.addWidget(self.title)
        self.rows: dict[str, tuple[QLabel, QPushButton]] = {}

    def set_item(self, key: str, done: bool, text: str, fix_label: str = "",
                 fix: Optional[Callable] = None, visible: bool = True):
        if key not in self.rows:
            row = QHBoxLayout()
            lbl = QLabel(wordWrap=True)
            btn = QPushButton()
            btn.setVisible(False)
            row.addWidget(lbl, 1)
            row.addWidget(btn)
            self.lay.addLayout(row)
            self.rows[key] = (lbl, btn, row)
        lbl, btn, _row = self.rows[key]
        lbl.setVisible(visible)
        lbl.setText(("✅  " if done else "⬜  ") + text)
        lbl.setStyleSheet("" if not done else "color: #7d8590;")
        old = getattr(btn, "_conn", None)
        if old is not None:
            try:
                btn.clicked.disconnect(old)
            except (RuntimeError, TypeError):
                pass
            btn._conn = None
        btn.setVisible(visible and not done and bool(fix))
        if fix:
            btn.setText(fix_label or "Fix")
            btn._conn = btn.clicked.connect(fix)
        all_done = all(not lb.isVisible() or lb.text().startswith("✅")
                       for lb, _b, _r in self.rows.values())
        self.title.setText("✅ ALL SET" if all_done else "GETTING STARTED")


# --------------------------------------------------------------------------- #
# Guided tour
# --------------------------------------------------------------------------- #
class Tour(QWidget):
    """Dims the window and points at one control after the other, with an explanation."""
    finished = Signal()

    def __init__(self, window, steps: list[tuple[Callable[[], QWidget], str, str]]):
        super().__init__(window)
        self.win = window
        self.steps = steps
        self.i = 0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.bubble = QFrame(self, objectName="panel")
        self.bubble.setAttribute(Qt.WA_StyledBackground, True)
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(14, 12, 14, 12)
        self.b_title = QLabel(objectName="title")
        self.b_title.setStyleSheet("font-size: 15px;")
        self.b_text = hint("")
        self.b_text.setMinimumWidth(320)
        self.count = QLabel(objectName="hint")
        bl.addWidget(self.b_title)
        bl.addWidget(self.b_text)
        row = QHBoxLayout()
        row.addWidget(self.count)
        row.addStretch()
        self.back = QPushButton("Back")
        self.next = QPushButton("Next", objectName="primary")
        skip = QPushButton("End tour")
        row.addWidget(skip)
        row.addWidget(self.back)
        row.addWidget(self.next)
        bl.addLayout(row)
        self.back.clicked.connect(lambda: self.go(self.i - 1))
        self.next.clicked.connect(lambda: self.go(self.i + 1))
        skip.clicked.connect(self.end)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.end)
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.go(self.i + 1))
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.go(self.i - 1))
        window.installEventFilter(self)

    def start(self):
        self.setGeometry(self.win.rect())
        self.show()
        self.raise_()
        self.go(0)

    def eventFilter(self, obj, e):
        if obj is self.win and e.type() == QEvent.Resize:
            self.setGeometry(self.win.rect())
            self._place()
        return False

    def end(self):
        self.win.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        self.finished.emit()

    def go(self, i: int):
        if i >= len(self.steps):
            return self.end()
        self.i = max(0, i)
        get, title, text = self.steps[self.i]
        try:
            target = get()
            if hasattr(target, "_tour_prepare"):
                target._tour_prepare()
        except Exception:
            target = None
        self.target = target
        self.b_title.setText(title)
        self.b_text.setText(text)
        self.count.setText(f"{self.i + 1} / {len(self.steps)}")
        self.back.setEnabled(self.i > 0)
        self.next.setText("Done" if self.i == len(self.steps) - 1 else "Next")
        QTimer.singleShot(30, self._place)
        self.next.setFocus()

    def _target_rect(self) -> QRect:
        t = self.target
        if t is None or not t.isVisible():
            return QRect()
        top_left = t.mapTo(self.win, QPoint(0, 0))
        return QRect(top_left, t.size()).adjusted(-6, -6, 6, 6)

    def _place(self):
        r = self._target_rect()
        self.bubble.adjustSize()
        bw, bh = self.bubble.width(), self.bubble.height()
        W, H = self.width(), self.height()
        if r.isNull():
            x, y = (W - bw) // 2, (H - bh) // 2
        else:
            x = min(max(12, r.center().x() - bw // 2), W - bw - 12)
            y = r.bottom() + 14 if r.bottom() + 14 + bh < H else r.top() - bh - 14
            if y < 12:                                    # tall target: put it beside
                y = min(max(12, r.top()), H - bh - 12)
                x = r.right() + 14 if r.right() + 14 + bw < W else max(12, r.left() - bw - 14)
        self.bubble.move(x, y)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        r = self._target_rect()
        if not r.isNull():
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(r), 10, 10)
            path = path.subtracted(hole)
        p.fillPath(path, QColor(0, 0, 0, 150))
        if not r.isNull():
            pal = theme.palette(getattr(self.win.s, "ui_theme", "dark"))
            p.setPen(QPen(QColor(pal["accent"]), 3))
            p.drawRoundedRect(QRectF(r), 10, 10)

    def mousePressEvent(self, e):
        e.accept()                        # clicks outside the bubble do nothing


# --------------------------------------------------------------------------- #
# Quick start (first start)
# --------------------------------------------------------------------------- #
class QuickStart(QDialog):
    """Four short steps: microphone, languages, VRChat, done."""

    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.setWindowTitle("Quick start")
        self.resize(620, 470)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        row = QHBoxLayout()
        self.step = QLabel(objectName="hint")
        row.addWidget(self.step)
        row.addStretch()
        self.skip = QPushButton("Skip")
        self.back = QPushButton("Back")
        self.next = QPushButton("Next", objectName="primary")
        for b in (self.skip, self.back, self.next):
            row.addWidget(b)
        lay.addLayout(row)
        self.skip.clicked.connect(self.reject)
        self.back.clicked.connect(lambda: self.goto(self.stack.currentIndex() - 1))
        self.next.clicked.connect(lambda: self.goto(self.stack.currentIndex() + 1))
        self._build()
        self.goto(0)

    def _page(self, title, text):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        t = QLabel(title, objectName="title")
        t.setStyleSheet("font-size: 18px;")
        v.addWidget(t)
        v.addWidget(hint(text))
        self.stack.addWidget(w)
        return v

    def _build(self):
        from PySide6.QtWidgets import QComboBox
        import routes_widget
        w = self.w
        v = self._page("👋  Welcome to Chatbox Studio",
                       "It listens to your microphone, writes down what you say, translates it "
                       "if you want, and shows it in your VRChat chatbox - all on your own PC.\n\n"
                       "This takes about a minute. You can change everything later, and the "
                       "Help button (top right) brings this back.")
        v.addStretch()

        v = self._page("🎙  Your microphone",
                       "Pick the microphone you use in VRChat and say something. The bar should "
                       "move and turn blue while you talk.")
        self.mic = QComboBox()
        for i in range(w.mic.count()):
            self.mic.addItem(w.mic.itemText(i), w.mic.itemData(i))
        self.mic.setCurrentIndex(w.mic.currentIndex())
        self.mic.currentIndexChanged.connect(lambda i: w.mic.setCurrentIndex(i))
        v.addWidget(self.mic)
        self.meter = type(w.meter)()
        self.meter.theme = w.s.ui_theme
        w.bridge.level.connect(self._level)
        v.addWidget(self.meter)
        self.heard = hint("Waiting for your voice…")
        v.addWidget(self.heard)
        v.addStretch()

        v = self._page("🌍  Your languages",
                       "Which language do you speak, and which language should your chatbox "
                       "show? Pick the same one for no translation. You can add more "
                       "languages in “Translation routes” later.")
        rs = [r for r in w.routes.routes() if r.get("enabled", True)]
        first = rs[0] if rs else {"src": "en", "dst": "en"}
        row = QHBoxLayout()
        row.addWidget(QLabel("I speak"))
        self.lang_src = routes_widget.lang_combo(first.get("src", "en"))
        row.addWidget(self.lang_src, 1)
        row.addWidget(QLabel("→ chatbox shows"))
        self.lang_dst = routes_widget.lang_combo(
            first.get("dst", "en") if first.get("dst") != routes_widget.ROOM else "en")
        row.addWidget(self.lang_dst, 1)
        v.addLayout(row)
        self.lang_back = QCheckBox("Also the other way round (when I speak the chatbox "
                                   "language, show it in mine)")
        v.addWidget(self.lang_back)
        self.lang_note = hint("")
        v.addWidget(self.lang_note)
        for c in (self.lang_src, self.lang_dst):
            c.currentIndexChanged.connect(self._langs)
        self.lang_back.toggled.connect(self._langs)
        self.lang_back.setEnabled(self.lang_src.currentData() != self.lang_dst.currentData())
        v.addStretch()

        v = self._page("🥽  Connect VRChat",
                       "In VRChat open the Action Menu → Options → OSC → Enabled. That's all - "
                       "the app finds VRChat by itself.\n\nWhen you mute or unmute your mic in "
                       "VRChat once, the status at the top shows “VRChat mic on”.")
        test = QPushButton("Send a test message to my chatbox")
        test.clicked.connect(self._test)
        v.addWidget(test)
        self.test_lbl = hint("")
        v.addWidget(self.test_lbl)
        v.addStretch()

        v = self._page("✅  You're ready",
                       "Just talk - what you say appears in the Speech & translation tab and "
                       "in your chatbox.\n\n"
                       "• 💬 Chatbox tab: build your chatbox from blocks - clock, music, PC "
                       "stats…\n"
                       "• 🎧 Earshot tab: optional, the app also listens to the game for "
                       "better translations.\n"
                       "• ⚙ Settings (top right): theme, speech model, VRChat ports, filters.\n"
                       "• ❔ Help: this guide again, a tour of the window, keyboard shortcuts.")
        self.tour = QCheckBox("Show me around the window now", checked=True)
        v.addWidget(self.tour)
        v.addStretch()

    def _level(self, lv, voice):
        try:
            self.meter.set(lv, voice)
            if voice:
                self.heard.setText("✅ I can hear you.")
        except RuntimeError:
            pass

    def _langs(self, *_):
        a, b = self.lang_src.currentData(), self.lang_dst.currentData()
        routes = [{"src": a, "dst": b, "enabled": True}]
        same = a == b
        self.lang_back.setEnabled(not same)
        if self.lang_back.isChecked() and not same:
            routes.append({"src": b, "dst": a, "enabled": True})
        self.w.routes.set_routes(routes)
        self.lang_note.setText(
            "No translation - your words are written down as you say them." if same else
            "Translating needs the translator part (Setup → Standard). Into English, the "
            "speech model translates by itself.")

    def _test(self):
        e = self.w.engine
        if e is None:
            self.test_lbl.setText("The app is still starting - try again in a moment.")
            return
        e.chatbox.set_speech("Hello from Chatbox Studio! 👋")
        self.test_lbl.setText("Sent. If it doesn't show up in VRChat, check that OSC is enabled "
                              "(Action Menu → Options → OSC).")

    def goto(self, i: int):
        n = self.stack.count()
        if i >= n:
            self.accept()
            return
        i = max(0, i)
        self.stack.setCurrentIndex(i)
        self.step.setText(f"Step {i + 1} of {n}")
        self.back.setVisible(i > 0)
        self.skip.setVisible(i < n - 1)
        self.next.setText("Finish" if i == n - 1 else "Next")
