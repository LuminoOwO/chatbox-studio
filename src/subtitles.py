"""
Windows for OBS, a second screen or a stream overlay:

* **Subtitles** - what you say (and its translation) as big text on a plain background.
* **Earshot chat** - what Earshot hears around you, as a chat: one message per sentence,
  newest at the bottom. No names (Earshot can't tell people apart): optionally an icon or
  "You" / "Room", and your own lines on the other side.

Both: right-click for settings, double-click for full screen. A fixed window title each, so
OBS's Window Capture finds them again. Settings live in settings.extra["subtitles"] and
settings.extra["earshot_chat"].
"""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

import languages
from version import APP_NAME

SUB_TITLE = f"{APP_NAME} - Subtitles"
CHAT_TITLE = f"{APP_NAME} - Earshot"
TITLE = SUB_TITLE                      # (1.34 name)

# --------------------------------------------------------------------------- settings
STYLE = {                              # both windows
    "open": False,
    "font": "",                        # "" = the app's font
    "size": 44,
    "bold": True,
    "uppercase": False,
    "color": "#ffffff",
    "original_color": "#bfc6cf",
    "outline": 4,                      # px, 0 = none
    "outline_color": "#000000",
    "shadow": False,
    "background": "#000000",
    "box": False,                      # a box behind the text (caption style)
    "box_color": "#000000",
    "box_opacity": 70,                 # %
    "align_h": "center",               # left | center | right
    "max_width": 94,                   # % of the window
    "line_spacing": 108,               # %
    "fade": True,                      # fade in / out
    "paused": False,                   # take no new lines
    "on_top": False,
    "geometry": None,
}
SUB_DEFAULTS = dict(STYLE, show="translation", typed=True, align="bottom", sentences=2,
                    clear_after=10.0, lang_tag=False)
CHAT_DEFAULTS = dict(STYLE, size=30, outline=0, box=True, box_opacity=85, align_h="left",
                     max_width=80, show="both", mine=True, labels="icons", timestamps=False,
                     lang_tag=True, sides=True, box_mine="#1f6f43", box_color="#262b33",
                     messages=8, clear_after=0.0)

PRESETS = {
    "Outlined": dict(outline=4, shadow=False, box=False, background="#000000",
                     color="#ffffff"),
    # an opaque box: a chroma key keeps it whole (a see-through one would turn green)
    "Caption box (green screen)": dict(outline=0, shadow=False, box=True,
                                       box_color="#141414", box_opacity=100,
                                       background="#00ff00", color="#ffffff"),
    "Shadow": dict(outline=0, shadow=True, box=False, background="#000000", color="#ffffff"),
    "Chroma green": dict(outline=4, shadow=False, box=False, background="#00ff00",
                         color="#ffffff", outline_color="#000000"),
    "Yellow (classic TV)": dict(outline=3, shadow=False, box=False, background="#000000",
                                color="#ffe14d", outline_color="#000000"),
}
BACKGROUNDS = [("Black", "#000000"), ("Green (chroma key)", "#00ff00"),
               ("Blue (chroma key)", "#0000ff"), ("Magenta (chroma key)", "#ff00ff")]
SHOW = [("Translation", "translation"), ("What was said", "original"),
        ("Both", "both")]


def config(extra: dict, key: str = "subtitles") -> dict:
    defaults = CHAT_DEFAULTS if key == "earshot_chat" else SUB_DEFAULTS
    cfg = extra.get(key)
    if not isinstance(cfg, dict):
        cfg = {}
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    extra[key] = cfg
    return cfg


def wrap(text: str, fm: QFontMetricsF, width: float) -> list[str]:
    """Word wrap; words wider than the line (or text without spaces, e.g. Japanese) are
    broken between characters."""
    out = []
    width = max(width, fm.horizontalAdvance("W") * 2)
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


def same_text(a: str, b: str) -> bool:
    """Equal apart from case, spaces and punctuation (then a second line adds nothing)."""
    def key(t):
        return "".join(c for c in (t or "").casefold() if c.isalnum())
    return key(a) == key(b)


