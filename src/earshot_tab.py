"""The 🎧 Earshot tab: conversation-aware mode."""

from __future__ import annotations

import devices
import html
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QTextBrowser, QVBoxLayout, QWidget)

import earshot as ES
import languages
import smart

SOURCES_LINUX = [("vrchat", "VRChat only (recommended)"),
                 ("output", "Everything you hear (default output)"),
                 ("device", "An input device…")]
SOURCES_WINDOWS = [("output", "Everything you hear (default output)"),
                   ("device", "An input device… (e.g. a virtual cable for VRChat)")]


def hint(text: str) -> QLabel:
    lbl = QLabel(text, objectName="hint", wordWrap=True)
    lbl.setTextFormat(Qt.PlainText)
    return lbl


class EarshotTab(QWidget):
    changed = Signal()                 # settings changed (save + apply)
    smart_changed = Signal()           # smart model settings changed (reload)
    install_smart = Signal()
    add_room_route = Signal()

    def __init__(self, settings, meter_cls):
        super().__init__()
        self.s = settings
        self.cfg = ES.settings_of(settings)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(10)

        # ---- left: settings
        left = QFrame(objectName="panel")
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget(objectName="inner")
        f = QFormLayout(inner)
        self._form = f
        f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        f.setContentsMargins(14, 10, 14, 14)
        f.setVerticalSpacing(9)
        scroll.setWidget(inner)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(scroll)

        title = QLabel("🎧  Earshot", objectName="title")
        title.setStyleSheet("font-size: 17px;")
        f.addRow(title)
        f.addRow(hint("Conversation-aware mode. Earshot also listens to the game - what the "
                      "people around you say - and uses the conversation to translate you "
                      "better. Everything stays on this PC, nothing is saved, and what others "
                      "say is never sent to your chatbox."))
        self.on = QPushButton("Earshot is off - click to turn on", checkable=True,
                              objectName="primary")
        self.on.setChecked(bool(self.cfg.get("enabled")))
        self.on.toggled.connect(self._toggle)
        f.addRow(self.on)

        f.addRow(QLabel("LISTEN TO", objectName="section"))
        self.source = QComboBox()
        for v, label in (SOURCES_WINDOWS if ES.IS_WINDOWS else SOURCES_LINUX):
            self.source.addItem(label, v)
        i = self.source.findData(self.cfg.get("source"))
        self.source.setCurrentIndex(max(0, i))
        self.source.currentIndexChanged.connect(self._source)
        f.addRow("Game sound", self.source)
        self.device = QComboBox()
        self.device.addItems(ES.list_input_devices())
        if self.cfg.get("device"):
            self.device.setCurrentText(self.cfg["device"])
        self.device.currentTextChanged.connect(lambda v: self._set("device", v))
        f.addRow("Device", self.device)
        self.meter = meter_cls()
        f.addRow("Level", self.meter)
        self.status = hint("Off")
        f.addRow(self.status)
        self.src_hint = hint("")
        f.addRow(self.src_hint)

        f.addRow(QLabel("USE THE CONVERSATION", objectName="section"))
        for key, text, tip in (
            ("whisper_context", "Experimental: hint Whisper with my last sentences",
             "Gives Whisper what you said in the last minute as a hint. Off by default - it "
             "often makes recognition worse, not better."),
            ("echo_guard", "Ignore my mic when it only hears the game",
             "Without a headset your mic hears the speakers. Sentences that are just what the "
             "game played are not sent."),
            ("show_heard", "Show what others say (here, not in the chatbox)", ""),
        ):
            cb = QCheckBox(text, checked=bool(self.cfg.get(key)))
            cb.setToolTip(tip)
            cb.toggled.connect(lambda v, k=key: self._set(k, bool(v)))
            f.addRow(cb)
        self.show_in = self._lang_combo(self.cfg.get("show_in", "en"))
        self.show_in.currentIndexChanged.connect(lambda _i: self._set("show_in",
                                                                     self.show_in.currentData()))
        f.addRow("…translated into", self.show_in)
        self.listen_dev = QComboBox()
        for spec, label in devices.choices(include_same=True,
                                           same_label="Share my speech model (waits until "
                                                      "I'm quiet)"):
            self.listen_dev.addItem(label, spec)
        self.listen_dev.setCurrentIndex(max(0, self.listen_dev.findData(
            self.cfg.get("listen_device") or "same")))
        self.listen_dev.setToolTip("With a second graphics card, Earshot can have its own speech "
                                   "model there and listen while you talk")
        self.listen_dev.currentIndexChanged.connect(
            lambda _i: self._set("listen_device", self.listen_dev.currentData()))
        f.addRow("Game speech runs on", self.listen_dev)
        self.listen_model = QComboBox()
        for k, label in (("small", "small - light (~0.7 GB)"), ("medium", "medium (~1.3 GB)"),
                         ("large-v3", "large-v3 - best (~2.2 GB)")):
            self.listen_model.addItem(label, k)
        self.listen_model.setCurrentIndex(max(0, self.listen_model.findData(
            self.cfg.get("listen_model") or "small")))
        self.listen_model.currentIndexChanged.connect(
            lambda _i: self._set("listen_model", self.listen_model.currentData()))
        f.addRow("…with the model", self.listen_model)
        mins = QDoubleSpinBox(decimals=1, minimum=0.5, maximum=15, singleStep=0.5, suffix=" min",
                              value=float(self.cfg.get("context_minutes", 3)))
        mins.setToolTip("How far back Earshot remembers the conversation")
        mins.valueChanged.connect(lambda v: self._set("context_minutes", v))
        f.addRow("Remember the last", mins)

        f.addRow(QLabel("ROOM LANGUAGE", objectName="section"))
        self.room = QLabel("—", objectName="title")
        f.addRow("People around you speak", self.room)
        fb = self._lang_combo(self.cfg.get("room_fallback", "en"))
        fb.currentIndexChanged.connect(lambda _i: self._set("room_fallback", fb.currentData()))
        f.addRow("Until it's known, use", fb)
        f.addRow(hint("Pick “🎧 Room language” as the target of a translation route, and you're "
                      "translated into whatever the people around you speak - it switches by "
                      "itself when you join another group."))
        add = QPushButton("Add room-language routes")
        add.setToolTip("Adds “your language → 🎧 Room language” for each language you speak")
        add.clicked.connect(self.add_room_route.emit)
        f.addRow(add)

        f.addRow(QLabel("SMART TRANSLATION (EXPERIMENTAL)", objectName="section"))
        self.smart_on = QCheckBox("Translate with a local AI",
                                  checked=bool(self.cfg.get("smart")))
        self.smart_on.toggled.connect(lambda v: self._set("smart", bool(v), smart_=True))
        f.addRow(self.smart_on)
        f.addRow(hint("A Qwen2.5 language model translates your sentence with the conversation "
                      "in mind: names, what “it” means, slang, how formal people talk. It runs "
                      "on your PC (falls back to the normal translator if it's busy or fails). "
                      "Slower than NLLB and needs more graphics memory. Experimental: in "
                      "practice it's often worse than the normal translator, so it's off by "
                      "default."))
        self.smart_model = QComboBox()
        for k, (_repo, _mb, label) in smart.MODELS.items():
            self.smart_model.addItem(label, k)
        self.smart_model.addItem("Custom folder…", "custom")
        cur = "custom" if self.cfg.get("smart_path") else self.cfg.get("smart_model")
        self.smart_model.setCurrentIndex(max(0, self.smart_model.findData(cur)))
        self.smart_model.currentIndexChanged.connect(self._smart_model)
        f.addRow("Model", self.smart_model)
        self.smart_path = QLineEdit(str(self.cfg.get("smart_path", "")),
                                    placeholderText="CTranslate2 chat model folder")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        prow = QHBoxLayout()
        prow.addWidget(self.smart_path, 1)
        prow.addWidget(browse)
        self.smart_path.editingFinished.connect(
            lambda: self._set("smart_path", self.smart_path.text().strip(), smart_=True))
        self._path_row = QWidget()
        self._path_row.setLayout(prow)
        f.addRow("", self._path_row)
        self.smart_dev = QComboBox()
        for spec, label in devices.choices(include_same=True,
                                           same_label="Same card as the speech model",
                                           kind="smart"):
            self.smart_dev.addItem(label, spec)
        sd = self.cfg.get("smart_device") or "same"
        sd = "same" if sd == "cuda" else sd
        self.smart_dev.setCurrentIndex(max(0, self.smart_dev.findData(sd)))
        self.smart_dev.currentIndexChanged.connect(
            lambda _i: self._set("smart_device", self.smart_dev.currentData(), smart_=True))
        f.addRow("Runs on", self.smart_dev)
        en = QCheckBox("Also for translations into English",
                       checked=bool(self.cfg.get("smart_english", True)))
        en.setToolTip("Off: Whisper's own English translation is used (faster)")
        en.toggled.connect(lambda v: self._set("smart_english", bool(v)))
        f.addRow(en)
        self.smart_btn = QPushButton("Install")
        self.smart_btn.clicked.connect(self.install_smart.emit)
        self.smart_status = hint("")
        srow = QHBoxLayout()
        srow.addWidget(self.smart_btn)
        srow.addWidget(self.smart_status, 1)
        f.addRow(srow)
        outer.addWidget(left, 5)

        # ---- right: the conversation
        right = QFrame(objectName="panel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 10, 14, 12)
        head = QHBoxLayout()
        t = QLabel("CONVERSATION", objectName="section")
        t.setStyleSheet("padding-top: 0;")
        head.addWidget(t)
        head.addStretch()
        self.count = QLabel(objectName="hint")
        head.addWidget(self.count)
        save = QPushButton("Save…")
        save.setToolTip("Save this conversation to a text file")
        save.clicked.connect(self._save)
        clear = QPushButton("Clear")
        clear.setToolTip("Forget the conversation (Earshot starts fresh)")
        clear.clicked.connect(self._clear)
        head.addWidget(save)
        head.addWidget(clear)
        rl.addLayout(head)
        self.view = QTextBrowser()
        self.view.setPlaceholderText("Turn Earshot on. What people around you say shows up here, "
                                     "with your own sentences in between.")
        rl.addWidget(self.view, 1)
        outer.addWidget(right, 4)

        self.engine = None
        self._lines = []
        self._sync()

    # ---- helpers
    def _lang_combo(self, code):
        c = QComboBox()
        for lc, name, _ in languages.LANGUAGES:
            c.addItem(name, lc)
        c.setCurrentIndex(max(0, c.findData(code)))
        c.setMaxVisibleItems(16)
        return c

    def _set(self, key, value, smart_=False):
        self.cfg[key] = value
        self._sync()
        self.changed.emit()
        if smart_:
            self.smart_changed.emit()

    def _toggle(self, on):
        self._set("enabled", bool(on), smart_=True)

    def _source(self, _i):
        self._set("source", self.source.currentData())

    def _smart_model(self, _i):
        v = self.smart_model.currentData()
        if v == "custom":
            self._sync()
            if not self.smart_path.text().strip():
                self._browse()
            return
        self.cfg["smart_path"] = ""
        self.smart_path.setText("")
        self._set("smart_model", v, smart_=True)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "CTranslate2 chat model folder",
                                             self.smart_path.text())
        if d:
            self.smart_path.setText(d)
            self._set("smart_path", d, smart_=True)

    def _sync(self):
        on = bool(self.cfg.get("enabled"))
        self.on.setText("🎧  Earshot is on - click to turn off" if on
                        else "Earshot is off - click to turn on")
        try:
            self._form.setRowVisible(self.device, self.cfg.get("source") == "device")
        except AttributeError:                          # Qt < 6.4
            self.device.setVisible(self.cfg.get("source") == "device")
        src = self.cfg.get("source")
        self.src_hint.setText({
            "vrchat": "Records only VRChat's sound (PipeWire), so music and Discord stay out.",
            "output": "Records everything your PC plays. Music and videos can end up in the "
                      "conversation - “VRChat only” or a virtual cable avoids that."
                      if not ES.IS_WINDOWS else
                      "Records everything your PC plays (Windows loopback). Music and videos "
                      "can end up in the conversation. To hear only VRChat, set VRChat's "
                      "output to a virtual cable (e.g. VB-CABLE) in Windows' sound settings "
                      "and pick that cable here as an input device.",
            "device": "Any input device - e.g. a virtual cable or a Voicemeeter output that "
                      "only carries VRChat.",
        }.get(src, ""))
        if ES.IS_WINDOWS and src != "device":
            import importlib.util
            if importlib.util.find_spec("soundcard") is None:
                self.src_hint.setText("⚠ Listening to the game isn't installed. Add “Earshot: "
                                      "listen to the game” in ⚙ Settings › Parts & about.")
        custom = self.smart_model.currentData() == "custom"
        self._path_row.setVisible(custom)
        try:
            self._form.setRowVisible(self.listen_model,
                                     (self.cfg.get("listen_device") or "same") != "same")
        except AttributeError:
            pass
        self.update_smart(getattr(self, "_smart_state", ("off", 0))[0],
                          getattr(self, "_smart_state", ("off", 0))[1])

    # ---- from the engine
    def set_engine(self, engine):
        self.engine = engine

    def set_status(self, text, ok):
        self.status.setText(("● " if ok else "○ ") + text)

    def update_smart(self, state, pct=0):
        self._smart_state = (state, pct)
        installed = smart.is_installed(self.cfg)
        custom = bool(self.cfg.get("smart_path"))
        if state == "downloading":
            msg = f"Downloading… {pct}%"
        elif state == "loading":
            msg = "Loading the model…"
        elif state == "ready":
            msg = "✓ Ready - your translations now use the conversation."
        elif state == "error":
            msg = "⚠ Failed - see the log."
        elif not installed:
            size = smart.MODELS.get(self.cfg.get("smart_model"), ("", 0, ""))[1]
            msg = ("⚠ No model found in that folder (needs model.bin and tokenizer.json)."
                   if custom else f"Not installed (one-time download, ~{size} MB).")
        elif not self.cfg.get("smart") or not self.cfg.get("enabled"):
            msg = "Installed. Tick the box above (and turn Earshot on) to use it."
        else:
            msg = "Installed."
        self.smart_status.setText(msg)
        self.smart_btn.setVisible(not installed and not custom and state != "downloading")

    def set_room(self, code, n):
        self.room.setText(f"{languages.name(code)}  ({n} lines)" if code
                          else "— not known yet")

    def add_line(self, ln):
        self._lines.append(ln)
        del self._lines[:-300]
        stamp = time.strftime("%H:%M:%S", time.localtime(ln.t))
        lang = languages.name(ln.lang)
        if ln.who == "them":
            who = f'<span style="color:#4c9cff; font-weight:600">🎧 Someone · {lang}</span>'
            body = html.escape(ln.text)
            if ln.translation:
                body += (f'<br><span style="color:#9aa2ad">→ {html.escape(ln.translation)}'
                         f'</span>')
        else:
            who = f'<span style="color:#7ee787; font-weight:600">🗣 You · {lang}</span>'
            body = html.escape(ln.text)
            if ln.translation and ln.translation.strip() != ln.text.strip():
                body += (f'<br><span style="color:#9aa2ad">sent: '
                         f'{html.escape(ln.translation)}</span>')
        self.view.append(f'<p><span style="color:#8a929d; font-size:11px">{stamp}</span> '
                         f'{who}<br>{body}</p>')
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())
        them = sum(1 for x in self._lines if x.who == "them")
        self.count.setText(f"{them} heard")
        if self.engine is not None:
            es = self.engine.earshot
            code, n = es.ctx.room_language(float(self.cfg.get("context_minutes", 3)))
            self.set_room(code, n)

    def _clear(self):
        self._lines.clear()
        self.view.clear()
        self.count.setText("")
        if self.engine is not None:
            self.engine.earshot.ctx.clear()
            self.engine.earshot.update_room_language()
        self.set_room(None, 0)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save conversation", "conversation.txt",
                                              "Text (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            for ln in self._lines:
                stamp = time.strftime("%H:%M:%S", time.localtime(ln.t))
                who = "Someone" if ln.who == "them" else "You"
                fh.write(f"[{stamp}] {who} ({ln.lang}): {ln.text}\n")
                if ln.translation:
                    fh.write(f"           -> {ln.translation}\n")
