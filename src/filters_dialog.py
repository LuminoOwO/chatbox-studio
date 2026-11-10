"""Dialog for editing word replacements and blocked phrases."""

from __future__ import annotations

import copy
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from filters import FILLERS, FilterSet


class RuleTable(QWidget):
    """Editable table: checkbox columns + text columns, with Add/Remove."""

    def __init__(self, columns: list[tuple[str, str, str]], rows: list[dict], on_change):
        # columns: (key, header, kind) where kind is "text" or "check"
        super().__init__()
        self.columns = columns
        self.on_change = on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels([c[1] for c in columns])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        hdr = self.table.horizontalHeader()
        for i, (_k, _h, kind) in enumerate(columns):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents if kind == "check"
                                     else QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        add = QPushButton("+ Add rule")
        add.clicked.connect(self.add_empty)
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self.remove_selected)
        btns.addWidget(add)
        btns.addWidget(rem)
        btns.addStretch()
        lay.addLayout(btns)

        for r in rows:
            self.add_row(r)
        self.table.itemChanged.connect(lambda *_: self.on_change())

    def add_row(self, data: dict):
        self.table.blockSignals(True)
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, (key, _h, kind) in enumerate(self.columns):
            if kind == "check":
                default = key in ("enabled", "whole_word")
                it = QTableWidgetItem()
                it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setCheckState(Qt.Checked if data.get(key, default) else Qt.Unchecked)
                it.setTextAlignment(Qt.AlignCenter)
            else:
                it = QTableWidgetItem(str(data.get(key, "")))
            self.table.setItem(row, col, it)
        self.table.blockSignals(False)
        return row

    def add_empty(self):
        row = self.add_row({})
        text_col = next(i for i, c in enumerate(self.columns) if c[2] == "text")
        self.table.setCurrentCell(row, text_col)
        self.table.editItem(self.table.item(row, text_col))

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
        self.on_change()

    def rows(self) -> list[dict]:
        out = []
        for row in range(self.table.rowCount()):
            d = {}
            for col, (key, _h, kind) in enumerate(self.columns):
                it = self.table.item(row, col)
                if kind == "check":
                    d[key] = it is not None and it.checkState() == Qt.Checked
                else:
                    d[key] = it.text() if it else ""
            text_keys = [k for k, _h, kind in self.columns if kind == "text"]
            if d.get(text_keys[0], "").strip():
                out.append(d)
        return out


class FiltersDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filters & replacements")
        self.resize(780, 580)
        self.s = copy.deepcopy(settings)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # replacements
        rep = QWidget()
        rl = QVBoxLayout(rep)
        rl.addWidget(self._hint(
            "Replaces words in the English text before it's sent. Matching ignores upper/lower "
            "case, and the capitalisation of the original word is kept. Leave “Replace with” "
            "empty to delete a word."))
        self.rep_table = RuleTable(
            [("enabled", "On", "check"), ("find", "Find", "text"),
             ("replace", "Replace with", "text"), ("whole_word", "Whole word", "check"),
             ("regex", "Regex", "check")],
            self.s.replace_rules, self.refresh_test)
        rl.addWidget(self.rep_table, 1)
        tabs.addTab(rep, "Replace words")

        # block list
        blk = QWidget()
        bl = QVBoxLayout(blk)
        bl.addWidget(self._hint(
            "Sentences that contain any of these words or phrases are not sent to VRChat at all. "
            "They still show up in the log, so you can see what was held back."))
        self.blk_table = RuleTable(
            [("enabled", "On", "check"), ("pattern", "Word or phrase", "text"),
             ("whole_word", "Whole word", "check"), ("regex", "Regex", "check")],
            self.s.block_rules, self.refresh_test)
        bl.addWidget(self.blk_table, 1)
        tabs.addTab(blk, "Block sentences")

        # options
        opt = QWidget()
        form = QFormLayout(opt)
        form.setVerticalSpacing(12)
        self.cb_fillers = QCheckBox("Remove filler words", checked=self.s.remove_fillers)
        form.addRow(self.cb_fillers)
        form.addRow(self._hint(", ".join(FILLERS)))
        self.min_words = QSpinBox(minimum=0, maximum=20, value=int(self.s.min_words))
        self.min_words.setSpecialValueText("Off")
        form.addRow("Ignore sentences with fewer words than", self.min_words)
        form.addRow(self._hint("For example 2 drops lone “Okay.” or “Yeah.”"))
        self.cb_typed = QCheckBox("Also filter messages I type by hand", checked=self.s.filter_typed)
        form.addRow(self.cb_typed)
        for w in (self.cb_fillers, self.cb_typed):
            w.toggled.connect(self.refresh_test)
        self.min_words.valueChanged.connect(self.refresh_test)
        tabs.addTab(opt, "Options")

        # live tester
        test = QFrame(objectName="panel")
        tl = QVBoxLayout(test)
        tl.setContentsMargins(12, 10, 12, 12)
        tl.addWidget(QLabel("TRY IT", objectName="section"))
        self.test_in = QLineEdit(placeholderText="Type a sentence to see what would be sent…")
        self.test_in.setText("Uh, damn, I love VR chat!")
        self.test_in.textChanged.connect(self.refresh_test)
        tl.addWidget(self.test_in)
        self.test_out = QLabel(textFormat=Qt.RichText, wordWrap=True)
        tl.addWidget(self.test_out)
        lay.addWidget(test)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.refresh_test()

    @staticmethod
    def _hint(text):
        lbl = QLabel(text, objectName="hint", wordWrap=True)
        return lbl

    def collect(self):
        self.s.replace_rules = self.rep_table.rows()
        self.s.block_rules = self.blk_table.rows()
        self.s.remove_fillers = self.cb_fillers.isChecked()
        self.s.min_words = self.min_words.value()
        self.s.filter_typed = self.cb_typed.isChecked()
        return self.s

    def refresh_test(self, *_):
        s = copy.copy(self.collect())
        s.filters_enabled = True
        fs = FilterSet.from_settings(s)
        parts = []
        text = self.test_in.text().strip()
        if text:
            out = fs.apply(text)
            if out.blocked:
                parts.append(f'<span style="color:#e5534b">Not sent - {html.escape(out.reason)}</span>')
            else:
                parts.append(f'<span style="color:#8b939e">Sends:</span> '
                             f'<span style="color:#3fb68b; font-size:14px">{html.escape(out.text)}</span>')
        for err in fs.errors:
            parts.append(f'<span style="color:#e5a54b">⚠ {html.escape(err)}</span>')
        self.test_out.setText("<br>".join(parts))