def _alpha(color: str, opacity_pct) -> QColor:
    c = QColor(color)
    c.setAlpha(int(255 * max(0, min(100, int(opacity_pct))) / 100))
    return c


# --------------------------------------------------------------------------- base window
class OverlayWindow(QWidget):
    """Shared by both windows: background, fonts, drawing outlined text, the menu."""
    closed = Signal()
    changed = Signal()             # settings changed (save them)
    TITLE = SUB_TITLE

    def __init__(self, cfg: dict):
        super().__init__(None, Qt.Window)
        self.cfg = cfg
        self.setWindowTitle(self.TITLE)
        self.setMinimumSize(320, 120)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._timer = QTimer(self, interval=500, timeout=self._tick)
        self._timer.start()
        g = cfg.get("geometry")
        if isinstance(g, list) and len(g) == 4:
            self.setGeometry(*[int(x) for x in g])
        else:
            self.resize(*self.default_size())
        self.apply()

    def default_size(self):
        return 1280, 260

    # ---- to override
    def _tick(self):
        pass

    def sample(self):
        pass

    def clear(self):
        pass

    def fields(self) -> list:
        return []

    def quick(self) -> list:
        """Switches in the right-click menu: ("check", label, key) or
        ("choice", label, key, [(text, value)])."""
        return []

    # ---- helpers
    def apply(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(self.cfg["on_top"]))
        self.update()

    def font_for(self, scale: float = 1.0) -> QFont:
        f = QFont(self.cfg["font"]) if self.cfg["font"] else QFont(self.font())
        f.setPixelSize(max(8, int(int(self.cfg["size"]) * scale)))
        f.setBold(bool(self.cfg["bold"]))
        return f

    def text_of(self, text: str) -> str:
        return text.upper() if self.cfg.get("uppercase") else text

    def line_h(self, fm: QFontMetricsF) -> float:
        return fm.height() * max(80, int(self.cfg.get("line_spacing", 108))) / 100

    def draw_line(self, p: QPainter, font: QFont, fm: QFontMetricsF, x: float, y: float,
                  text: str, color: str, plain: bool = False):
        """One line at (x, top y): shadow, outline, fill. plain: normal text drawing (colour
        emoji work there, not in outlines)."""
        if plain:
            p.setFont(font)
            p.setPen(QColor(color))
            p.drawText(QPointF(x, y + fm.ascent()), text)
            return
        path = QPainterPath()
        path.addText(QPointF(x, y + fm.ascent()), font, text)
        if self.cfg.get("shadow"):
            off = max(2.0, fm.height() * 0.06)
            p.fillPath(path.translated(off, off), QColor(0, 0, 0, 200))
        outline = float(self.cfg.get("outline") or 0)
        if outline > 0:
            pen = QPen(QColor(self.cfg["outline_color"]), outline * 2)
            pen.setJoinStyle(Qt.RoundJoin)
            p.strokePath(path, pen)
        p.fillPath(path, QColor(color))

    def x_for(self, w: float, left: float, right: float) -> float:
        a = self.cfg.get("align_h", "center")
        if a == "left":
            return left
        if a == "right":
            return right - w
        return (left + right - w) / 2

    def fade_of(self, born: float, now: float, life: float) -> float:
        if not self.cfg.get("fade"):
            return 1.0
        a = min(1.0, (now - born) / 0.25)
        if life > 0:
            a = min(a, max(0.0, (born + life - now) / 1.0))
        return max(0.0, a)

    def animating(self, on: bool):
        self._timer.setInterval(33 if on else 500)

    # ---- window
    def contextMenuEvent(self, e):
        m = QMenu(self)
        pause = m.addAction("⏸  Pause - take no new lines")
        pause.setCheckable(True)
        pause.setChecked(bool(self.cfg.get("paused")))
        pause.toggled.connect(lambda on: self.set_opt("paused", on))
        m.addSeparator()
        for item in self.quick():
            if item[0] == "check":
                _k, label, key = item
                a = m.addAction(label)
                a.setCheckable(True)
                if key == "outline":
                    a.setChecked(float(self.cfg.get("outline") or 0) > 0)
                    a.toggled.connect(self._toggle_outline)
                else:
                    a.setChecked(bool(self.cfg.get(key)))
                    a.toggled.connect(lambda on, key=key: self.set_opt(key, on))
            else:
                _k, label, key, options = item
                sub = m.addMenu(label)
                for text, val in options:
                    a = sub.addAction(text)
                    a.setCheckable(True)
                    a.setChecked(self.cfg.get(key) == val)
                    a.triggered.connect(lambda _c=False, key=key, val=val:
                                        self.set_opt(key, val))
        m.addSeparator()
        m.addAction("Settings…", self.open_settings)
        presets = m.addMenu("Look")
        for name in PRESETS:
            presets.addAction(name, lambda n=name: self.use_preset(n))
        m.addAction("Show a sample", self.sample)
        m.addAction("Clear", self.clear)
        m.addSeparator()
        top = m.addAction("Always on top")
        top.setCheckable(True)
        top.setChecked(bool(self.cfg["on_top"]))
        top.toggled.connect(lambda on: self.set_opt("on_top", on))
        full = m.addAction("Full screen (double-click)")
        full.setCheckable(True)
        full.setChecked(self.isFullScreen())
        full.toggled.connect(lambda _on: self._toggle_full())
        m.addSeparator()
        m.addAction("Close", self.close)
        m.exec(e.globalPos())

    def _toggle_outline(self, on: bool):
        if on:
            self.set_opt("outline", int(self.cfg.get("outline_was") or 4))
        else:
            self.cfg["outline_was"] = int(self.cfg.get("outline") or 4)
            self.set_opt("outline", 0)

    def use_preset(self, name: str):
        for k, v in PRESETS[name].items():
            if k == "box_color" and isinstance(self, ChatWindow):
                continue                       # the chat keeps its bubble colours
            self.cfg[k] = v
        self.update()
        self.changed.emit()

    def mouseDoubleClickEvent(self, _e):
        self._toggle_full()

    def _toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def set_opt(self, key, value):
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
        OverlaySettings(self).exec()


