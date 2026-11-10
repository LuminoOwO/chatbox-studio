"""GUI for the chatbox: a block list (everything you can put into the chatbox), a
VRChat-style chatbox you build by dragging blocks into it, the block settings, the privacy
bar, and the media filter dialog."""

from __future__ import annotations

import copy
import json
import re
from typing import Optional

from PySide6.QtCore import QDateTime, QMimeData, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QDrag, QFont, QFontMetrics, QKeySequence, QPainter,
                           QPen, QPixmap, QShortcut, QSyntaxHighlighter, QTextCharFormat)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import blocks as B
import chatbox as C
import media as M
import theme

TIMEZONES = ["local", "UTC", "Europe/Berlin", "Europe/London", "Europe/Paris", "Europe/Moscow",
             "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
             "America/Sao_Paulo", "Asia/Tokyo", "Asia/Seoul", "Asia/Shanghai", "Asia/Kolkata",
             "Asia/Dubai", "Australia/Sydney", "Pacific/Auckland"]

MIME_NEW = "application/x-chatbox-studio-new"
MIME_MOVE = "application/x-chatbox-studio-move"


def hint(text: str) -> QLabel:
    lbl = QLabel(text, objectName="hint", wordWrap=True)
    lbl.setTextFormat(Qt.PlainText)
    return lbl


# --------------------------------------------------------------------------- #
# VRChat look
# --------------------------------------------------------------------------- #
# The in-game chatbox (VRChat 2026.3, measured by the vrc-live-caption project): TextMesh Pro
# text in a 300 x 265 unit box with 10-unit margins (280 units of text width), Noto Sans
# Regular at size 18, centered, word wrap, at most 9 visible lines, monochrome Noto Emoji.
VRC_BOX_W, VRC_MARGIN, VRC_FONT, VRC_LINES = 300.0, 10.0, 18.0, 9
FONT_FAMILIES = ["Noto Sans", "Noto Emoji"]
_EMOJI = re.compile("([←-⯿〰〽㊗㊙\U0001F000-\U0001FAFF])️?")


def mono_emoji(text: str) -> str:
    """Asks for the black-and-white emoji, like VRChat shows them (text presentation)."""
    return _EMOJI.sub(lambda m: m.group(1) + "︎", text)


def load_fonts():
    """Bundled Noto Sans (VRChat's chatbox font) and monochrome Noto Emoji."""
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase
    d = Path(__file__).resolve().parent / "fonts"
    for f in sorted(d.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))


def chatbox_font(px: float) -> QFont:
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPixelSize(max(9, round(px)))
    return f


def _drag_pixmap(text, widget=None):
    f = chatbox_font(15)
    fm = QFontMetrics(f)
    text = mono_emoji(text)
    w, h = min(420, fm.horizontalAdvance(text) + 24), fm.height() + 12
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(12, 12, 14, 225))
    p.setPen(QColor(255, 255, 255, 110))
    p.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)
    p.setPen(QColor("#ffffff"))
    p.setFont(f)
    p.drawText(pm.rect().adjusted(8, 0, -8, 0), Qt.AlignCenter,
               fm.elidedText(text, Qt.ElideRight, w - 16))
    p.end()
    return pm


# --------------------------------------------------------------------------- #
# Block names
# --------------------------------------------------------------------------- #
def token_label(key: str) -> str:
    return f"‹{B.short_name(key)}›" if key in B.KEY_INFO else "{" + key + "}"


def block_name(b: dict) -> str:
    t = b["type"]
    if t == "speech":
        return "💬 Speech / translation"
    if t == "newline":
        return "↵ New line"
    if t == "value":
        return B.short_name(b.get("key", ""))
    if t == "rotating":
        first = next((m for m in str(b.get("messages", "")).splitlines() if m.strip()), "")
        return "🔁 " + (first[:24] or "Rotating messages")
    tpl = str(b.get("text", ""))
    shown = C._PH.sub(lambda m: token_label(m.group(1)), tpl).replace("\n", " ⏎ ")
    return (shown[:30] + "…") if len(shown) > 31 else (shown if shown.strip() else "(space)")


def placeholder(b: dict) -> str:
    """What an empty block shows in the editor (greyed out)."""
    t = b["type"]
    if t == "speech":
        return "💬 your speech appears here"
    if t == "value":
        return token_label(b.get("key", ""))
    if t == "rotating":
        first = next((m for m in str(b.get("messages", "")).splitlines() if m.strip()), "")
        return C._PH.sub(lambda m: token_label(m.group(1)), first) or "🔁 (no messages)"
    tpl = str(b.get("text", "")).replace("\\n", "\n").replace("[", "").replace("]", "")
    return C._PH.sub(lambda m: token_label(m.group(1)), tpl) or "(empty text)"


# --------------------------------------------------------------------------- #
# The block list (left)
# --------------------------------------------------------------------------- #
class BlockPalette(QWidget):
    """Everything you can put into the chatbox, by category, with what it shows now.
    Drag a block into the chatbox, or into a text block's text box. Enter / double-click
    adds it at the end. Dropping a chatbox block here removes it from the chatbox."""
    add_new = Signal(dict)          # {"kind", "over"}
    remove = Signal(str)            # chatbox block dragged onto the list

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.search = QLineEdit(placeholderText="Search blocks: gpu, battery, song, time…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search blocks")
        lay.addWidget(self.search)
        self.only = QCheckBox("Only blocks with a value right now")
        lay.addWidget(self.only)
        self.tree = _PaletteTree(self)
        lay.addWidget(self.tree, 1)
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.only.toggled.connect(lambda _v: self.refresh())
        self.tree.itemActivated.connect(self._activate)
        self.values_provider = lambda: {}
        self._rows = []
        self._groups = {}
        self._build()

    def _build(self):
        t = self.tree
        t.clear()
        self._rows, self._groups = [], {}

        def group(name, open_=False):
            g = QTreeWidgetItem([name, ""])
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            g.setFlags(Qt.ItemIsEnabled)
            t.addTopLevelItem(g)
            g.setExpanded(open_)
            self._groups[name] = g
            return g

        def row(g, label, data, tip="", key=None):
            it = QTreeWidgetItem([label, ""])
            it.setData(0, Qt.UserRole, data)
            it.setToolTip(0, tip or "Drag into your chatbox, or press Enter to add it at the end")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            g.addChild(it)
            self._rows.append((it, key))
            return it

        g = group("Speech", True)
        row(g, "💬 Speech / translation", {"kind": "speech"},
            "Your transcribed and translated sentences")
        g = group("Text & layout", True)
        row(g, "↵ New line", {"kind": "newline"}, "Starts a new line in the chatbox")
        for label, text, tip in B.TEXT_STARTERS:
            row(g, f"“{label}”", {"kind": "text", "over": {"text": text}}, tip)
        row(g, "🔁 Rotating messages", {"kind": "rotating"}, "Messages that take turns")
        for key, desc, cat in B.CATALOG:
            g = self._groups.get(cat) or group(cat)
            row(g, desc, {"kind": "value", "over": {"key": key}}, "{" + key + "}  -  drag into "
                "the chatbox, or onto a text block to put it inside", key)
        self.refresh()

    def refresh(self):
        vals = self.values_provider() or {}
        q = self.search.text().strip().lower()
        try:
            for it, key in self._rows:
                v = str(vals.get(key, "") or "") if key else ""
                it.setText(1, v.replace("\n", " ")[:40] if key else "")
                if key:
                    it.setToolTip(1, v or "nothing right now")
                label = it.text(0).lower()
                hit = not q or q in label or (key and q in key) or \
                    q in it.parent().text(0).lower() or (v and q in v.lower())
                it.setHidden(not hit or (self.only.isChecked() and key is not None and not v))
            for name, g in self._groups.items():
                vis = [g.child(i) for i in range(g.childCount()) if not g.child(i).isHidden()]
                g.setHidden(not vis)
                if q and vis:
                    g.setExpanded(True)
                n = sum(1 for c in vis if c.text(1))
                keyed = sum(1 for it, k in self._rows if k and it.parent() is g)
                g.setText(1, f"{n}/{keyed} now" if keyed else "")
                g.setToolTip(1, f"{n} of {keyed} have a value right now" if keyed else "")
        except RuntimeError:
            pass

    def _activate(self, it, _col=0):
        data = it.data(0, Qt.UserRole)
        if data:
            self.add_new.emit(data)
        else:
            it.setExpanded(not it.isExpanded())