# --------------------------------------------------------------------------- subtitles
class SubtitleWindow(OverlayWindow):
    TITLE = SUB_TITLE

    def __init__(self, cfg: dict):
        self.entries: deque = deque()      # (time, [(kind, text, lang)])
        super().__init__(cfg)

    def add(self, original: str, translations: list, src: str = "", dsts: list | None = None):
        """One sentence: what was said and its translations (empty when not translated)."""
        if self.cfg.get("paused"):
            return
        show = self.cfg["show"]
        dsts = list(dsts or [])
        parts = []
        if show in ("original", "both") and original.strip():
            parts.append(("orig" if show == "both" else "main", original.strip(), src))
        if show in ("translation", "both"):
            trs = [(t.strip(), dsts[i] if i < len(dsts) else "")
                   for i, t in enumerate(translations) if t.strip()
                   and not (show == "both" and same_text(t, original))]
            if not trs and show == "translation" and original.strip():
                trs = [(original.strip(), src)]        # nothing to translate
            parts += [("main", t, lang) for t, lang in trs]
            if show == "both" and not trs:          # nothing new to show: one big line
                parts = [("main",) + p[1:] for p in parts]
        if not parts:
            return
        self.entries.append((time.monotonic(), parts))
        while len(self.entries) > max(1, int(self.cfg["sentences"])):
            self.entries.popleft()
        self.animating(bool(self.cfg.get("fade")))
        self.update()

    def clear(self):
        self.entries.clear()
        self.update()

    def _tick(self):
        now = time.monotonic()
        after = float(self.cfg.get("clear_after") or 0)
        if after > 0 and self.entries and now - self.entries[-1][0] > after:
            self.clear()
        moving = self.cfg.get("fade") and any(
            now - t < 0.3 or (after > 0 and now > t + after - 1.1) for t, _p in self.entries)
        self.animating(bool(moving))
        if moving:
            self.update()

    def sample(self):
        self.add("This is how your subtitles look.",
                 ["And this is where the translation shows."], "en", ["en"])

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(self.cfg["background"]))
        margin = max(12.0, self.width() * 0.03)
        maxw = self.width() * max(30, min(100, int(self.cfg["max_width"]))) / 100 - 2 * margin
        maxw = min(maxw, self.width() - 2 * margin)
        pad = (int(self.cfg["size"]) * 0.28) if self.cfg.get("box") else 0.0
        inner = maxw - 2 * pad - 2 * float(self.cfg.get("outline") or 0)
        now = time.monotonic()
        life = float(self.cfg.get("clear_after") or 0)

        blocks = []                         # (alpha, [(font, fm, color, line, h)])
        for born, parts in self.entries:
            rows = []
            for kind, text, lang in parts:
                font = self.font_for(0.62 if kind == "orig" else 1.0)
                fm = QFontMetricsF(font)
                color = self.cfg["original_color"] if kind == "orig" else self.cfg["color"]
                tag = f"{lang.upper()} · " if self.cfg.get("lang_tag") and lang else ""
                for line in wrap(tag + self.text_of(text), fm, inner):
                    rows.append((font, fm, color, line, self.line_h(fm)))
            if rows:
                blocks.append((self.fade_of(born, now, life), rows))
        if not blocks:
            return
        gap = int(self.cfg["size"]) * 0.3
        heights = [sum(r[4] for r in rows) + 2 * pad for _a, rows in blocks]
        avail = self.height() - 2 * margin
        while len(blocks) > 1 and sum(heights) + gap * (len(blocks) - 1) > avail:
            blocks.pop(0)                    # too much: the oldest sentences go first
            heights.pop(0)
        total = sum(heights) + gap * (len(blocks) - 1)
        align = self.cfg["align"]
        y = (margin if align == "top" else self.height() - margin - total if align == "bottom"
             else (self.height() - total) / 2)
        for (alpha, rows), h in zip(blocks, heights):
            p.setOpacity(alpha)
            width = max(fm.horizontalAdvance(line) for _f, fm, _c, line, _h in rows)
            if self.cfg.get("box"):
                bx = self.x_for(width + 2 * pad, margin, self.width() - margin)
                p.setPen(Qt.NoPen)
                p.setBrush(_alpha(self.cfg["box_color"], self.cfg["box_opacity"]))
                p.drawRoundedRect(QRectF(bx, y, width + 2 * pad, h), pad * 0.6, pad * 0.6)
            ly = y + pad
            for font, fm, color, line, lh in rows:
                lw = fm.horizontalAdvance(line)
                if self.cfg.get("box"):
                    a = self.cfg.get("align_h", "center")
                    lx = (bx + pad if a == "left" else bx + pad + width - lw if a == "right"
                          else bx + pad + (width - lw) / 2)
                else:
                    lx = self.x_for(lw, margin, self.width() - margin)
                self.draw_line(p, font, fm, lx, ly + (lh - fm.height()) / 2, line, color)
                ly += lh
            y += h + gap
        p.setOpacity(1.0)

    def quick(self):
        return [
            ("choice", "Show", "show", SHOW),
            ("check", "Messages you type", "typed"),
            ("check", "Language in front", "lang_tag"),
            ("check", "Box behind the text", "box"),
            ("check", "Outline", "outline"),
            ("check", "Drop shadow", "shadow"),
            ("check", "CAPITALS", "uppercase"),
            ("check", "Fade in and out", "fade"),
        ]

    def fields(self):
        return [
            ("Show", "combo", "show", SHOW),
            ("", "check", "typed", "Also messages you type"),
            ("", "check", "lang_tag", "Language in front (EN · …)"),
            ("Position", "combo", "align", [("Bottom", "bottom"), ("Middle", "center"),
                                           ("Top", "top")]),
            ("Sentences shown", "spin", "sentences", (1, 6, "")),
            ("Empty after", "dspin", "clear_after", (0, 600, " s", "never")),
        ]


# --------------------------------------------------------------------------- Earshot chat
class ChatWindow(OverlayWindow):
    """What Earshot hears, as chat messages (no names - Earshot can't tell people apart)."""
    TITLE = CHAT_TITLE

    def __init__(self, cfg: dict):
        self.msgs: deque = deque(maxlen=60)   # (born, line)
        super().__init__(cfg)

    def default_size(self):
        return 620, 760

    def add(self, ln):
        if self.cfg.get("paused"):
            return
        if ln.who != "them" and not self.cfg.get("mine", True):
            return
        self.msgs.append((time.monotonic(), ln))
        self.animating(bool(self.cfg.get("fade")))
        self.update()

    def clear(self):
        self.msgs.clear()
        self.update()

    def _tick(self):
        now = time.monotonic()
        life = float(self.cfg.get("clear_after") or 0)
        if life > 0:
            while self.msgs and now - self.msgs[0][0] > life:
                self.msgs.popleft()
                self.update()
        moving = self.cfg.get("fade") and any(
            now - t < 0.3 or (life > 0 and now > t + life - 1.1) for t, _l in self.msgs)
        self.animating(bool(moving))
        if moving:
            self.update()

    def sample(self):
        from types import SimpleNamespace as NS
        t = time.time()
        for who, lang, text, tr in (
                ("them", "es", "¿Alguien sabe dónde está el mundo del espejo?",
                 "Does anyone know where the mirror world is?"),
                ("you", "en", "It's in the back, past the stairs.",
                 "Está atrás, pasando las escaleras."),
                ("them", "es", "¡Gracias!", "Thanks!")):
            self.add(NS(t=t, who=who, lang=lang, text=text, translation=tr))

    def _texts(self, ln) -> list:
        """[(kind, text)] for a message: main text + optional small second line."""
        show = self.cfg["show"]
        said, tr = ln.text.strip(), (ln.translation or "").strip()
        if same_text(said, tr):                    # not translated: one line is enough
            tr = ""
        if show == "original" or not tr:
            return [("main", said)]
        if show == "translation":
            return [("main", tr)]
        return [("main", tr), ("orig", said)] if ln.who == "them" else \
            [("main", said), ("orig", tr)]

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(self.cfg["background"]))
        margin = max(10.0, self.width() * 0.025)
        maxw = (self.width() - 2 * margin) * max(30, min(100, int(self.cfg["max_width"]))) / 100
        box = bool(self.cfg.get("box"))
        pad = int(self.cfg["size"]) * (0.45 if box else 0.0)
        now = time.monotonic()
        life = float(self.cfg.get("clear_after") or 0)
        small = self.font_for(0.55)
        small_fm = QFontMetricsF(small)
        limit = max(1, int(self.cfg.get("messages", 8)))
        y = self.height() - margin
        gap = int(self.cfg["size"]) * 0.35
        for born, ln in list(self.msgs)[-limit:][::-1]:      # newest at the bottom
            mine = ln.who != "them"
            rows = []
            head = []
            if self.cfg.get("labels") == "icons":
                head.append("🗣" if mine else "🎧")
            elif self.cfg.get("labels") == "words":
                head.append("You" if mine else "Room")
            if self.cfg.get("lang_tag") and ln.lang:
                head.append(languages.name(ln.lang) if self.cfg.get("labels") == "words"
                            else ln.lang.upper())
            if self.cfg.get("timestamps"):
                head.append(time.strftime("%H:%M", time.localtime(getattr(ln, "t", time.time()))))
            inner = maxw - 2 * pad - 2 * float(self.cfg.get("outline") or 0)
            if head:
                rows.append((small, small_fm, self.cfg["original_color"], " · ".join(head),
                             self.line_h(small_fm)))
            head_row = rows[0] if head else None
            for kind, text in self._texts(ln):
                font = self.font_for(0.68 if kind == "orig" else 1.0)
                fm = QFontMetricsF(font)
                color = self.cfg["original_color"] if kind == "orig" else self.cfg["color"]
                for line in wrap(self.text_of(text), fm, inner):
                    rows.append((font, fm, color, line, self.line_h(fm)))
            if not rows:
                continue
            width = max(fm.horizontalAdvance(line) for _f, fm, _c, line, _h in rows)
            h = sum(r[4] for r in rows) + 2 * pad
            y -= h
            if y + h < 0:
                break
            right = mine and self.cfg.get("sides", True)
            if right:
                bx = self.width() - margin - width - 2 * pad
            else:
                bx = self.x_for(width + 2 * pad, margin, self.width() - margin) \
                    if not self.cfg.get("sides", True) else margin
            p.setOpacity(self.fade_of(born, now, life))
            if box:
                p.setPen(Qt.NoPen)
                col = self.cfg["box_mine"] if mine else self.cfg["box_color"]
                p.setBrush(_alpha(col, self.cfg["box_opacity"]))
                p.drawRoundedRect(QRectF(bx, y, width + 2 * pad, h), pad * 0.8, pad * 0.8)
            ly = y + pad
            for row in rows:
                font, fm, color, line, lh = row
                lx = bx + pad + (width - fm.horizontalAdvance(line) if right else 0)
                self.draw_line(p, font, fm, lx, ly + (lh - fm.height()) / 2, line, color,
                               plain=row is head_row)
                ly += lh
            y -= gap
        p.setOpacity(1.0)

    def quick(self):
        return [
            ("choice", "Show", "show", [("Translation", "translation"),
                                        ("What was said", "original"), ("Both", "both")]),
            ("choice", "Mark who's talking", "labels", [("Icons (🎧 / 🗣)", "icons"),
                                                        ("Words (Room / You)", "words"),
                                                        ("Nothing", "none")]),
            ("check", "Your own sentences", "mine"),
            ("check", "Yours on the right", "sides"),
            ("check", "Language", "lang_tag"),
            ("check", "Time", "timestamps"),
            ("check", "Bubbles", "box"),
            ("check", "Fade in and out", "fade"),
        ]

    def fields(self):
        return [
            ("Show", "combo", "show", [("Translation", "translation"),
                                       ("What was said", "original"),
                                       ("Both (the other one small below)", "both")]),
            ("", "check", "mine", "Your own sentences too"),
            ("", "check", "sides", "Yours on the right, the room on the left"),
            ("Mark who's talking", "combo", "labels", [("Icons (🎧 / 🗣)", "icons"),
                                                       ("Words (Room / You)", "words"),
                                                       ("Nothing", "none")]),
            ("", "check", "lang_tag", "Show the language"),
            ("", "check", "timestamps", "Show the time"),
            ("Bubble for yours", "color", "box_mine", None),
            ("Messages shown", "spin", "messages", (1, 30, "")),
            ("Messages vanish after", "dspin", "clear_after", (0, 3600, " s", "never")),
        ]