class _PaletteTree(QTreeWidget):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setColumnCount(2)
        self.setHeaderLabels(["Block", "Now"])
        self.setAccessibleName("Blocks you can add. Enter adds the selected block at the end.")
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setUniformRowHeights(True)
        self.setIndentation(14)
        hdr = self.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.resizeSection(1, 105)
        self.setTextElideMode(Qt.ElideRight)

    def startDrag(self, _actions):
        it = self.currentItem()
        data = it.data(0, Qt.UserRole) if it else None
        if not data:
            return
        md = QMimeData()
        md.setData(MIME_NEW, json.dumps(data).encode())
        over = data.get("over") or {}
        if data.get("kind") == "value":
            md.setText("{" + over["key"] + "}")       # so it can be dropped into a text box
        elif data.get("kind") == "text":
            md.setText(over.get("text", ""))
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(_drag_pixmap(it.text(0)))
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_MOVE):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasFormat(MIME_MOVE):
            self.owner.remove.emit(bytes(e.mimeData().data(MIME_MOVE)).decode())
            e.acceptProposedAction()


# --------------------------------------------------------------------------- #
# The chatbox you build (VRChat-style)
# --------------------------------------------------------------------------- #
class ChatboxCanvas(QWidget):
    """Draws the chatbox like VRChat (dark box, white Noto Sans, centered) out of blocks.
    Click a block to change it, drag to move it, drop new blocks in. A value dropped on the
    middle of a text block goes inside that text block."""
    selected = Signal(str)
    insert_new = Signal(dict, int)       # data, index in cfg["blocks"]
    move_to = Signal(str, int)           # block id, index in cfg["blocks"]
    embed = Signal(str, str)             # text block id, value key
    remove = Signal(str)
    move_by = Signal(str, int)

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setMinimumHeight(200)
        self.setAccessibleName("Your chatbox. Arrow keys pick a block, Alt+arrows move it, "
                               "Delete removes it.")
        self.parts: list = []          # [(block, text, state)]
        self.ranges: list = []         # [(start, end, block id)]
        self.sel_id: Optional[str] = None
        self.hover_id: Optional[str] = None
        self.embed_id: Optional[str] = None
        self.show_empty = True
        self._press = None
        self._drop_pos: Optional[int] = None
        self.doc = None
        self._doc_origin = (0.0, 0.0)
        self._bubble = None
        self.line_count = 0

    def set_parts(self, parts):
        self.parts = parts
        self.update()

    def select(self, bid, emit=True):
        self.sel_id = bid
        self.update()
        if emit and bid:
            self.selected.emit(bid)

    def _unit(self):
        return max(0.9, min(1.3, (self.width() - 60) / VRC_BOX_W)) * \
            float(getattr(self.s, "ui_scale", 1.0) or 1.0) ** 0.5

    def _font_px(self):
        return VRC_FONT * self._unit()

    # ---- layout
    def _build_doc(self):
        from PySide6.QtGui import QTextBlockFormat, QTextCursor, QTextDocument, QTextOption
        pal = theme.palette(getattr(self.s, "ui_theme", "dark"))
        doc = QTextDocument()
        doc.setDefaultFont(chatbox_font(self._font_px()))
        doc.setDocumentMargin(0)
        opt = QTextOption(Qt.AlignHCenter)
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(opt)
        doc.setTextWidth((VRC_BOX_W - 2 * VRC_MARGIN) * self._unit())
        cur = QTextCursor(doc)
        bf = QTextBlockFormat()
        bf.setAlignment(Qt.AlignHCenter)
        cur.setBlockFormat(bf)
        plain = QTextCharFormat()
        plain.setForeground(QColor("#ffffff"))
        accent = QColor(pal["accent"])
        self.ranges = []
        prev = None                     # previous block on this line (for the space rule)
        line_has = False
        any_ = False
        for b, text, state in self.parts:
            real = state == "shown" and bool(text)
            if b["type"] == "newline":
                if self.show_empty or b["id"] == self.sel_id:
                    fmt = self._fmt(b, False, accent)
                    if line_has:
                        cur.insertText(" ", plain)
                    start = cur.position()
                    cur.insertText("↵", fmt)
                    self.ranges.append((start, cur.position(), b["id"]))
                if line_has or self.show_empty:
                    cur.insertText("\n", plain)
                prev, line_has = None, False
                continue
            if not real and not self.show_empty and b["id"] != self.sel_id:
                continue
            shown = text if real or state == "dropped" else placeholder(b)
            if not shown:
                continue
            if prev is not None and b["type"] != "text" and prev["type"] != "text":
                cur.insertText(" ", plain)          # same rule as in VRChat: values get a space
            elif prev is not None and self.show_empty:
                cur.insertText("\u2009", plain)     # edit view: a hair of room between blocks
            start = cur.position()
            cur.insertText(mono_emoji(shown), self._fmt(b, real, accent, state))
            self.ranges.append((start, cur.position(), b["id"]))
            prev, line_has, any_ = b, True, True
        if not any_ and not self.ranges:
            f = QTextCharFormat()
            f.setForeground(QColor(255, 255, 255, 130))
            f.setFontItalic(True)
            cur.insertText("Empty chatbox - drag blocks here from the list", f)
        self.doc = doc

    def _fmt(self, b, real, accent, state=""):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#ffffff") if real else QColor(255, 255, 255, 125))
        if not real:
            fmt.setFontItalic(True)
        if state == "dropped":
            fmt.setFontStrikeOut(True)
        if b["id"] == self.embed_id:
            bg = QColor(accent)
            bg.setAlpha(170)
            fmt.setBackground(bg)
        elif b["id"] == self.sel_id:
            bg = QColor(accent)
            bg.setAlpha(110)
            fmt.setBackground(bg)
        elif b["id"] == self.hover_id:
            fmt.setBackground(QColor(255, 255, 255, 40))
        elif self.show_empty:
            fmt.setBackground(QColor(255, 255, 255, 16 if real else 10))
        return fmt

    def _lines(self):
        n, block, cut = 0, self.doc.begin(), None
        top_of = self.doc.documentLayout().blockBoundingRect
        while block.isValid():
            lay = block.layout()
            for i in range(lay.lineCount()):
                n += 1
                if n == VRC_LINES + 1 and cut is None:
                    cut = top_of(block).top() + lay.lineAt(i).y()
            block = block.next()
        return n, cut

    def paintEvent(self, _e):
        from PySide6.QtGui import QLinearGradient
        self._build_doc()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#3b4a63"))
        g.setColorAt(1, QColor("#20252f"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 10, 10)
        doc = self.doc
        ideal = min(doc.idealWidth(), doc.textWidth())
        fpx = self._font_px()
        pad = VRC_MARGIN * self._unit()
        self.line_count, cut_y = self._lines()
        text_h = cut_y if (cut_y is not None and not self.show_empty) else doc.size().height()
        bw, bh = ideal + 2 * pad, text_h + 2 * pad
        bx, by = (r.width() - bw) / 2, max(12.0, (r.height() - bh) / 2 - 10)
        self._bubble = QRectF(bx, by, bw, bh)
        want = int(bh + 36)
        if want > self.minimumHeight():
            QTimer.singleShot(0, self, lambda h=want: self.setMinimumHeight(h))
        p.setBrush(QColor(12, 12, 14, 205))
        p.drawRoundedRect(self._bubble, fpx * 0.55, fpx * 0.55)
        ox = bx + pad - (doc.textWidth() - ideal) / 2
        oy = by + pad
        self._doc_origin = (ox, oy)
        p.save()
        p.translate(ox, oy)
        if cut_y is not None and not self.show_empty:
            doc.drawContents(p, QRectF(-ox, 0, self.width() + ox, cut_y))
        else:
            doc.drawContents(p)
        p.restore()
        if self._drop_pos is not None:
            rect = self._caret_rect(self._drop_pos)
            if rect is not None:
                pal = theme.palette(getattr(self.s, "ui_theme", "dark"))
                p.setPen(QPen(QColor(pal["accent"]), 3))
                p.drawLine(QPointF(rect[0] + ox, rect[1] + oy),
                           QPointF(rect[0] + ox, rect[1] + rect[2] + oy))
        p.end()

    def _caret_rect(self, pos):
        if self.doc is None:
            return None
        block = self.doc.findBlock(pos)
        if not block.isValid():
            block = self.doc.lastBlock()
            pos = block.position() + block.length() - 1
        lay = block.layout()
        rel = pos - block.position()
        line = lay.lineForTextPosition(rel)
        if not line.isValid():
            return None
        top = self.doc.documentLayout().blockBoundingRect(block).top()
        x, _ = line.cursorToX(rel)
        return (lay.position().x() + x, top + line.y(), line.height())

    def _pos_at(self, pt):
        if self.doc is None:
            return None
        ox, oy = self._doc_origin
        return self.doc.documentLayout().hitTest(QPointF(pt.x() - ox, pt.y() - oy), Qt.FuzzyHit)

    def _range_at(self, pt):
        if self._bubble is None or not self._bubble.adjusted(-6, -6, 6, 6).contains(pt):
            return None
        pos = self._pos_at(pt)
        if pos is None or pos < 0:
            return None
        for rng in self.ranges:
            if rng[0] <= pos < rng[1]:
                return rng
        return min(self.ranges, key=lambda r: min(abs(pos - r[0]), abs(pos - r[1])),
                   default=None)

    def _id_at(self, pt):
        rng = self._range_at(pt)
        return rng[2] if rng else None

    def _drop_target(self, pt, md):
        """('embed', text block id) or ('insert', index in cfg blocks, caret position)."""
        all_ids = [b["id"] for b, _t, _s in self.parts]
        if not self.ranges:
            return ("insert", len(all_ids), 0)
        pos = self._pos_at(pt)
        if pos is None or pos < 0 or not (self._bubble and
                                          self._bubble.adjusted(-30, -30, 30, 30).contains(pt)):
            return ("insert", len(all_ids), self.ranges[-1][1])
        is_value = False
        if md.hasFormat(MIME_NEW):
            data = json.loads(bytes(md.data(MIME_NEW)).decode())
            is_value = data.get("kind") == "value"
        for start, end, bid in self.ranges:
            if pos < end:
                b = next((b for b, _t, _s in self.parts if b["id"] == bid), None)
                span = max(1, end - start)
                inside = start + span * 0.2 < pos < end - span * 0.2
                if is_value and b is not None and b["type"] == "text" and inside:
                    return ("embed", bid)
                if pos <= (start + end) / 2:
                    return ("insert", all_ids.index(bid), start)
                return ("insert", all_ids.index(bid) + 1, end)
        return ("insert", len(all_ids), self.ranges[-1][1])

    # ---- mouse
    def mousePressEvent(self, e):
        self.setFocus()
        bid = self._id_at(e.position())
        self._press = (e.position(), bid)
        if bid:
            self.select(bid)

    def mouseMoveEvent(self, e):
        if self._press and self._press[1] and e.buttons() & Qt.LeftButton:
            if (e.position() - self._press[0]).manhattanLength() > 8:
                bid = self._press[1]
                self._press = None
                self._start_drag(bid)
                return
        bid = self._id_at(e.position())
        if bid != self.hover_id:
            self.hover_id = bid
            self.setCursor(Qt.PointingHandCursor if bid else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, _e):
        self._press = None

    def leaveEvent(self, _e):
        self.hover_id = None
        self.update()

    def contextMenuEvent(self, e):
        bid = self._id_at(e.pos())
        if not bid:
            return
        self.select(bid)
        m = QMenu(self)
        m.addAction("Move earlier", lambda: self.move_by.emit(bid, -1))
        m.addAction("Move later", lambda: self.move_by.emit(bid, 1))
        m.addAction("Remove from chatbox", lambda: self.remove.emit(bid))
        m.exec(e.globalPos())

    def _start_drag(self, bid):
        drag = QDrag(self)
        md = QMimeData()
        md.setData(MIME_MOVE, bid.encode())
        drag.setMimeData(md)
        b = next((b for b, _t, _s in self.parts if b["id"] == bid), None)
        if b is not None:
            drag.setPixmap(_drag_pixmap(block_name(b)))
        drag.exec(Qt.MoveAction)

    # ---- drops
    def _accepts(self, md):
        return md.hasFormat(MIME_NEW) or md.hasFormat(MIME_MOVE)

    def dragEnterEvent(self, e):
        if self._accepts(e.mimeData()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if not self._accepts(e.mimeData()):
            return
        tgt = self._drop_target(e.position(), e.mimeData())
        if tgt[0] == "embed":
            self.embed_id, self._drop_pos = tgt[1], None
        else:
            self.embed_id, self._drop_pos = None, tgt[2]
        self.update()
        e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._drop_pos = self.embed_id = None
        self.update()

    def dropEvent(self, e):
        md = e.mimeData()
        tgt = self._drop_target(e.position(), md)
        self._drop_pos = self.embed_id = None
        if tgt[0] == "embed":
            data = json.loads(bytes(md.data(MIME_NEW)).decode())
            self.embed.emit(tgt[1], data["over"]["key"])
        elif md.hasFormat(MIME_MOVE):
            self.move_to.emit(bytes(md.data(MIME_MOVE)).decode(), tgt[1])
        elif md.hasFormat(MIME_NEW):
            self.insert_new.emit(json.loads(bytes(md.data(MIME_NEW)).decode()), tgt[1])
        e.acceptProposedAction()
        self.update()

    # ---- keyboard
    def keyPressEvent(self, e):
        ids = [r[2] for r in self.ranges]
        if not ids:
            return super().keyPressEvent(e)
        k = e.key()
        cur = ids.index(self.sel_id) if self.sel_id in ids else -1
        alt = bool(e.modifiers() & Qt.AltModifier)
        if k in (Qt.Key_Left, Qt.Key_Up, Qt.Key_Right, Qt.Key_Down):
            d = -1 if k in (Qt.Key_Left, Qt.Key_Up) else 1
            if alt and self.sel_id:
                self.move_by.emit(self.sel_id, d)
            else:
                self.select(ids[max(0, min(len(ids) - 1, cur + d))] if cur >= 0 else ids[0])
        elif k in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel_id:
            self.remove.emit(self.sel_id)
        else:
            super().keyPressEvent(e)


class PreviewPanel(QFrame):
    clear_clicked = Signal()
    undo_clicked = Signal()

    def __init__(self, settings):
        super().__init__(objectName="panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel("YOUR CHATBOX", objectName="section")
        title.setStyleSheet("padding-top: 0;")
        top.addWidget(title)
        top.addStretch()
        self.count = QLabel(objectName="hint")
        top.addWidget(self.count)
        self.empty_cb = QCheckBox("Edit view", checked=True)
        self.empty_cb.setToolTip("On: also shows blocks that are empty right now (greyed out) "
                                 "and the line breaks, so you can click and move them.\nOff: "
                                 "exactly what VRChat shows.")
        top.addWidget(self.empty_cb)
        self.undo = QPushButton("↶ Undo")
        self.undo.setToolTip("Undo the last change to the chatbox (Ctrl+Z)")
        self.undo.clicked.connect(self.undo_clicked)
        top.addWidget(self.undo)
        clear = QPushButton("Clear chatbox")
        clear.setToolTip("Removes the current message in VRChat right away")
        clear.clicked.connect(self.clear_clicked)
        top.addWidget(clear)
        lay.addLayout(top)
        self.canvas = ChatboxCanvas(settings)
        self.empty_cb.toggled.connect(self._empty)
        lay.addWidget(self.canvas, 1)
        self.status = QLabel(objectName="hint", wordWrap=True)
        lay.addWidget(self.status)
        self.set_status("", None)

    def _empty(self, on):
        self.canvas.show_empty = on
        self.canvas.update()

    def set_status(self, text: str, sent):
        n = C.ulen(text)
        self.count.setText(f"{n}/{C.LIMIT}" + (f" · ⚠ more than {VRC_LINES} lines"
                                                if self.canvas.line_count > VRC_LINES
                                                and not self.canvas.show_empty else ""))
        tip = ("Drag blocks in from the list. Click one to change it, drag it to move it, drag "
               "it back to the list (or press Delete) to remove it. ")
        if sent is None:
            self.status.setText(tip + "Sending starts when the app is running.")
        elif sent:
            self.status.setText(tip + "✓ Sent to VRChat")
        else:
            self.status.setText(tip + "Waiting - VRChat takes about one update per second")

    def set_sending_off(self):
        self.status.setText("Sending is off (main window, left: VRChat → Send to chatbox)")


# --------------------------------------------------------------------------- #
# Quick privacy bar
# --------------------------------------------------------------------------- #
class QuickBar(QWidget):
    changed = Signal()

    def __init__(self, cfg_getter):
        super().__init__()
        self.cfg = cfg_getter
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.rebuild()

    def rebuild(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.lay.addWidget(QLabel("Chatbox shows:", objectName="hint"))
        cfg = self.cfg()
        hide_all = bool(cfg.get("hide_extras"))
        present = set()
        for b in cfg["blocks"]:
            if b["type"] == "speech" and not b.get("enabled", True):
                continue
            present |= B.groups_of(b)
        hidden = set(cfg.get("hidden_groups", []))
        for gid, label in B.PRIVACY_GROUPS:
            if gid not in present:
                continue
            b = QPushButton(label, objectName="chip", checkable=True)
            b.setChecked(gid not in hidden)
            b.setEnabled(not hide_all or gid == "speech")
            b.setToolTip("Click to hide or show all of these in VRChat right now")
            b.toggled.connect(lambda on, g=gid: self._toggle(g, on))
            self.lay.addWidget(b)
        self.lay.addStretch()
        hide = QPushButton("🙈 Hide extras", objectName="chipwarn", checkable=True)
        hide.setChecked(hide_all)
        hide.setToolTip("Privacy: show only speech, hide everything else at once")
        hide.toggled.connect(self._hide_all)
        self.lay.addWidget(hide)

    def _toggle(self, gid, on):
        hidden = set(self.cfg().get("hidden_groups", []))
        (hidden.discard if on else hidden.add)(gid)
        self.cfg()["hidden_groups"] = sorted(hidden)
        self.changed.emit()

    def _hide_all(self, on):
        self.cfg()["hide_extras"] = on
        self.changed.emit()
        QTimer.singleShot(0, self.rebuild)


# --------------------------------------------------------------------------- #
# Block settings
# --------------------------------------------------------------------------- #
class TokenHighlighter(QSyntaxHighlighter):
    """Colours {values} and [optional parts] in a text block's text box."""

    def __init__(self, doc, accent):
        super().__init__(doc)
        self.f_val = QTextCharFormat()
        self.f_val.setForeground(QColor(accent))
        self.f_val.setFontWeight(QFont.Bold)
        self.f_opt = QTextCharFormat()
        self.f_opt.setForeground(QColor("#9aa2ad"))

    def highlightBlock(self, text):
        for m in re.finditer(r"[\[\]]", text):
            self.setFormat(m.start(), 1, self.f_opt)
        for m in C._PH.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self.f_val)


class BlockEditor(QScrollArea):
    changed = Signal()
    open_filters = Signal()
    sample = Signal(bool)
    send_sample = Signal()
    move = Signal(int)
    remove = Signal()

    def __init__(self, settings):
        super().__init__(widgetResizable=True)
        self.s = settings
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.el = None
        self.values_provider = lambda: {}
        self._sample_btn = None
        self.show_element(None)

    @property
    def cfg(self):
        return self.s.chatbox

    def show_element(self, el):
        self.el = el
        self._sample_btn = None
        self.sample.emit(False)
        w = QWidget(objectName="inner")
        self.form = QFormLayout(w)
        self.form.setContentsMargins(14, 10, 14, 14)
        self.form.setVerticalSpacing(10)
        if el is None:
            self.form.addRow(hint("Click a block in your chatbox to change it, or drag one in "
                                  "from the list on the left."))
        else:
            head = QLabel(block_name(el), objectName="title")
            head.setStyleSheet("font-size: 15px;")
            hrow = QHBoxLayout()
            hrow.addWidget(head, 1)
            for text, tip, d in (("◀", "Move earlier (Alt+←)", -1), ("▶", "Move later (Alt+→)", 1)):
                b = QPushButton(text)
                b.setFixedWidth(34)
                b.setToolTip(tip)
                b.setAccessibleName(tip)
                b.clicked.connect(lambda _c=False, d=d: self.move.emit(d))
                hrow.addWidget(b)
            rm = QPushButton("Remove")
            rm.setToolTip("Takes it out of the chatbox (Delete). Undo brings it back.")
            rm.clicked.connect(self.remove.emit)
            hrow.addWidget(rm)
            self.form.addRow(hrow)
            t = el["type"]
            if t != "newline":
                self._priority(el)
            getattr(self, f"_build_{t}")(el)
            if t in ("value", "text", "rotating"):
                self._options(el)
                self._build_display(el)
        self.setWidget(w)

    # ---- helpers
    def _set(self, key, value):
        self.el[key] = value
        self.changed.emit()

    def _setopt(self, key, value):
        self.el.setdefault("opts", {})[key] = value
        self.changed.emit()

    def _setcfg(self, path, value):
        d = self.cfg
        for p in path[:-1]:
            d = d.setdefault(p, {})
        d[path[-1]] = value
        self.changed.emit()

    def _spin(self, key, lo, hi, step=1.0, decimals=0, suffix="", special=None, src=None,
              setter=None):
        src = self.el if src is None else src
        if decimals:
            sp = QDoubleSpinBox(decimals=decimals, minimum=lo, maximum=hi, singleStep=step)
            sp.setValue(float(src.get(key) or 0))
        else:
            sp = QSpinBox(minimum=int(lo), maximum=int(hi), singleStep=int(step))
            sp.setValue(int(src.get(key) or 0))
        if suffix:
            sp.setSuffix(suffix)
        if special:
            sp.setSpecialValueText(special)
        sp.valueChanged.connect(setter or (lambda v: self._set(key, v)))
        return sp

    def _check(self, key, text):
        cb = QCheckBox(text, checked=bool(self.el.get(key)))
        cb.toggled.connect(lambda v: self._set(key, bool(v)))
        return cb

    def _combo(self, key, options, current=None, setter=None):
        cb = QComboBox()
        for value, label in options:
            cb.addItem(label, value)
        i = cb.findData(self.el.get(key) if current is None else current)
        cb.setCurrentIndex(max(0, i))
        cb.currentIndexChanged.connect(
            lambda _i: (setter or (lambda v: self._set(key, v)))(cb.currentData()))
        return cb

    def _insert_menu(self, put) -> QPushButton:
        btn = QPushButton("Insert value ▾")
        btn.setToolTip("Adds a value at the cursor. You can also drag one in from the list.")
        menu = QMenu(btn)
        subs = {}
        for key, desc, cat in B.CATALOG:
            if cat not in subs:
                subs[cat] = menu.addMenu(cat)
            subs[cat].addAction(f"{desc}    {{{key}}}", lambda k=key: put("{" + k + "}"))
        btn.setMenu(menu)
        return btn

    def _priority(self, el):
        pr = self._spin("prio", 0, 20)
        pr.setToolTip("Lower number = more important. When the chatbox is full, the highest "
                      "number makes room first; blocks with the same number make room "
                      "together. 0 = never removed.")
        self.form.addRow("Priority", pr)
        sp = next((b for b in self.cfg["blocks"] if b["type"] == "speech"), None)
        if el["type"] == "speech":
            self.form.addRow(hint("Blocks with a higher number than speech make room while you "
                                  "talk (highest number first, same numbers together). Blocks "
                                  "with the same or a lower number stay, and your sentence "
                                  "pages through the space that's left."))
        else:
            sp_p = int(sp.get("prio", 1)) if sp else 1
            self.form.addRow(hint(f"Lower = more important. Speech is {sp_p}: blocks with a "
                                  "higher number make room while you talk, the highest first, "
                                  "same numbers together. 0 = never removed."))

    # ---- per type
    def _build_newline(self, el):
        self.form.addRow(hint("Starts a new line in the chatbox. A line where every block is "
                              "empty disappears by itself."))

    def _build_speech(self, el):
        f = self.form
        f.addRow(hint("Your transcribed and translated sentences."))
        width = self._spin("width", 20, 144)
        width.setSuffix(" characters")
        width.valueChanged.connect(lambda _v: self.sample.emit(True))
        f.addRow("Characters shown at once", width)
        row = QHBoxLayout()
        show = QPushButton("👁 Show sample", checkable=True)
        show.setToolTip("Fills the preview with sample text of exactly this length")
        show.toggled.connect(self.sample.emit)
        self._sample_btn = show
        send = QPushButton("Send sample to VRChat")
        send.setToolTip("Shows the sample in your real chatbox, to see how big it is in game")
        send.clicked.connect(self.send_sample.emit)
        row.addWidget(show)
        row.addWidget(send)
        row.addStretch()
        f.addRow("", row)
        mode = self._combo("mode", [("pages", "Pages (show part by part)"),
                                    ("scroll", "Scroll (move along word by word)"),
                                    ("marquee", "Marquee (slide along letter by letter)"),
                                    ("typewriter", "Typewriter (words appear one by one)")])
        f.addRow("Longer text", mode)
        page = self._spin("page_seconds", 0, 20, 0.5, 1, " s", "Auto (by length)")
        scroll_w = self._spin("scroll_words", 1, 10, 1, 0, " words")
        scroll_s = self._spin("scroll_seconds", 1.0, 10, 0.2, 1, " s")
        f.addRow("Time per page", page)
        f.addRow("Scroll by", scroll_w)
        f.addRow("Time per scroll step", scroll_s)
        f.addRow("Keep visible afterwards", self._spin("hold_seconds", 0, 60, 1, 1, " s"))
        f.addRow("When you keep talking", self._combo("flow", [
            ("live", "Live: jump to what I'm saying now"),
            ("queue", "Queue: show everything in order")]))
        f.addRow(hint("Live keeps the chatbox in step with your voice: a new sentence replaces "
                      "the old one as soon as the old line has been shown once. Queue never "
                      "skips anything, but falls behind when you talk a lot."))
        f.addRow("Max characters per sentence", self._spin("limit", 0, 2000, 10, 0, "",
                                                          "No limit"))

        def sync():
            scroll = el.get("mode") in ("scroll", "marquee", "typewriter")
            scroll_w.setEnabled(scroll)
            scroll_s.setEnabled(scroll)
            page.setEnabled(not scroll)
        mode.currentIndexChanged.connect(lambda _i: sync())
        sync()

    def _build_value(self, el):
        f = self.form
        key = el.get("key", "")
        desc, cat = B.KEY_INFO.get(key, (key, ""))
        f.addRow(hint(f"{cat}: {desc}. Shows nothing while it has no value."))
        self._now = QLabel(objectName="hint")
        f.addRow("Shows now", self._now)
        self._tick_now(lambda v: str(v.get(key, "") or "—  nothing right now"))
        f.addRow(hint("Want text around it, like “CPU: … %”? Drag a “Text” block in and drop "
                      "this value onto it - the text then only shows while the value has one."))

    def _tick_now(self, fn):
        lbl = self._now

        def upd():
            try:
                base = self.values_provider() or {}
                lbl.setText(fn(base))
            except RuntimeError:
                pass
        t = QTimer(lbl, interval=1500, timeout=upd)
        t.start()
        upd()

    def _build_text(self, el):
        f = self.form
        f.addRow(hint("Your own text. Drag values from the list into the box below (or use "
                      "Insert value). While a value in it is empty, the whole text block is "
                      "hidden - so “CPU: {cpu}%” never shows as “CPU: %”. A part in [ ] "
                      "disappears on its own instead. New lines work."))
        te = QPlainTextEdit(str(el.get("text", "")))
        te.setFixedHeight(96)
        te.setAccessibleName("Text")
        pal = theme.palette(getattr(self.s, "ui_theme", "dark"))
        te._hl = TokenHighlighter(te.document(), pal["accent"])
        te.textChanged.connect(lambda: self._set("text", te.toPlainText()))
        f.addRow(te)

        def put(piece):
            cur = te.textCursor()
            cur.insertText(piece)
            te.setTextCursor(cur)
            te.setFocus()
        row = QHBoxLayout()
        row.addWidget(self._insert_menu(put))
        row.addStretch()
        f.addRow(row)
        self._now = QLabel(objectName="hint", wordWrap=True)
        f.addRow("Shows now", self._now)
        self._tick_now(lambda v: self._preview_of(el) or "—  hidden right now (a value is "
                                                         "empty)")

    def _preview_of(self, el):
        comp = getattr(self, "composer", None)
        if comp is None:
            return ""
        base = comp.last_values or {}
        v = comp.block_values(el, base)
        if el["type"] == "rotating":
            return ""
        tpl = str(el.get("text", ""))
        return comp.fill_block(tpl, v) if C._PH.search(tpl) else tpl

    def _build_rotating(self, el):
        f = self.form
        f.addRow(hint("One message per line. They take turns. Values work here too - a message "
                      "with an empty value stays empty for its turn."))
        te = QPlainTextEdit(str(el.get("messages", "")))
        te.setFixedHeight(110)
        pal = theme.palette(getattr(self.s, "ui_theme", "dark"))
        te._hl = TokenHighlighter(te.document(), pal["accent"])
        te.textChanged.connect(lambda: self._set("messages", te.toPlainText()))
        f.addRow(te)

        def put(piece):
            cur = te.textCursor()
            cur.insertText(piece)
            te.setTextCursor(cur)
            te.setFocus()
        f.addRow(self._insert_menu(put))
        f.addRow("Change every", self._spin("interval", 2, 600, 1, 0, " s"))
        f.addRow(self._check("random", "Random order"))

    # ---- options that depend on the values in a block
    def _options(self, el):
        sets = B.option_sets(el)
        keys = set(B.keys_of(el))
        f = self.form
        o = el.setdefault("opts", dict(B.OPT_DEFAULTS))
        if "time" in sets:
            f.addRow(QLabel("CLOCK", objectName="section"))
            tz = QComboBox(editable=True)
            tz.addItems(TIMEZONES)
            tz.setCurrentText(o.get("timezone", "local"))
            tz.setToolTip("'local' is your PC's time. Any zone like Asia/Tokyo works.")
            tz_hint = hint("")

            def tz_changed(v):
                v = v.strip() or "local"
                ok = True
                if v != "local":
                    try:
                        from zoneinfo import ZoneInfo
                        ZoneInfo(v)
                    except Exception:
                        ok = False
                tz_hint.setText("" if ok else "⚠ Unknown time zone - using local time.")
                self._setopt("timezone", v)
            tz.currentTextChanged.connect(tz_changed)
            f.addRow("Time zone", tz)
            f.addRow(tz_hint)
            f.addRow("Style", self._combo("style", [
                ("24h", "24-hour (14:05)"), ("24h_sec", "24-hour with seconds"),
                ("12h", "12-hour (2:05 PM)"), ("12h_sec", "12-hour with seconds")],
                current=o.get("style", "24h"), setter=lambda v: self._setopt("style", v)))
            f.addRow(hint("Tip: drag in another clock block for a friend's time zone."))
        if "countdown" in sets:
            f.addRow(QLabel("COUNTDOWN", objectName="section"))
            name = QLineEdit(str(o.get("label", "")), placeholderText="e.g. My birthday")
            name.textChanged.connect(lambda v: self._setopt("label", v))
            f.addRow("Name", name)
            dt = QDateTimeEdit(calendarPopup=True)
            dt.setDisplayFormat("yyyy-MM-dd HH:mm")
            q = QDateTime.fromString(str(o.get("target", "")), "yyyy-MM-dd HH:mm")
            dt.setDateTime(q if q.isValid() else QDateTime.currentDateTime().addDays(7))
            dt.dateTimeChanged.connect(
                lambda v: self._setopt("target", v.toString("yyyy-MM-dd HH:mm")))
            f.addRow("Counts down to", dt)
        music = keys & {"title", "artist", "album", "player", "site", "position", "duration",
                        "bar", "status", "audio_app"}
        if music:
            f.addRow(QLabel("NOW PLAYING", objectName="section"))
            if "title" in keys:
                f.addRow("Shorten titles to", self._spin(
                    "max_title", 0, 120, 5, 0, " chars", "No limit", src=o,
                    setter=lambda v: self._setopt("max_title", v)))
            sp = QCheckBox("Also show paused media (with ⏸)",
                           checked=bool(self.cfg.get("show_paused")))
            sp.toggled.connect(lambda v: self._setcfg(["show_paused"], bool(v)))
            f.addRow(sp)
            btn = QPushButton("Filters and detected players…", objectName="primary")
            btn.clicked.connect(self.open_filters.emit)
            f.addRow(btn)
            f.addRow(hint("Filters are shared by all music blocks. {audio_app} shows the "
                          "program while sound plays without song info (e.g. a soundboard)."))
        if keys & {"lyric_line", "lyric_next"}:
            ly = self.cfg.setdefault("lyrics", {})
            f.addRow(QLabel("LYRICS", objectName="section"))
            on = QCheckBox("Look up lyrics online (LRCLIB)", checked=bool(ly.get("online")))
            on.toggled.connect(lambda v: self._setcfg(["lyrics", "online"], bool(v)))
            f.addRow(on)
            f.addRow(hint("Only the song title, artist, album and length are sent to lrclib.net. "
                          "Found lyrics are saved, so each song is looked up once."))
            folder = QHBoxLayout()
            le = QLineEdit(str(ly.get("folder", "")),
                           placeholderText="optional: folder with .lrc files")
            le.textChanged.connect(lambda v: self._setcfg(["lyrics", "folder"], v))
            browse = QPushButton("Browse…")

            def pick():
                from PySide6.QtWidgets import QFileDialog
                d = QFileDialog.getExistingDirectory(self, "Folder with .lrc files", le.text())
                if d:
                    le.setText(d)
            browse.clicked.connect(pick)
            folder.addWidget(le, 1)
            folder.addWidget(browse)
            f.addRow("Lyrics folder", folder)
            f.addRow("Timing correction", self._spin(
                "offset", -10, 10, 0.25, 2, " s", src=ly,
                setter=lambda v: self._setcfg(["lyrics", "offset"], v)))
        if "gpu" in sets:
            f.addRow("GPU number", self._spin(
                "gpu_index", 0, 8, src=self.cfg, setter=lambda v: self._setcfg(["gpu_index"], v)))
            f.addRow(hint("Which NVIDIA card GPU values mean (nvidia-smi number; 0 is usually "
                          "your main card)."))
        if "window_title" in keys:
            f.addRow(hint("⚠ The window title can show private things (file names, chats, "
                          "websites)."))

    def _build_display(self, el):
        f = self.form
        f.addRow(QLabel("SCROLLING", objectName="section"))
        mode = self._combo("show_mode", C.DISPLAY_MODES)
        mode.setToolTip("Show long text in a fixed number of characters, so this block always "
                        "takes the same space")
        f.addRow("Show", mode)
        chars = self._spin("win_chars", 5, 144, 1, 0, " characters")
        lines = self._spin("win_lines", 1, 9, 1, 0, " lines")
        step = self._spin("win_step", 1, 20, 1, 0)
        secs = self._spin("win_seconds", 1.0, 30, 0.2, 1, " s")
        f.addRow("Window width", chars)
        f.addRow("Lines at once", lines)
        f.addRow("Move by", step)
        f.addRow("Next step every", secs)
        explain = hint("")
        f.addRow(explain)
        texts = {
            "full": "The whole text, as it is.",
            "scroll": "A ticker: the text moves through the window a few words at a time and "
                      "starts over at the end.",
            "marquee": "Like an LED sign: moves a few letters at a time, around and around.",
            "bounce": "Slides to the end of the text, then back to the start.",
            "lines": "Like film credits: shows a few lines and moves up one line per step.",
            "pages": "Shows one window-sized part after the other.",
            "typewriter": "Words appear a few at a time until the window is full, then the "
                          "next part starts.",
        }

        def sync():
            m = el.get("show_mode", "full")
            unit = C.STEP_UNIT.get(m)
            chars.setEnabled(m != "full")
            secs.setEnabled(m != "full")
            lines.setEnabled(m == "lines")
            step.setEnabled(unit is not None)
            step.setSuffix(f" {unit}" if unit else "")
            explain.setText(texts.get(m, "") + ("" if m == "full" else
                            "  Steps are never faster than “Update at most every” below."))
        mode.currentIndexChanged.connect(lambda _i: sync())
        sync()


# --------------------------------------------------------------------------- #
# Media filter dialog
# --------------------------------------------------------------------------- #
FIELDS = [("any", "Anything"), ("app", "Program"), ("site", "Website"), ("title", "Title / artist")]


class MediaFilterDialog(QDialog):
    def __init__(self, filters: dict, watcher: M.MediaWatcher, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Now playing - filters")
        self.resize(860, 740)
        self.f = copy.deepcopy(filters)
        self.watcher = watcher
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        # mode
        modes = QHBoxLayout()
        self.r_hide = QRadioButton("Show everything, except:")
        self.r_only = QRadioButton("Only show:")
        g = QButtonGroup(self)
        g.addButton(self.r_hide)
        g.addButton(self.r_only)
        (self.r_only if self.f.get("mode") == "only" else self.r_hide).setChecked(True)
        self.r_hide.toggled.connect(self._mode_changed)
        modes.addWidget(self.r_hide)
        modes.addWidget(self.r_only)
        modes.addStretch()
        lay.addLayout(modes)

        self.rules = QTableWidget(0, 3)
        self.rules.setHorizontalHeaderLabels(["On", "Match in", "Text (e.g. youtube, spotify)"])
        self.rules.verticalHeader().hide()
        hdr = self.rules.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        self.rules.setFixedHeight(120)
        for r in self.f.get("rules", []):
            self._add_rule(r)
        self.rules.itemChanged.connect(lambda *_: self._refresh())
        lay.addWidget(self.rules)
        rb = QHBoxLayout()
        add = QPushButton("+ Add rule")
        add.clicked.connect(lambda: self._add_rule({"field": "any", "text": "", "enabled": True},
                                                   edit=True))
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_rule)
        rb.addWidget(add)
        rb.addWidget(rem)
        rb.addStretch()
        lay.addLayout(rb)

        # live list
        live_head = QHBoxLayout()
        live_head.addWidget(QLabel("DETECTED RIGHT NOW", objectName="section"))
        live_head.addStretch()
        self.status = hint("")
        live_head.addWidget(self.status)
        lay.addLayout(live_head)
        self.live = QTableWidget(0, 5)
        self.live.setHorizontalHeaderLabels(["In chatbox", "Program", "Website", "Title", "State"])
        self.live.verticalHeader().hide()
        self.live.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.live.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h2 = self.live.horizontalHeader()
        for i in (0, 1, 2, 4):
            h2.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h2.setSectionResizeMode(3, QHeaderView.Stretch)
        lay.addWidget(self.live, 1)
        qb = QHBoxLayout()
        self.b_app = QPushButton()
        self.b_site = QPushButton()
        self.b_never = QPushButton("Never show as program")
        self.b_never.setToolTip("Adds the program to the list below, so it's never shown when "
                                "it plays sound without song info")
        self.b_app.clicked.connect(lambda: self._quick("app"))
        self.b_site.clicked.connect(lambda: self._quick("site"))
        self.b_never.clicked.connect(self._never)
        for b in (self.b_app, self.b_site, self.b_never):
            qb.addWidget(b)
        qb.addStretch()
        lay.addLayout(qb)

        # fallback ignore list
        lay.addWidget(QLabel("PROGRAMS NEVER SHOWN AS “PLAYING AUDIO”", objectName="section"))
        lay.addWidget(hint("When something plays sound without song info, the program name is "
                           "shown, except for these (one per line). VRChat, Discord and SteamVR "
                           "are in here so your game and voice chat don't show up."))
        self.ignore = QPlainTextEdit("\n".join(self.f.get("fallback_ignore", [])))
        self.ignore.setFixedHeight(80)
        self.ignore.textChanged.connect(self._refresh)
        lay.addWidget(self.ignore)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self._items = []
        self._ready = True
        self._mode_changed()
        self.watcher.hold(True)
        self.timer = QTimer(self, interval=1500, timeout=self._refresh)
        self.timer.start()
        QTimer.singleShot(300, self, self._refresh)

    def done(self, r):
        self.timer.stop()
        self.watcher.hold(False)
        super().done(r)

    # ---- rules
    def _add_rule(self, r, edit=False):
        self.rules.blockSignals(True)
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        on = QTableWidgetItem()
        on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        on.setCheckState(Qt.Checked if r.get("enabled", True) else Qt.Unchecked)
        self.rules.setItem(row, 0, on)
        cb = QComboBox()
        for v, label in FIELDS:
            cb.addItem(label, v)
        cb.setCurrentIndex(max(0, cb.findData(r.get("field", "any"))))
        cb.currentIndexChanged.connect(lambda _i: self._refresh())
        self.rules.setCellWidget(row, 1, cb)
        self.rules.setItem(row, 2, QTableWidgetItem(str(r.get("text", ""))))
        self.rules.blockSignals(False)
        if edit:
            self.rules.setCurrentCell(row, 2)
            self.rules.editItem(self.rules.item(row, 2))
        self._refresh()

    def _remove_rule(self):
        for row in sorted({i.row() for i in self.rules.selectedIndexes()}, reverse=True):
            self.rules.removeRow(row)
        self._refresh()

    def collect(self) -> dict:
        rules = []
        for row in range(self.rules.rowCount()):
            text = (self.rules.item(row, 2).text() if self.rules.item(row, 2) else "").strip()
            if not text:
                continue
            rules.append({"field": self.rules.cellWidget(row, 1).currentData(), "text": text,
                          "enabled": self.rules.item(row, 0).checkState() == Qt.Checked})
        self.f["rules"] = rules
        self.f["mode"] = "only" if self.r_only.isChecked() else "hide"
        self.f["fallback_ignore"] = [x.strip() for x in self.ignore.toPlainText().splitlines()
                                     if x.strip()]
        return self.f

    def _mode_changed(self, *_):
        if not getattr(self, "_ready", False):
            return
        only = self.r_only.isChecked()
        self.b_app.setText("Allow this program" if only else "Hide this program")
        self.b_site.setText("Allow this website" if only else "Hide this website")
        self._refresh()

    def _selected(self):
        rows = {i.row() for i in self.live.selectedIndexes()}
        return self._items[min(rows)] if rows and min(rows) < len(self._items) else None

    def _quick(self, field_):
        it = self._selected()
        if it is None:
            return
        text = it.app if field_ == "app" else it.site
        if not text:
            return
        self._add_rule({"field": field_, "text": text.lower(), "enabled": True})

    def _never(self):
        it = self._selected()
        if it is None:
            return
        cur = self.ignore.toPlainText().rstrip()
        self.ignore.setPlainText((cur + "\n" if cur else "") + it.app)

    # ---- live refresh
    def _refresh(self):
        if not getattr(self, "_ready", False):
            return
        f = self.collect()
        media, audio = self.watcher.snapshot()
        chosen, why = M.choose(media, audio, f, show_paused=False)
        st = self.watcher.status
        names = {"ok": "✓", "off": "…", "unavailable": "✗"}
        self.status.setText(f"Now playing {names.get(st['media'], '?')}   Program audio "
                            f"{names.get(st['audio'], '?')}   Window titles "
                            f"{names.get(st['windows'], '?')}")
        items = media + [a for a in sorted(audio, key=lambda x: -x.level) if a.level >= 0.001
                         or a.playing]
        sel = self._selected()
        sel_key = (sel.kind, sel.app, sel.title) if sel else None
        self._items = items
        self.live.setRowCount(len(items))
        for row, it in enumerate(items):
            if it.kind == "media":
                shown = "✓ shown" if it is chosen else (
                    "hidden" if not M.is_allowed(it, f) else ("paused" if not it.playing
                                                                else "–"))
                state = "Playing" if it.playing else "Paused"
            else:
                shown = "✓ shown" if it is chosen else (
                    "never" if M._ignored_for_fallback(it, f) else "–")
                state = f"Audio {int(it.level * 100)}%"
            vals = [shown, it.app, it.site, it.title + (f" — {it.artist}" if it.artist else ""),
                    state]
            for col, v in enumerate(vals):
                self.live.setItem(row, col, QTableWidgetItem(v))
            if sel_key == (it.kind, it.app, it.title):
                self.live.selectRow(row)
        if not items:
            self.live.setRowCount(1)
            self.live.setItem(0, 3, QTableWidgetItem("Nothing is playing right now. Start a song "
                                                     "or video to see it here."))





# --------------------------------------------------------------------------- #
# The tab
# --------------------------------------------------------------------------- #
class ChatboxTab(QWidget):
    changed = Signal()          # config changed (save + update composer)

    def __init__(self, settings):
        super().__init__()
        self.s = settings
        self.s.chatbox = B.normalize(getattr(settings, "chatbox", None))
        self.composer = None
        self._undo: list = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        outer.addWidget(split)

        left = QFrame(objectName="panel")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 10, 12, 12)
        ll.addWidget(QLabel("BLOCKS", objectName="section"))
        self.palette = BlockPalette()
        self.palette.add_new.connect(lambda d: self._insert_new(d, None))
        self.palette.remove.connect(self._remove_id)
        self.palette.values_provider = self._values
        ll.addWidget(self.palette, 1)
        ll.addWidget(hint("Drag a block into your chatbox. Drop a value onto a text block to "
                          "put it inside. Drag a block back here to remove it."))
        split.addWidget(left)

        right = QWidget()
        lay = QVBoxLayout(right)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.preview = PreviewPanel(settings)
        self.preview.clear_clicked.connect(self._clear)
        self.preview.undo_clicked.connect(self.undo)
        self.canvas = self.preview.canvas
        self.canvas.selected.connect(self._select_id)
        self.canvas.insert_new.connect(self._insert_new)
        self.canvas.move_to.connect(self._move_to)
        self.canvas.embed.connect(self._embed)
        self.canvas.remove.connect(self._remove_id)
        self.canvas.move_by.connect(self._move_by)
        vsplit = QSplitter(Qt.Vertical)
        vsplit.setChildrenCollapsible(False)
        vsplit.addWidget(self.preview)
        ed = QFrame(objectName="panel")
        rl = QVBoxLayout(ed)
        rl.setContentsMargins(0, 0, 0, 0)
        self.editor = BlockEditor(settings)
        self.editor.changed.connect(self._element_changed)
        self.editor.open_filters.connect(self._open_filters)
        self.editor.sample.connect(self._sample)
        self.editor.send_sample.connect(self._send_sample)
        self.editor.move.connect(lambda d: self.editor.el and self._move_by(self.editor.el["id"], d))
        self.editor.remove.connect(lambda: self.editor.el and self._remove_id(self.editor.el["id"]))
        self.editor.values_provider = self._values
        rl.addWidget(self.editor)
        vsplit.addWidget(ed)
        vsplit.setStretchFactor(0, 0)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([320, 480])
        lay.addWidget(vsplit, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([370, 600])
        left.setMinimumWidth(300)

        gen = QFrame(objectName="panel")
        gl = QHBoxLayout(gen)
        gl.setContentsMargins(12, 8, 12, 8)
        cfg = self.cfg
        self.alone = QCheckBox("Speech replaces the rest while shown",
                               checked=bool(cfg.get("speech_alone")))
        self.alone.toggled.connect(self._general)
        gl.addWidget(self.alone)
        gl.addStretch()
        gl.addWidget(QLabel("Update at most every"))
        self.min_int = QDoubleSpinBox(decimals=1, minimum=1.0, maximum=10, singleStep=0.1,
                                      suffix=" s", value=float(cfg.get("min_interval", 1.2)))
        self.min_int.valueChanged.connect(self._general)
        gl.addWidget(self.min_int)
        gl.addWidget(QLabel("Repeat unchanged every"))
        self.keep = QSpinBox(minimum=0, maximum=120, suffix=" s",
                             value=int(cfg.get("keepalive", 20)))
        self.keep.setSpecialValueText("Never")
        self.keep.setToolTip("Sends the same message again so it stays visible in VRChat")
        self.keep.valueChanged.connect(self._general)
        gl.addWidget(self.keep)
        lay.addWidget(gen)

        QShortcut(QKeySequence.Undo, self, activated=self.undo)
        self._sample_timer = QTimer(self, interval=500, timeout=self._refresh_sample)
        self._canvas_timer = QTimer(self, interval=400, timeout=self.refresh_canvas)
        self._canvas_timer.start()
        self._palette_timer = QTimer(self, interval=2000, timeout=self.palette.refresh)
        self._palette_timer.start()
        self.reload_list()

    @property
    def cfg(self) -> dict:
        return self.s.chatbox

    def _values(self):
        return dict(getattr(self.composer, "last_values", {}) or {}) if self.composer else {}

    def set_composer(self, composer):
        self.composer = composer
        self.editor.composer = composer
        try:
            from PySide6.QtGui import QGuiApplication

            def px(sc):
                g, dpr = sc.geometry(), sc.devicePixelRatio()
                return f"{round(g.width() * dpr)}x{round(g.height() * dpr)}"
            scr = QGuiApplication.primaryScreen()
            if scr is not None:
                allscr = QGuiApplication.screens()
                composer.stats.set_screen({
                    "screen": px(scr), "refresh": f"{scr.refreshRate():.0f}",
                    "screens": " + ".join(f"{px(sc)} @ {sc.refreshRate():.0f} Hz"
                                          for sc in allscr),
                    "screen_count": str(len(allscr))})
        except Exception:
            pass
        self._update_value_watch()
        if self.editor.el is not None:
            self.editor.show_element(self.editor.el)
        self.refresh_canvas()

    def showEvent(self, e):
        super().showEvent(e)
        self._update_value_watch(True)

    def hideEvent(self, e):
        super().hideEvent(e)
        self._update_value_watch(False)

    _watching = False

    def _update_value_watch(self, on=None):
        """While this tab is open, read all values so the block list can show them."""
        if on is None:
            on = self.isVisible()
        if self.composer is not None and on != self._watching:
            self._watching = on
            try:
                self.composer.want_all_values(on)
            except Exception:
                pass

    # ---- undo
    def _snapshot(self):
        self._undo.append(copy.deepcopy(self.cfg["blocks"]))
        del self._undo[:-40]

    def undo(self):
        if not self._undo:
            return
        self.cfg["blocks"] = self._undo.pop()
        self.editor.el = None
        self.reload_list()
        self.changed.emit()

    # ---- canvas
    def refresh_canvas(self, parts=None):
        try:
            if parts is None:
                if self._sample_active():
                    return
                parts = list(getattr(self.composer, "last_parts", []) or [])
            by_id = {p["id"]: p for p in parts}
            rows = []
            for b in self.cfg["blocks"]:
                if b["type"] == "speech" and not b.get("enabled", True):
                    continue
                p = by_id.get(b["id"], {"text": "", "state": "empty"})
                rows.append((b, p["text"], p["state"]))
            self.canvas.set_parts(rows)
        except RuntimeError:
            pass

    # ---- size sample
    _sample_until = 0.0
    _last_real = ("", None)

    def _sample(self, on: bool):
        import time as _t
        self._sample_until = _t.monotonic() + 8 if on else 0.0
        self._refresh_sample()

    def _speech_block(self):
        return next(b for b in self.cfg["blocks"] if b["type"] == "speech")

    def _sample_width(self) -> int:
        return int(self._speech_block().get("width") or C.LIMIT)

    def _sample_active(self) -> bool:
        import time as _t
        btn = getattr(self.editor, "_sample_btn", None)
        try:
            sticky = btn is not None and btn.isChecked()
        except RuntimeError:
            self.editor._sample_btn = None
            sticky = False
        return sticky or _t.monotonic() < self._sample_until

    def _refresh_sample(self):
        if not self._sample_active():
            if self._sample_timer.isActive():
                self._sample_timer.stop()
                self.preview.set_status(*self._last_real)
                self.refresh_canvas()
            return
        n = self._sample_width()
        sample = C.sample_text(n)
        if self.composer:
            msg, parts = self.composer.build_parts(sample=sample)
        else:
            msg, parts = sample, [{"id": self._speech_block()["id"], "text": sample,
                                   "state": "shown"}]
        self.refresh_canvas(parts)
        self.preview.set_status(msg, None)
        self.preview.status.setText(f"Sample: {n} characters at once - not sent. "
                                    "Use “Send sample to VRChat” to see it in game.")
        if not self._sample_timer.isActive():
            self._sample_timer.start()

    def _send_sample(self):
        if self.composer:
            self.composer.set_speech(C.sample_text(self._sample_width()))

    # ---- preview
    def show_preview(self, text: str, sent: bool):
        self._last_real = (text, sent)
        if self._sample_active():
            return
        self.preview.set_status(text, sent)
        self.refresh_canvas()
        dropped = list(self.composer.last_dropped) if self.composer else []
        if dropped:
            names = ", ".join(block_name(b) for b in dropped[:4])
            self.preview.status.setText(self.preview.status.text()
                                        + f"   ·   Made room: {names}")
        if not self.s.osc_enabled:
            self.preview.set_sending_off()

    def _clear(self):
        if self.composer:
            self.composer.clear()

    # ---- editing the block list
    def reload_list(self, select_id=None):
        ids = [b["id"] for b in self.cfg["blocks"]]
        cur = select_id or (self.editor.el["id"] if self.editor.el else None)
        if cur not in ids:
            cur = None
        self.refresh_canvas()
        self._select_id(cur)

    def _by_id(self, bid):
        return next((b for b in self.cfg["blocks"] if b["id"] == bid), None)

    def select_id(self, bid):
        self._select_id(bid)

    def _select_id(self, bid):
        b = self._by_id(bid) if bid else None
        self.canvas.select(bid if b else None, emit=False)
        if b is not self.editor.el:
            self.editor.show_element(b)

    def _place(self, b, index):
        blocks = self.cfg["blocks"]
        if b in blocks:
            old = blocks.index(b)
            blocks.remove(b)
            if index is not None and old < index:
                index -= 1
        if index is None or index >= len(blocks):
            blocks.append(b)
        else:
            blocks.insert(max(0, index), b)

    def _insert_new(self, data, index):
        kind = data.get("kind")
        if kind not in ("speech", "value", "text", "rotating", "newline"):
            return
        self._snapshot()
        if kind == "speech":
            b = self._speech_block()
            b["enabled"] = True
        else:
            b = B.new_block(kind, **(data.get("over") or {}))
        self._place(b, index)
        self.reload_list(b["id"])
        self.changed.emit()

    def _move_to(self, bid, index):
        b = self._by_id(bid)
        if b is None:
            return
        self._snapshot()
        self._place(b, index)
        self.reload_list(bid)
        self.changed.emit()

    def _move_by(self, bid, d):
        b = self._by_id(bid)
        if b is None:
            return
        blocks = [x for x in self.cfg["blocks"]
                  if not (x["type"] == "speech" and not x.get("enabled", True))]
        i = blocks.index(b) + d
        if 0 <= i < len(blocks):
            self._snapshot()
            allb = self.cfg["blocks"]
            target = allb.index(blocks[i])
            allb.remove(b)
            allb.insert(target, b)
            self.reload_list(bid)
            self.changed.emit()

    def _embed(self, bid, key):
        b = self._by_id(bid)
        if b is None or b["type"] != "text":
            return
        self._snapshot()
        t = str(b.get("text", ""))
        if t.strip() in ("", "Text"):
            t = ""
        b["text"] = (t + ("" if not t or t.endswith(" ") else " ") + "{" + key + "}")
        self.reload_list(bid)
        self.editor.show_element(b)
        self.changed.emit()

    def _remove_id(self, bid):
        b = self._by_id(bid)
        if b is None:
            return
        self._snapshot()
        if b["type"] == "speech":
            b["enabled"] = False             # settings stay; drag "Speech" in to bring it back
        else:
            self.cfg["blocks"].remove(b)
        if self.editor.el is b:
            self.editor.el = None
        self.reload_list()
        self.changed.emit()

    def _element_changed(self):
        self.refresh_canvas()
        self.changed.emit()

    def _general(self, *_):
        self.cfg["speech_alone"] = self.alone.isChecked()
        self.cfg["min_interval"] = self.min_int.value()
        self.cfg["keepalive"] = self.keep.value()
        self.changed.emit()

    def _open_filters(self):
        if self.composer is None:
            return
        dlg = MediaFilterDialog(self.cfg["media_filters"], self.composer.media, self)
        if dlg.exec():
            self.cfg["media_filters"] = dlg.collect()
            if self.editor.el is not None:
                self.editor.show_element(self.editor.el)
            self._element_changed()