# --------------------------------------------------------------------------- settings dialog
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


class OverlaySettings(QDialog):
    """Changes show in the window right away."""

    def __init__(self, win: OverlayWindow):
        super().__init__(win)
        self.win = win
        chat = isinstance(win, ChatWindow)
        self.setWindowTitle("Earshot chat settings" if chat else "Subtitle settings")
        outer = QVBoxLayout(self)
        scroll = QScrollArea(widgetResizable=True)
        body = QWidget()
        self.form = QFormLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.resize(560, 720)
        self.widgets = {}

        look = QComboBox()
        look.addItem("Pick a look…", "")
        for name in PRESETS:
            look.addItem(name, name)
        look.currentIndexChanged.connect(lambda _i: look.currentData() and self._preset(
            look.currentData()))
        self.form.addRow("Look", look)
        for spec in win.fields():
            self._add(*spec)
        self._heading("Text")
        for spec in [
            ("Font", "font", "font", None),
            ("Size", "spin", "size", (12, 200, " px")),
            ("", "check", "bold", "Bold"),
            ("", "check", "uppercase", "CAPITALS"),
            ("Text colour", "color", "color", None),
            ("Small text colour", "color", "original_color", None),
            ("Outline", "spin", "outline", (0, 20, " px")),
            ("Outline colour", "color", "outline_color", None),
            ("", "check", "shadow", "Drop shadow"),
            ("Line spacing", "spin", "line_spacing", (80, 200, " %")),
            ("Width", "spin", "max_width", (30, 100, " % of the window")),
            ("Alignment", "combo", "align_h", [("Left", "left"), ("Centre", "center"),
                                               ("Right", "right")]),
        ]:
            self._add(*spec)
        self._heading("Background")
        self._background()
        for spec in [
            ("", "check", "box", "Bubbles behind the messages" if chat else
             "Box behind the text (caption style)"),
            ("Bubble colour" if chat else "Box colour", "color", "box_color", None),
            ("Opacity", "spin", "box_opacity", (0, 100, " %")),
            ("", "check", "fade", "Fade in and out"),
        ]:
            self._add(*spec)
        title = win.TITLE
        hint = QLabel("For OBS: add a <b>Window Capture</b> source and pick "
                      f"“{title}”. On black, set the source's blend mode to <b>Screen</b> or "
                      "<b>Add</b>; with a green background, add a <b>Chroma Key</b> filter "
                      "(boxes and bubbles then need 100 % opacity).",
                      objectName="hint", wordWrap=True)
        self.form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        sample = buttons.addButton("Show a sample", QDialogButtonBox.ActionRole)
        sample.clicked.connect(win.sample)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _heading(self, text):
        lab = QLabel(f"<b>{text}</b>")
        lab.setContentsMargins(0, 10, 0, 0)
        self.form.addRow(lab)

    def _add(self, label, kind, key, arg):
        cfg = self.win.cfg
        if kind == "combo":
            w = QComboBox()
            for text, val in arg:
                w.addItem(text, val)
            w.setCurrentIndex(max(0, w.findData(cfg[key])))
            w.currentIndexChanged.connect(lambda _i, w=w: self._set(key, w.currentData()))
        elif kind == "check":
            w = QCheckBox(arg)
            w.setChecked(bool(cfg[key]))
            w.toggled.connect(lambda on: self._set(key, on))
        elif kind == "spin":
            lo, hi, suffix = arg
            w = QSpinBox(minimum=lo, maximum=hi, suffix=suffix)
            w.setValue(int(cfg[key]))
            w.valueChanged.connect(lambda v: self._set(key, v))
        elif kind == "dspin":
            lo, hi, suffix, special = arg
            w = QDoubleSpinBox(minimum=lo, maximum=hi, decimals=0, suffix=suffix)
            w.setSpecialValueText(special)
            w.setValue(float(cfg[key]))
            w.valueChanged.connect(lambda v: self._set(key, v))
        elif kind == "color":
            w = ColorButton(cfg[key], lambda c: self._set(key, c))
        elif kind == "font":
            w = QFontComboBox()
            if cfg[key]:
                w.setCurrentFont(QFont(cfg[key]))
            w.currentFontChanged.connect(lambda f: self._set(key, f.family()))
        else:
            return
        self.widgets[key] = w
        self.form.addRow(label, w)

    def _background(self):
        row = QHBoxLayout()
        bg = QComboBox()
        for label, val in BACKGROUNDS:
            bg.addItem(label, val)
        bg.addItem("Other colour…", "")
        i = bg.findData(self.win.cfg["background"])
        bg.setCurrentIndex(i if i >= 0 else bg.count() - 1)
        btn = ColorButton(self.win.cfg["background"], lambda c: self._set("background", c))

        def changed(_i):
            if bg.currentData():
                btn.set(bg.currentData())
                self._set("background", bg.currentData())
            else:
                btn.click()
        bg.currentIndexChanged.connect(changed)
        row.addWidget(bg)
        row.addWidget(btn)
        row.addStretch()
        self.form.addRow("Window", row)
        self.widgets["background"] = (bg, btn)

    def _preset(self, name):
        self.win.use_preset(name)
        cfg = self.win.cfg
        for key, w in self.widgets.items():         # show the new values
            if key == "background":
                bg, btn = w
                bg.blockSignals(True)
                i = bg.findData(cfg[key])
                bg.setCurrentIndex(i if i >= 0 else bg.count() - 1)
                bg.blockSignals(False)
                btn.set(cfg[key])
                continue
            w.blockSignals(True)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(cfg[key]))
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(cfg[key])
            elif isinstance(w, ColorButton):
                w.set(cfg[key])
            elif isinstance(w, QComboBox) and not isinstance(w, QFontComboBox):
                w.setCurrentIndex(max(0, w.findData(cfg[key])))
            w.blockSignals(False)

    def _set(self, key, value):
        self.win.set_opt(key, value)
        if key in ("show", "sentences", "mine", "messages"):
            self.win.clear()
            self.win.sample()
