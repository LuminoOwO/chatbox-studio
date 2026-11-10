#!/usr/bin/env python3
"""Chatbox Studio - voice translation and a status chatbox for VRChat."""

from __future__ import annotations

import html
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
sys.path.insert(0, str(Path(__file__).resolve().parent))   # works from any working directory

import paths
from version import APP_NAME, __version__

paths.setup_output_streams()          # no console (pythonw): log instead of crashing
paths.setup_cuda_libraries()          # Windows: CUDA DLLs from the nvidia-* packages

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

import engine
import theme
from engine import Result, Settings
from filters_dialog import FiltersDialog
from routes_widget import RoutesPanel
from chatbox_tab import ChatboxTab, QuickBar, load_fonts
import chatbox as chatbox_mod
import languages
import translator as nllb

APP_DIR = Path(__file__).resolve().parent

CONFIG = paths.config_dir() / "settings.json"


def hint_label(text: str) -> QLabel:
    lb = QLabel(text, objectName="hint", wordWrap=True)
    lb.setTextFormat(Qt.PlainText)
    return lb

MODELS = [
    ("large-v3", "large-v3  (best quality)"),
    ("large-v2", "large-v2  (sometimes better translations)"),
    ("medium", "medium  (faster, a bit less accurate)"),
    ("small", "small  (fastest, rough)"),
]
SPEEDS = [(1, "Fastest (greedy)"), (2, "Balanced"), (5, "Most accurate")]
TEMPLATES = [
    "{text}",
    "{text} ({original})",
    "{original}\\n{text}",
    "[{src}→{dst}] {text}",
]

STATE_STYLE = {
    "loading": ("Loading model", "#c9a227"),
    "ready": ("Listening", "#3fb68b"),
    "speaking": ("Hearing you", "#4c9cff"),
    "working": ("Transcribing", "#a77bff"),
    "paused": ("Paused", "#7d8590"),
    "error": ("Error - see log", "#e5534b"),
}

STYLE = theme.build_style()          # default look; the real one is set from settings


class Bridge(QObject):
    translator = Signal(str, int)
    preview = Signal(str, bool)
    vrc = Signal(str, bool)
    log = Signal(str)
    result = Signal(object)
    level = Signal(float, bool)
    state = Signal(str)
    heard = Signal(object)
    earshot = Signal(str, bool)
    room_level = Signal(float, bool)
    smart = Signal(str, int)


class LevelMeter(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(8)
        self.level = 0.0
        self.voice = False

    def set(self, level: float, voice: bool):
        self.level = max(level, self.level * 0.85)   # smooth decay
        self.voice = voice
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        pal = theme.palette(getattr(self, "theme", "dark"))
        p.setBrush(QColor(pal["meter_track"]))
        p.drawRoundedRect(self.rect(), 4, 4)
        w = int(self.width() * min(1.0, self.level))
        if w > 0:
            p.setBrush(QColor(pal["meter_voice"] if self.voice else pal["meter_idle"]))
            p.drawRoundedRect(0, 0, w, self.height(), 4, 4)


class StatePill(QLabel):
    def set_state(self, state: str):
        text, color = STATE_STYLE.get(state, (state, "#7d8590"))
        self.setText(f"●  {text}")
        c = QColor(color)
        rgba = lambda a: f"rgba({c.red()},{c.green()},{c.blue()},{a})"
        self.setStyleSheet(
            f"color: {color}; background: {rgba(0.13)}; border: 1px solid {rgba(0.35)};"
            "border-radius: 11px; padding: 3px 10px; font-weight: 600;")


def load_settings() -> Settings:
    s = Settings()
    try:
        data = json.loads(CONFIG.read_text())
        for k, v in data.items():
            if hasattr(s, k):
                setattr(s, k, v)
    except (OSError, ValueError):
        # first start: with several cards, the smallest one that fits a speech model - so
        # the main card stays free for VRChat
        import devices
        gl = devices.available_gpus()
        if gl:
            fit = [g for g in gl if g.vram_mb >= 3500] or gl
            g = min(fit, key=lambda x: x.vram_mb) if len(gl) > 1 else gl[0]
            s.speech_device = f"{g.vendor}:{g.index}"
            s.gpu_index = g.index
        else:
            s.gpu_index = -1               # no NVIDIA GPU: run on the CPU
            s.model = "small"              # large models are too slow on a CPU
            s.translator_device = "cpu"
    return s


def save_settings(s: Settings):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(asdict(s), indent=2))


class Window(QMainWindow):
    def __init__(self, settings: Settings, start_engine: bool = True):
        super().__init__()
        self.s = settings
        import blocks as _blocks
        self.s.chatbox = _blocks.normalize(self.s.chatbox)      # 1.18 elements -> blocks
        self.engine: engine.Engine | None = None
        self.history: list[str] = []
        self.tr_state, self.tr_pct = "missing", 0
        self.bridge = Bridge()
        self.bridge.log.connect(self.add_log)
        self.bridge.result.connect(self.add_result)
        self.bridge.translator.connect(self.on_translator_state)
        self.bridge.vrc.connect(self.on_vrc_state)
        self.bridge.level.connect(lambda lv, v: self.meter.set(lv, v))

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1140, 760)
        self._build()
        self.bridge.state.connect(self._on_state)
        self.bridge.level.connect(self._on_level_for_checklist)
        self._on_state("loading")
        self.meter.theme = self.s.ui_theme
        self._shortcuts()
        self.on_vrc_state("off", False)

        self._save_timer = QTimer(self, singleShot=True, interval=400,
                                  timeout=lambda: save_settings(self.s))
        if start_engine:
            QTimer.singleShot(0, self.start_engine)
            if not self.s.extra.get("onboarded"):
                QTimer.singleShot(700, self.show_quick_start)

    def _on_state(self, state):
        self.pill.set_state(state)
        self._pill_state = state
        self.update_checklist()

    # ------------------------------------------------------------------ UI
    def _build(self):
        # created first so anything during setup can already log
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setMaximumBlockCount(300)
        self.logbox.setFixedHeight(120)

        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(12)

        # header
        head = QHBoxLayout()
        title = QLabel(f"{APP_NAME}  <span style='color:#6b737e; font-size:12px; "
                       f"font-weight:400'>v{__version__}</span>", objectName="title")
        sub = QLabel("Speak  →  translated into your VRChat chatbox, all locally", objectName="hint")
        tbox = QVBoxLayout()
        tbox.setSpacing(2)
        tbox.addWidget(title)
        tbox.addWidget(sub)
        head.addLayout(tbox)
        head.addStretch()
        self.pill = StatePill()
        head.addWidget(self.pill)
        self.vrc_chip = QLabel()
        head.addWidget(self.vrc_chip)
        self.tr_chip = QPushButton()
        self.tr_chip.clicked.connect(self.on_install_translator)
        head.addWidget(self.tr_chip)
        self.listen_btn = QPushButton("Pause", objectName="listen", checkable=True, checked=True)
        self.listen_btn.setMinimumWidth(110)
        self.listen_btn.toggled.connect(self.on_listen_toggled)
        head.addWidget(self.listen_btn)
        self.help_btn = QPushButton("❔ Help")
        self.help_btn.setToolTip("Quick start, a tour of the window, keyboard shortcuts (F1)")
        hm = QMenu(self.help_btn)
        hm.addAction("Quick start…", self.show_quick_start)
        hm.addAction("Show me around (tour)", self.start_tour)
        hm.addAction("Keyboard shortcuts", lambda: self.open_settings("general"))
        hm.addSeparator()
        hm.addAction("Open the manual (README)", self.open_readme)
        hm.addAction("Copy the log (for a bug report)", self.copy_log)
        self.help_btn.setMenu(hm)
        head.addWidget(self.help_btn)
        self.settings_btn = QPushButton("⚙ Settings")
        self.settings_btn.setToolTip("Theme, speech model, translator, VRChat ports, filters, "
                                     "parts (Ctrl+,)")
        self.settings_btn.clicked.connect(lambda: self.open_settings())
        head.addWidget(self.settings_btn)
        outer.addLayout(head)
        self.quick = QuickBar(lambda: self.s.chatbox)
        self.quick.changed.connect(self.on_chatbox_changed)
        self._quick_sig = self._quick_signature()
        outer.addWidget(self.quick)

        body = QHBoxLayout()
        body.setSpacing(12)
        outer.addLayout(body, 1)

        # ---- quick panel (what you change while playing); everything else is in Settings
        from ui_help import Checklist, SettingsDialog
        self.settings_dlg = SettingsDialog(self)
        dlg = self.settings_dlg
        pg_general = dlg.page("general", "General", "🎨",
                              "How the app looks, and its keyboard shortcuts.")
        pg_speech = dlg.page("speech", "Speech recognition", "🗣",
                             "The Whisper model that writes down what you say, and where it runs.")
        pg_tr = dlg.page("translation", "Translation", "🌍",
                         "The translator for languages other than English, and how translations "
                         "look in the chatbox. Which languages are translated is set in "
                         "“Translation routes” on the main window.")
        pg_vrc = dlg.page("vrchat", "VRChat & OSC", "🥽",
                          "How the app talks to VRChat. The defaults work for almost everyone.")
        pg_filters = dlg.page("filters", "Filters", "🧹",
                              "Remove filler words, replace words, or block sentences before they "
                              "reach your chatbox.")
        pg_parts = dlg.page("parts", "Parts & about", "🧩",
                            "Add or remove speech models, the translator, graphics card support "
                            "and the Earshot AI.")
        panel = QFrame(objectName="panel")
        panel.setFixedWidth(int(320 * min(1.35, max(1.0, self.s.ui_scale))))
        self.panel = panel
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(widgetResizable=True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget(objectName="inner")
        form = QFormLayout(inner)
        form.setContentsMargins(14, 6, 14, 14)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        scroll.setWidget(inner)
        pl.addWidget(scroll)

        quick = form

        def section(text, where=None):
            (where or form).addRow(QLabel(text.upper(), objectName="section"))

        self.checklist = Checklist()
        quick.addRow(self.checklist)
        section("Microphone")
        self.mic = self._shrink(QComboBox())
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.mic, 1)
        refresh = QPushButton("↻")
        refresh.setAccessibleName("Refresh microphone list")
        refresh.setFixedWidth(32)
        refresh.setToolTip("Refresh microphone list")
        refresh.clicked.connect(self.fill_mics)
        mic_row.addWidget(refresh)
        form.addRow(mic_row)
        self.fill_mics()
        self.mic.currentIndexChanged.connect(self.on_mic_changed)

        self.sens = self._slider(20, 90, int(self.s.vad_threshold * 100))
        self.sens_lbl = QLabel()
        self.sens.valueChanged.connect(self.on_sens)
        form.addRow("Voice threshold", self._with_value(self.sens, self.sens_lbl))
        self.on_sens(self.sens.value(), save=False)

        self.pause = self._slider(250, 1500, self.s.end_silence_ms, step=50)
        self.pause_lbl = QLabel()
        self.pause.valueChanged.connect(self.on_pause)
        form.addRow("Send after pause", self._with_value(self.pause, self.pause_lbl))
        self.on_pause(self.pause.value(), save=False)
        form.addRow(QLabel("Shorter pause = faster, but may cut sentences.", objectName="hint"))

        section("VRChat")
        self.cb_osc = self._check("Send to chatbox", "osc_enabled")
        self.cb_osc.setToolTip("Off: the app still listens and translates, but nothing goes to "
                               "VRChat")
        self.cb_mute = self._check("Pause when muted in VRChat", "follow_vrc_mute")
        self.cb_mute.setToolTip("Follows your VRChat mute button")
        form.addRow(self.cb_osc)
        form.addRow(self.cb_mute)
        from gpu_panel import GpuMonitor
        self.gpu_title = QLabel("GRAPHICS CARDS", objectName="section")
        self.gpu_title.setToolTip("Live memory and load of every card your models run on or "
                                  "are set to run on. Bar: colour = Chatbox Studio, grey = "
                                  "VRChat and other programs. Graph: load (green area) and "
                                  "memory (line) over the last 3 minutes. Hover a card for "
                                  "the list of programs.")
        form.addRow(self.gpu_title)
        self.gpu_mon = GpuMonitor(theme.palette(self.s.ui_theme)["accent"],
                                  show_all=bool(self.s.extra.get("gpu_monitor_all")))
        form.addRow(self.gpu_mon)
        if self.s.extra.get("hide_gpu_monitor"):
            self.gpu_title.setVisible(False)
            self.gpu_mon.setVisible(False)
        more = QPushButton("More settings…")
        more.setToolTip("Speech model, translator, VRChat ports, filters, theme")
        more.clicked.connect(lambda: self.open_settings())
        form.addRow(more)

        # ---------------- Settings window pages
        form = pg_speech
        section("Timing", form)
        self.maxlen = self._slider(4, 20, int(round(self.s.max_utterance_s)))
        self.maxlen_lbl = QLabel()
        self.maxlen.valueChanged.connect(self.on_maxlen)
        form.addRow("Send long talk every", self._with_value(self.maxlen, self.maxlen_lbl))
        self.on_maxlen(self.maxlen.value(), save=False)
        form.addRow(QLabel("If you talk without a pause, a piece is sent at the latest after "
                           "this time. Lower = the chatbox keeps up better.",
                           objectName="hint", wordWrap=True))

        section("Model", form)
        import devices
        self.gpu = self._shrink(QComboBox())
        for spec, label in devices.choices():
            self.gpu.addItem(label, spec)
        self._select(self.gpu, devices.speech_spec(self.s))
        self.gpu.setToolTip("Where the speech model runs. NVIDIA and AMD cards can be mixed: "
                            "the translator and the Earshot AI can run on another card.")
        form.addRow("Device", self.gpu)
        if devices.speech_spec(self.s) == "cpu":
            form.addRow(QLabel("On the CPU, use the small or medium model.",
                               objectName="hint", wordWrap=True))

        self.model = self._shrink(QComboBox())
        for key, label in MODELS:
            self.model.addItem(label, key)
        self._select(self.model, self.s.model)
        form.addRow("Model", self.model)

        self.speed = self._shrink(QComboBox())
        for beam, label in SPEEDS:
            self.speed.addItem(label, beam)
        self._select(self.speed, self.s.beam_size)
        self.speed.currentIndexChanged.connect(self.on_simple_change)
        form.addRow("Decoding", self.speed)

        self.reload_btn = QPushButton("Load model", objectName="primary")
        self.reload_btn.clicked.connect(self.on_reload)
        self.gpu.currentIndexChanged.connect(self.mark_reload)
        self.model.currentIndexChanged.connect(self.mark_reload)
        form.addRow(self.reload_btn)

        form = pg_tr
        section("Translator", form)
        self.tr_status = QLabel(objectName="hint", wordWrap=True)
        form.addRow(self.tr_status)
        self.tr_model = self._shrink(QComboBox())
        for key, label in nllb.MODELS.items():
            self.tr_model.addItem(label, key)
        self._select(self.tr_model, self.s.translator_model)
        self.tr_model.currentIndexChanged.connect(self.on_tr_settings)
        form.addRow("Translator", self.tr_model)
        self.tr_device = self._shrink(QComboBox())
        for spec, label in devices.choices(include_same=True,
                                           same_label="Same as the speech model", kind="nllb"):
            self.tr_device.addItem(label, spec)
        tdev = self.s.translator_device if self.s.translator_device not in ("cuda", "") \
            else "same"
        self._select(self.tr_device, tdev)
        self.tr_device.setToolTip("Put the translator on another graphics card to keep memory "
                                  "free on the first one")
        self.tr_device.currentIndexChanged.connect(self.on_tr_settings)
        form.addRow("Runs on", self.tr_device)
        self.tr_install = QPushButton()
        self.tr_install.clicked.connect(self.on_install_translator)
        form.addRow(self.tr_install)
        self.cb_nllb_en = self._check("Use translator for English too", "nllb_for_english")
        self.cb_nllb_en.setToolTip("Normally Whisper translates to English by itself (faster). "
                                   "The translator can be more accurate for some languages.")
        form.addRow(self.cb_nllb_en)
        self.cb_tr_typed = self._check("Translate typed messages", "translate_typed")
        self.cb_tr_typed.setToolTip("Typed text is treated as the language of your first route "
                                    "and goes through that language's routes.")
        form.addRow(self.cb_tr_typed)

        section("In the chatbox", form)
        form.addRow(QLabel("Chatbox format", objectName="hint"))
        self.template = QComboBox(editable=True)
        self.template.addItems(TEMPLATES)
        self.template.setCurrentText(self.s.chatbox_template)
        self.template.currentTextChanged.connect(self.on_template)
        form.addRow(self.template)
        self.join = self._shrink(QComboBox())
        for key, label in [("newline", "New line"), ("bar", "  |  "), ("slash", "  /  ")]:
            self.join.addItem(label, key)
        self._select(self.join, self.s.join_with)
        self.join.currentIndexChanged.connect(self.on_join)
        form.addRow("Several translations", self.join)
        form.addRow(QLabel("{text} translation · {original} what you said · {src} {dst} "
                           "language codes · \\n new line", objectName="hint", wordWrap=True))
        self.update_translator_ui()

        form = pg_vrc
        section("Chatbox", form)
        self.cb_typing = self._check("Show typing indicator", "typing_indicator")
        self.cb_sound = self._check("Chatbox notification sound", "notify_sound")
        for cb in (self.cb_typing, self.cb_sound):
            form.addRow(cb)
        form.addRow(hint_label("“Send to chatbox” and “Pause when muted” are on the main "
                               "window, left."))
        section("Ports", form)
        self.port = QSpinBox(minimum=1, maximum=65535, value=self.s.osc_port)
        self.port.valueChanged.connect(self.on_port)
        self.port.setToolTip("Where chatbox messages go. VRChat listens on 9000.")
        form.addRow("Send to port", self.port)
        self.lport = QSpinBox(minimum=1, maximum=65535, value=int(self.s.osc_listen_port))
        self.lport.setToolTip("Where VRChat's mute and AFK state arrive. VRChat sends to 9001. "
                              "With an OSC router (VOR, ...), use the port the router "
                              "forwards to.")
        self.lport.setAccessibleName("Listen port")
        self._lport_timer = QTimer(self, singleShot=True, interval=800,
                                   timeout=self.apply_listen_port)
        self.lport.valueChanged.connect(lambda _v: self._lport_timer.start())
        form.addRow("Listen on port", self.lport)
        self.cb_oscq = self._check("Find VRChat by itself (OSCQuery)", "oscquery")
        self.cb_oscq.setToolTip("Sends the chatbox to the port VRChat really uses (it can differ "
                                "from 9000 with OSC routers or --osc), and if the listen port "
                                "is taken, picks a free one that VRChat finds by itself.")
        form.addRow(self.cb_oscq)
        form.addRow(QLabel("Using an OSC router like VOR? Leave “Find VRChat by itself” on: the "
                           "chatbox goes straight to VRChat, and mute/AFK arrive on a free port "
                           "VRChat finds by itself. Keep “Send to port” at 9000 - it's VRChat's "
                           "port, not the router's.", objectName="hint", wordWrap=True))

        form = pg_filters
        self.cb_filters = self._check("Use filters and replacements", "filters_enabled")
        form.addRow(self.cb_filters)
        self.filter_summary = QLabel(objectName="hint", wordWrap=True)
        form.addRow(self.filter_summary)
        edit_filters = QPushButton("Edit filters…")
        edit_filters.clicked.connect(self.on_edit_filters)
        form.addRow(edit_filters)
        self.update_filter_summary()

        form = pg_general
        section("Appearance", form)
        self.theme_box = self._shrink(QComboBox())
        for key, label in theme.THEMES:
            self.theme_box.addItem(label, key)
        self._select(self.theme_box, self.s.ui_theme)
        self.theme_box.currentIndexChanged.connect(self.on_appearance)
        form.addRow("Theme", self.theme_box)
        self.scale_box = self._shrink(QComboBox())
        for value, label in theme.SCALES:
            self.scale_box.addItem(label, value)
        i = min(range(len(theme.SCALES)), key=lambda k: abs(theme.SCALES[k][0] - self.s.ui_scale))
        self.scale_box.setCurrentIndex(i)
        self.scale_box.currentIndexChanged.connect(self.on_appearance)
        form.addRow("Text size", self.scale_box)
        section("Help", form)
        self.cb_tips = QCheckBox("Show the “Getting started” checklist",
                                 checked=not self.s.extra.get("hide_checklist", False))
        self.cb_tips.toggled.connect(self.on_tips_toggled)
        form.addRow(self.cb_tips)
        self.cb_gpu_mon = QCheckBox("Show live graphics card usage on the main window",
                                    checked=not self.s.extra.get("hide_gpu_monitor", False))
        self.cb_gpu_mon.toggled.connect(self.on_gpu_mon_toggled)
        form.addRow(self.cb_gpu_mon)
        self.cb_gpu_all = QCheckBox("…also cards Chatbox Studio doesn't use",
                                    checked=bool(self.s.extra.get("gpu_monitor_all")))
        self.cb_gpu_all.setToolTip("Shows every NVIDIA / AMD card, e.g. the one VRChat runs on")
        self.cb_gpu_all.toggled.connect(self.on_gpu_all_toggled)
        form.addRow(self.cb_gpu_all)
        again = QHBoxLayout()
        qs = QPushButton("Quick start…")
        qs.clicked.connect(self.show_quick_start)
        tour = QPushButton("Show me around")
        tour.clicked.connect(self.start_tour)
        again.addWidget(qs)
        again.addWidget(tour)
        again.addStretch()
        form.addRow(again)
        section("Keyboard shortcuts", form)
        for keys, what in (("Ctrl+P", "Pause / resume listening"),
                           ("Ctrl+H", "Hide extras (privacy: only speech in the chatbox)"),
                           ("Ctrl+L", "Clear the chatbox in VRChat"),
                           ("Ctrl+1 / 2 / 3", "Speech & translation / Chatbox / Earshot tab"),
                           ("Ctrl+,", "Settings"), ("F1", "Help"),
                           ("Tab / Shift+Tab", "Move between controls")):
            k = QLabel(f"<b>{keys}</b>")
            form.addRow(k, QLabel(what))

        form = pg_parts
        parts = QPushButton("Add or remove parts…", objectName="primary")
        parts.setToolTip("Opens Chatbox Studio Setup: speech models, translator, graphics card "
                         "support, Earshot AI and more")
        parts.clicked.connect(self.open_setup)
        form.addRow(parts)
        form.addRow(hint_label("Opens Chatbox Studio Setup. It shows how much graphics memory "
                               "and disk space each part needs. Restart the app afterwards."))
        section("About", form)
        form.addRow(QLabel(f"{APP_NAME} {__version__}"))
        form.addRow(hint_label(f"Settings: {paths.config_dir()}\nApp and models: "
                               f"{paths.data_dir()}"))
        folder = QPushButton("Open the app folder")
        folder.clicked.connect(lambda: self._open_path(paths.data_dir()))
        form.addRow(folder)
        form = quick

        body.addWidget(panel)

        # ---- transcript side
        right = QVBoxLayout()
        right.setSpacing(8)
        self.tabs = QTabWidget(objectName="maintabs")
        speech_tab = QWidget()
        sp = QVBoxLayout(speech_tab)
        sp.setContentsMargins(0, 8, 0, 0)
        sp.setSpacing(8)
        self.chatbox_tab = ChatboxTab(self.s)
        self.chatbox_tab.changed.connect(self.on_chatbox_changed)
        self.bridge.preview.connect(self.chatbox_tab.show_preview)
        cb_wrap = QWidget()
        cw = QVBoxLayout(cb_wrap)
        cw.setContentsMargins(0, 8, 0, 0)
        cw.addWidget(self.chatbox_tab)
        from earshot_tab import EarshotTab
        self.earshot_tab = EarshotTab(self.s, LevelMeter)
        self.earshot_tab.changed.connect(self.on_earshot_changed)
        self.earshot_tab.smart_changed.connect(
            lambda: self.engine and self.engine.load_smart_async())
        self.earshot_tab.install_smart.connect(
            lambda: self.engine and self.engine.install_smart_async())
        self.earshot_tab.add_room_route.connect(self.on_add_room_routes)
        self.bridge.heard.connect(self.earshot_tab.add_line)
        self.bridge.earshot.connect(self.earshot_tab.set_status)
        self.bridge.room_level.connect(lambda lv, v: self.earshot_tab.meter.set(lv, v))
        self.bridge.smart.connect(self.earshot_tab.update_smart)
        self.tabs.addTab(speech_tab, "🗣  Speech && translation")
        self.tabs.addTab(cb_wrap, "💬  Chatbox")
        self.tabs.addTab(self.earshot_tab, "🎧  Earshot")
        if self.s.earshot.get("enabled"):
            self.tabs.setTabText(2, "🎧  Earshot ●")
        right = sp                      # the speech widgets below go into the first tab
        self.routes = RoutesPanel(self.s.routes)
        self.routes.changed.connect(self.on_routes_changed)
        right.addWidget(self.routes)
        self.meter = LevelMeter()
        right.addWidget(self.meter)

        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self._style_transcript()
        self.transcript.setPlaceholderText("Start talking - your sentences show up here.")
        right.addWidget(self.transcript, 3)

        send_row = QHBoxLayout()
        self.manual = QLineEdit(placeholderText="Type a message to send to the chatbox…")
        self.manual.returnPressed.connect(self.on_manual_send)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.on_manual_send)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.on_clear)
        save_btn = QPushButton("Save…")
        save_btn.setToolTip("Save the transcript as a text file")
        save_btn.clicked.connect(self.on_save_transcript)
        send_row.addWidget(self.manual, 1)
        send_row.addWidget(send_btn)
        send_row.addWidget(save_btn)
        send_row.addWidget(clear_btn)
        right.addLayout(send_row)

        main_right = QVBoxLayout()
        main_right.setSpacing(6)
        main_right.addWidget(self.tabs, 1)
        logbar = QHBoxLayout()
        self.log_toggle = QPushButton("▸ Log")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setChecked(bool(self.s.extra.get("show_log", False)))
        self.log_toggle.setToolTip("Show or hide the app's messages")
        self.log_last = QLabel(objectName="hint")
        self.log_last.setTextFormat(Qt.PlainText)
        logbar.addWidget(self.log_toggle)
        logbar.addWidget(self.log_last, 1)
        main_right.addLayout(logbar)
        main_right.addWidget(self.logbox)
        self.log_toggle.toggled.connect(self.on_log_toggled)
        self.on_log_toggled(self.log_toggle.isChecked(), save=False)
        body.addLayout(main_right, 1)

    def _slider(self, lo, hi, val, step=1):
        sl = QSlider(Qt.Horizontal, minimum=lo, maximum=hi, value=val)
        sl.setSingleStep(step)
        sl.setPageStep(step * 5)
        return sl

    def _with_value(self, slider, label):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(slider, 1)
        label.setFixedWidth(52)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(label)
        return w

    def _check(self, text, attr):
        cb = QCheckBox(text, checked=bool(getattr(self.s, attr)))

        def changed(on):
            setattr(self.s, attr, bool(on))
            self.save()
            if attr == "oscquery" and self.engine:
                self.engine.osc.restart_listener()
                self.engine.osc.start_finder()
            if attr == "follow_vrc_mute":
                if self.engine:
                    self.engine.set_follow_mute(bool(on))
                else:
                    self.on_vrc_state("off", False)
            if attr == "filters_enabled":
                self.apply_filters()
            if attr in ("nllb_for_english", "translate_typed"):
                if self.engine:
                    self.engine.load_translator_async()
                self.update_translator_ui()
        cb.toggled.connect(changed)
        return cb

    @staticmethod
    def _shrink(combo):
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(8)
        return combo

    @staticmethod
    def _select(combo, value):
        i = combo.findData(value)
        if i >= 0:
            combo.setCurrentIndex(i)

    # --------------------------------------------------------------- logic
    def save(self):
        self._save_timer.start()

    def fill_mics(self):
        current = self.s.mic_device
        self.mic.blockSignals(True)
        self.mic.clear()
        self.mic.addItem("System default", None)
        try:
            for name in engine.list_microphones():
                self.mic.addItem(name, name)
        except Exception as e:
            self.add_log(f"Can't list microphones: {e}")
        self._select(self.mic, current)
        self.mic.blockSignals(False)

    def start_engine(self):
        try:
            self.engine = engine.Engine(
                self.s,
                on_log=self.bridge.log.emit,
                on_result=self.bridge.result.emit,
                on_level=self.bridge.level.emit,
                on_state=self.bridge.state.emit,
                on_translator=self.bridge.translator.emit,
                on_vrc=self.bridge.vrc.emit,
                on_preview=self.bridge.preview.emit,
                on_heard=self.bridge.heard.emit,
                on_earshot=self.bridge.earshot.emit,
                on_room_level=self.bridge.room_level.emit,
                on_smart=self.bridge.smart.emit,
            )
        except Exception as e:
            self.add_log(f"Engine failed to start: {e}")
            self.pill.set_state("error")
            return
        self.chatbox_tab.set_composer(self.engine.chatbox)
        self.earshot_tab.set_engine(self.engine)
        self.gpu_mon.start(self.engine)
        self.engine.load_model_async()
        self.engine.load_translator_async()
        if self.nllb_needed():
            QTimer.singleShot(600, self.maybe_offer_install)
        self.restart_audio()

    def restart_audio(self):
        if not self.engine:
            return
        try:
            self.engine.start_audio()
        except Exception as e:
            self.add_log(f"Microphone error: {e}")

    def on_mic_changed(self):
        self.s.mic_device = self.mic.currentData()
        self.save()
        self.restart_audio()

    def on_sens(self, v, save=True):
        self.s.vad_threshold = v / 100
        self.sens_lbl.setText(f"{v / 100:.2f}")
        if save:
            self.save()

    def on_pause(self, v, save=True):
        v = int(round(v / 50) * 50)
        self.s.end_silence_ms = v
        self.pause_lbl.setText(f"{v} ms")
        if save:
            self.save()

    def on_maxlen(self, v, save=True):
        self.s.max_utterance_s = float(v)
        self.maxlen_lbl.setText(f"{v} s")
        if save:
            self.save()

    def on_simple_change(self):
        self.s.beam_size = self.speed.currentData()
        self.save()

    # ---- languages / translation
    def on_routes_changed(self):
        needed_before = self.nllb_needed()
        self.s.routes = self.routes.routes()
        self.save()
        if self.engine:
            self.engine.load_translator_async()
        self.update_translator_ui()
        if not needed_before and self.nllb_needed():
            self.maybe_offer_install()

    def open_setup(self):
        import subprocess
        here = Path(__file__).resolve().parent
        exe = Path(sys.executable)
        if paths.IS_WINDOWS:
            pyw = exe.with_name("pythonw.exe")
            exe = pyw if pyw.exists() else exe
        try:
            subprocess.Popen([str(exe), str(here / "setup_gui.py"), "--modify"], cwd=str(here),
                             **paths.hidden_subprocess_kwargs())
            self.add_log("Setup opened. Restart Chatbox Studio after adding or removing parts.")
        except Exception as e:
            self.add_log(f"Couldn't open the setup: {e}")

    def on_earshot_changed(self):
        self.save()
        if self.engine:
            self.engine.earshot.apply()
            self.engine.load_translator_async()
        on = bool(self.s.earshot.get("enabled"))
        self.tabs.setTabText(2, "🎧  Earshot ●" if on else "🎧  Earshot")

    def on_add_room_routes(self):
        routes = self.routes.routes()
        srcs = list(dict.fromkeys(r["src"] for r in routes if r.get("enabled", True))) or ["en"]
        have = {(r["src"], r["dst"]) for r in routes}
        added = [{"src": s_, "dst": "room", "enabled": True} for s_ in srcs
                 if (s_, "room") not in have]
        if added:
            for r in routes:            # the room route replaces the fixed ones it overlaps
                if r["src"] in srcs and r["dst"] != r["src"]:
                    r["enabled"] = False
            self.routes.set_routes(routes + added)
            self.add_log("Added “→ Room language” routes; your other translation routes for "
                         "those languages are switched off (switch them back on any time).")
        self.tabs.setCurrentIndex(0)

    def nllb_needed(self) -> bool:
        return any(r["enabled"] and r["src"] != r["dst"] and r["dst"] != "en"
                   for r in self.s.routes) or (
            bool(self.s.earshot.get("enabled")) and bool(self.s.earshot.get("show_heard"))
            and self.s.earshot.get("show_in", "en") != "en")

    def maybe_offer_install(self):
        if (self.tr_state == "missing" and self.engine is not None
                and not getattr(self, "_offered", False)):
            self._offered = True
            self.offer_install()

    def on_tr_settings(self):
        changed = (self.tr_model.currentData() != self.s.translator_model
                   or self.tr_device.currentData() != self.s.translator_device)
        self.s.translator_model = self.tr_model.currentData()
        self.s.translator_device = self.tr_device.currentData()
        self.save()
        self.update_translator_ui()
        if changed and self.engine:
            self.engine.load_translator_async(force=self.engine.translator_needed())

    def on_join(self):
        self.s.join_with = self.join.currentData()
        self.save()

    def on_template(self, text):
        self.s.chatbox_template = text or "{text}"
        self.save()

    # ---- VRChat mute sync status ----------------------------------------------
    # ---- chatbox composer ---------------------------------------------------
    def _quick_signature(self):
        c = self.s.chatbox
        import blocks as B
        groups = set()
        for b in c.get("blocks", []):
            if not (b["type"] == "speech" and not b.get("enabled", True)):
                groups |= B.groups_of(b)
        return (c.get("hide_extras"), tuple(c.get("hidden_groups", [])), tuple(sorted(groups)))

    def on_chatbox_changed(self):
        self.save()
        if self.engine:
            self.engine.chatbox.changed()
        sig = self._quick_signature()
        if sig != self._quick_sig:
            self._quick_sig = sig
            QTimer.singleShot(0, self.quick.rebuild)

    def on_vrc_state(self, state: str, muted: bool):
        if not self.s.follow_vrc_mute:
            state = "off"
        self._vrc_state = state
        QTimer.singleShot(0, self.update_checklist)
        port = self.s.osc_listen_port
        text, color, tip = {
            "off": ("", "#7d8590", ""),
            "waiting": ("VRChat mute: waiting", "#7d8590",
                        "Listening for VRChat's mute state. It arrives the first time you "
                        "mute or unmute in VRChat (OSC must be enabled in VRChat)."),
            "ok": (("🔇 Muted in VRChat" if muted else "🎙 VRChat mic on"),
                   ("#e5a54b" if muted else "#3fb68b"),
                   "The app follows your VRChat mute button."),
            "busy": (f"⚠ Mute sync: port {port} busy", "#e5534b",
                     f"Another program (an OSC router like VOR, face tracking, ...) uses port "
                     f"{port}, so the app can't see when you mute in VRChat. Fix: turn on "
                     "'Find VRChat by itself' (Settings › VRChat & OSC), or add Chatbox Studio "
                     "to your OSC router and set 'Listen on port' to its port."),
            "oscquery": ("VRChat mute: waiting (OSCQuery)", "#7d8590",
                         f"Port {port} is used by another app, so Chatbox Studio listens on "
                         f"port {self.engine.osc.actual_port if self.engine else '?'} and told "
                         "VRChat via OSCQuery. Mute or unmute once in VRChat to check it "
                         "arrives. If it never does, add Chatbox Studio to your OSC router "
                         "instead."),
        }.get(state, ("", "#7d8590", ""))
        self.vrc_chip.setVisible(bool(text))
        self.vrc_chip.setText(text)
        self.vrc_chip.setToolTip(tip)
        c = QColor(color)
        self.vrc_chip.setStyleSheet(
            f"color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},0.12);"
            f" border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.35);"
            " border-radius: 11px; padding: 3px 10px; font-weight: 600;")

    # ---- translator status ------------------------------------------------
    def on_translator_state(self, state: str, pct: int):
        self.tr_state, self.tr_pct = state, pct
        self.update_translator_ui()

    def translator_relevant(self) -> bool:
        return (self.nllb_needed() or self.s.nllb_for_english
                or self.s.translate_typed)

    def update_translator_ui(self):
        if not hasattr(self, "tr_install"):
            return
        QTimer.singleShot(0, self.update_checklist)
        state, pct = self.tr_state, self.tr_pct
        if state not in ("downloading", "loading", "ready", "error"):
            state = "installed" if nllb.is_installed(self.s.translator_model) else "missing"
            self.tr_state = state
        needs = self.nllb_needed()
        lang = next((languages.name(r["dst"]) for r in self.s.routes
                     if r["enabled"] and r["src"] != r["dst"] and r["dst"] != "en"), "")

        status = {
            "missing": (f"⚠ Translating into {lang} needs the translator. Click Install (one time, "
                        f"~{nllb.REPOS[self.s.translator_model][1]} MB)." if needs else
                        "Translating into English needs no extra model. For routes into other "
                        "languages, install the translator (one time)."),
            "downloading": f"Downloading the translator… {pct}%",
            "loading": "Loading the translator…",
            "installed": "Translator installed. It loads when a route translates into a "
                         "language other than English.",
            "ready": "Translator ready.",
            "error": ("Translator failed - see the log. Try Install again." if paths.IS_WINDOWS
                      else "Translator failed - see the log. Try Install again, or run "
                           "setup_translator.sh in a terminal."),
        }[state]
        self.tr_status.setText(status)

        button = {"missing": "Install translator", "downloading": f"Downloading… {pct}%",
                  "loading": "Loading…", "installed": "Installed ✓", "ready": "Installed ✓",
                  "error": "Install again"}[state]
        self.tr_install.setText(button)
        self.tr_install.setEnabled(state in ("missing", "error"))
        self._set_primary(self.tr_install, state in ("missing", "error") and needs)

        # translator status chip in the header
        chip = {
            "missing": ("⚠  Install translator", "#e5a54b", True),
            "downloading": (f"Downloading translator {pct}%", "#c9a227", False),
            "loading": ("Loading translator…", "#c9a227", False),
            "installed": ("Translator idle", "#7d8590", False),
            "ready": ("✓ Translator ready", "#3fb68b", False),
            "error": ("⚠  Translator failed", "#e5534b", True),
        }[state]
        text, color, clickable = chip
        self.tr_chip.setText(text)
        c = QColor(color)
        self.tr_chip.setStyleSheet(
            f"QPushButton {{ color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},0.12);"
            f" border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.35); border-radius: 11px;"
            f" padding: 3px 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: rgba({c.red()},{c.green()},{c.blue()},0.22); }}")
        self.tr_chip.setEnabled(clickable)
        self.tr_chip.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.tr_chip.setVisible(self.translator_relevant())

    @staticmethod
    def _set_primary(btn, on: bool):
        btn.setObjectName("primary" if on else "")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def offer_install(self):
        size = nllb.REPOS[self.s.translator_model][1]
        langs = sorted({languages.name(r["dst"]) for r in self.s.routes
                        if r["enabled"] and r["src"] != r["dst"] and r["dst"] != "en"})
        answer = QMessageBox.question(
            self, "Install translator?",
            f"Translating into {', '.join(langs)} needs the translation model "
            f"(NLLB-200, about {size} MB, one-time download).\n\nInstall it now?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.on_install_translator()

    def on_install_translator(self):
        if self.engine is None:
            self.add_log("Can't install: the engine isn't running (see the errors above).")
            return
        self.engine.install_translator_async()

    def on_clear(self):
        self.transcript.clear()
        self.history.clear()

    def on_save_transcript(self):
        if not self.history:
            self.add_log("Nothing to save yet.")
            return
        default = str(Path.home() / f"vrc-transcript-{time.strftime('%Y-%m-%d_%H%M')}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save transcript", default, "Text (*.txt)")
        if path:
            Path(path).write_text("\n".join(self.history) + "\n", encoding="utf-8")
            self.add_log(f"Transcript saved to {path}")

    def apply_listen_port(self):
        self.s.osc_listen_port = int(self.lport.value())
        self.save()
        if self.engine:
            self.engine.osc.restart_listener()

    def on_port(self, v):
        self.s.osc_port = v
        self.save()
        if self.engine:
            self.engine.osc.retarget()

    def mark_reload(self):
        import devices
        changed = (self.gpu.currentData() != devices.speech_spec(self.s)
                   or self.model.currentData() != self.s.model)
        self.reload_btn.setText("Load model  (changed)" if changed else "Load model")

    def on_reload(self):
        import devices
        spec = self.gpu.currentData()
        self.s.speech_device = spec
        vendor, idx = devices.parse(spec)
        self.s.gpu_index = idx if vendor != "cpu" else -1
        self.s.model = self.model.currentData()
        self.save()
        self.reload_btn.setText("Load model")
        if self.engine:
            self.engine.load_model_async()

    def update_filter_summary(self):
        rep = sum(1 for r in self.s.replace_rules if r.get("enabled", True))
        blk = sum(1 for r in self.s.block_rules if r.get("enabled", True))
        bits = [f"{rep} replacement{'s' * (rep != 1)}", f"{blk} blocked phrase{'s' * (blk != 1)}"]
        if self.s.remove_fillers:
            bits.append("fillers removed")
        if self.s.min_words:
            bits.append(f"min {self.s.min_words} words")
        self.filter_summary.setText("  ·  ".join(bits))

    def apply_filters(self):
        self.update_filter_summary()
        if self.engine:
            self.engine.update_filters()

    def on_edit_filters(self):
        dlg = FiltersDialog(self.s, self)
        if dlg.exec():
            new = dlg.collect()
            for key in ("replace_rules", "block_rules", "remove_fillers", "min_words", "filter_typed"):
                setattr(self.s, key, getattr(new, key))
            self.save()
            self.apply_filters()

    # ---- appearance -------------------------------------------------------
    def _style_transcript(self):
        pal = theme.palette(self.s.ui_theme)
        sc = self.s.ui_scale
        self.transcript.document().setDefaultStyleSheet(
            f".row {{ margin: 0 0 10px 0; }} .badge {{ color: {pal['muted']}; "
            f"font-size: {round(11 * sc)}px; }} .text {{ font-size: {round(15 * sc)}px; "
            f"color: {pal['text']}; }} .meta {{ color: {pal['faint']}; "
            f"font-size: {round(11 * sc)}px; }}")

    def on_gpu_all_toggled(self, on):
        self.s.extra["gpu_monitor_all"] = bool(on)
        self.save()
        self.gpu_mon.set_show_all(bool(on))

    def on_gpu_mon_toggled(self, on):
        self.s.extra["hide_gpu_monitor"] = not on
        self.save()
        self.gpu_title.setVisible(on)
        self.gpu_mon.setVisible(on)

    def on_appearance(self, *_):
        self.s.ui_theme = self.theme_box.currentData()
        self.s.ui_scale = float(self.scale_box.currentData())
        self.save()
        self.apply_appearance()

    def apply_appearance(self):
        QApplication.instance().setStyleSheet(theme.build_style(self.s.ui_theme, self.s.ui_scale))
        self.meter.theme = self.s.ui_theme
        self._style_transcript()
        if hasattr(self, "gpu_mon"):
            self.gpu_mon.accent = theme.palette(self.s.ui_theme)["accent"]
            for row in self.gpu_mon.rows.values():
                row.accent = self.gpu_mon.accent
        self.panel.setFixedWidth(int(360 * min(1.35, max(1.0, self.s.ui_scale))))

    def _shortcuts(self):
        from PySide6.QtGui import QKeySequence, QShortcut
        def sc(keys, fn):
            QShortcut(QKeySequence(keys), self, activated=fn)
        sc("Ctrl+P", self.listen_btn.toggle)
        sc("Ctrl+H", lambda: self.quick._hide_all(not self.s.chatbox.get("hide_extras")))
        sc("Ctrl+L", lambda: self.engine and self.engine.chatbox.clear())
        sc("Ctrl+1", lambda: self.tabs.setCurrentIndex(0))
        sc("Ctrl+2", lambda: self.tabs.setCurrentIndex(1))
        sc("Ctrl+3", lambda: self.tabs.setCurrentIndex(2))
        sc("Ctrl+,", lambda: self.open_settings())
        sc("F1", self.help_btn.showMenu)
        self.listen_btn.setToolTip("Pause or resume listening (Ctrl+P)")

    def on_listen_toggled(self, on):
        self.listen_btn.setText("Pause" if on else "Resume")
        if self.engine:
            self.engine.set_enabled(on)
        else:
            self.pill.set_state("ready" if on else "paused")

    def on_manual_send(self):
        text = self.manual.text().strip()
        if not text:
            return
        sent, blocked = (self.engine.send_manual(text) if self.engine else (text, ""))
        if blocked:
            self.add_log(f"Typed message not sent ({blocked}): {text!r}")
        else:
            self.add_row("TYPED", [("", line, "", False) for line in sent.split("\n")])
        self.manual.clear()

    def add_result(self, r: Result):
        if r.dropped and not r.filtered:
            self.add_log(f"Ignored ({r.reason}): {r.text!r}")
            return
        if r.dropped:
            self.add_log(f"Blocked by filter ({r.reason}): {r.original or r.text!r}")
            return
        badge = r.language.upper()
        if r.lang_prob < 0.999:
            badge += f"  ·  {r.lang_prob:.0%} sure"
        lines = []
        for o in r.outputs:
            note = o.note or (f"blocked by filter: {o.blocked}" if o.blocked else "")
            if o.raw and o.raw != o.text and not o.blocked:
                note = (note + "  ·  " if note else "") + f"before filters: {o.raw}"
            lines.append((o.dst.upper() + (" ✨" if getattr(o, "smart", False) else ""),
                          o.text, note, bool(o.blocked)))
        meta = f"{r.latency_ms:.0f} ms for {r.audio_s:.1f} s of speech"
        if any(o.translated for o in r.outputs) and r.original:
            meta += f"  ·  you said: {r.original}"
        self.add_row(badge, lines, meta)

    def add_row(self, badge, lines, meta=""):
        """lines: [(target_tag, text, note, blocked)]"""
        stamp = time.strftime("%H:%M:%S")
        body = []
        for tag, text, note, blocked in lines:
            t = html.escape(text)
            if blocked:
                t = f'<span style="color:#6b737e; text-decoration:line-through">{t}</span>'
            tag_html = (f'<span style="color:#4c9cff; font-size:11px; font-weight:600">'
                        f'{html.escape(tag)}&nbsp;&nbsp;</span>') if tag else ""
            body.append(f'{tag_html}<span class="text">{t}</span>')
            if note:
                body.append(f'<span style="color:#e5a54b; font-size:11px">⚠ {html.escape(note)}</span>')
            self.history.append(f"[{stamp}] {badge} -> {tag or '-'}: {text}"
                                + (f"   [{note}]" if note else ""))
        if meta:
            self.history[-1] += f"   ({meta})"
        self.transcript.append(
            f'<div class="row"><span class="badge">{stamp}  ·  {html.escape(badge)}</span><br>'
            + "<br>".join(body)
            + (f'<br><span class="meta">{html.escape(meta)}</span>' if meta else "")
            + "</div>")
        sb = self.transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    def add_log(self, msg):
        self.logbox.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")
        if hasattr(self, "log_last"):
            self.log_last.setText(str(msg).splitlines()[0][:160] if msg else "")

    # ---- settings, help, tour ------------------------------------------------
    def open_settings(self, page=None):
        self.settings_dlg.open_page(page)

    def on_log_toggled(self, on, save=True):
        self.logbox.setVisible(on)
        self.log_toggle.setText("▾ Log" if on else "▸ Log")
        self.log_last.setVisible(not on)
        if save:
            self.s.extra["show_log"] = bool(on)
            self.save()

    def on_tips_toggled(self, on):
        self.s.extra["hide_checklist"] = not on
        self.save()
        self.update_checklist()

    def _open_path(self, p):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def open_readme(self):
        for cand in (paths.data_dir() / "README.md", paths.data_dir() / "README.txt",
                     APP_DIR.parent / "README.md", APP_DIR.parent / "docs" / "README.md"):
            if cand.exists():
                return self._open_path(cand)
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl("https://github.com/LuminoOwO/chatbox-studio#readme"))

    def copy_log(self):
        text = (f"{APP_NAME} {__version__} on {sys.platform}\n" + self.logbox.toPlainText())
        QApplication.clipboard().setText(text)
        self.add_log("Log copied - paste it into your bug report.")

    def show_quick_start(self):
        from ui_help import QuickStart
        dlg = QuickStart(self)
        ok = dlg.exec()
        self.s.extra["onboarded"] = True
        self.save()
        if ok and dlg.tour.isChecked():
            QTimer.singleShot(200, self.start_tour)

    def start_tour(self):
        from ui_help import Tour
        if getattr(self, "_tour", None) is not None:
            return
        self.tabs.setCurrentIndex(0)

        def tab(i):
            def get():
                self.tabs.setCurrentIndex(i)
                return self.tabs.tabBar()
            return get
        steps = [
            (lambda: self.pill, "Status",
             "Shows what the app is doing: loading the model, ready, hearing you, working. "
             "Green “Ready” means: just talk."),
            (lambda: self.listen_btn, "Pause",
             "Stops listening (Ctrl+P). With “Pause when muted in VRChat” it also pauses by "
             "itself when you mute in VRChat."),
            (lambda: self.panel, "Quick controls",
             "The things you change while playing: microphone, how sensitive it is, how long "
             "a pause ends a sentence. The checklist on top shows what's still missing."),
            (lambda: self.routes, "Translation routes",
             "Each row means: when I speak THIS language, the chatbox gets THAT one. Several "
             "routes work at the same time. Presets has common setups."),
            (lambda: self.transcript, "What you said",
             "Every sentence shows up here with its translation and how long it took. You can "
             "also type a message below and send it."),
            (tab(1), "Chatbox tab",
             "Build your chatbox from blocks: your speech, a clock, the song you're playing, "
             "PC stats… Drag them into the preview, which looks like the real chatbox."),
            (tab(2), "Earshot tab",
             "Optional: the app also listens to the game, so translations fit the "
             "conversation and you can read what others say."),
            (lambda: self.quick, "Privacy",
             "Hide parts of your chatbox right away, or click “Hide extras” to show only "
             "your speech (Ctrl+H)."),
            (lambda: self.settings_btn, "Settings",
             "Everything else: theme and text size, the speech model and graphics card, the "
             "translator, VRChat ports, filters, and adding or removing parts (Ctrl+,)."),
            (lambda: self.help_btn, "Help",
             "Brings back the quick start and this tour, and lists the keyboard shortcuts (F1)."),
        ]
        self._tour = Tour(self, steps)

        def done():
            self._tour = None
            self.tabs.setCurrentIndex(0)
            self.s.extra["toured"] = True
            self.save()
        self._tour.finished.connect(done)
        self._tour.start()

    _heard_voice = False

    def _on_level_for_checklist(self, lv, voice):
        if voice and not self._heard_voice:
            self._heard_voice = True
            self.update_checklist()

    def update_checklist(self):
        cl = getattr(self, "checklist", None)
        if cl is None:
            return
        if self.s.extra.get("hide_checklist"):
            cl.setVisible(False)
            return
        state = getattr(self, "_pill_state", "loading")
        model_ok = state not in ("loading", "error")
        cl.set_item("model", model_ok,
                    "Speech model loaded" if model_ok else
                    ("Speech model failed to load" if state == "error" else
                     "Loading the speech model… (the first start downloads it)"),
                    "Settings", lambda: self.open_settings("speech"))
        cl.set_item("mic", self._heard_voice,
                    "The microphone hears you" if self._heard_voice else
                    "Say something - the bar under the routes should turn blue",
                    "Pick mic", lambda: self.mic.showPopup())
        vrc = getattr(self, "_vrc_state", "off")
        cl.set_item("vrchat", vrc == "ok",
                    "VRChat is connected" if vrc == "ok" else
                    "Connect VRChat: Action Menu → Options → OSC → Enabled, then mute and unmute "
                    "once",
                    "How?", lambda: self.open_settings("vrchat"),
                    visible=self.s.follow_vrc_mute)
        need_tr = self.nllb_needed()
        cl.set_item("translator", self.tr_state == "ready",
                    "Translator ready" if self.tr_state == "ready" else
                    "Your routes need the translator", "Install",
                    self.on_install_translator if self.tr_state in ("missing", "error",
                                                                    "installed") else None,
                    visible=need_tr)
        done = all(lb.text().startswith("✅") or not lb.isVisible()
                   for lb, _b, _r in cl.rows.values())
        if done and not cl.isHidden():
            if not hasattr(self, "_done_timer"):
                self._done_timer = QTimer(self, singleShot=True, interval=8000,
                                          timeout=lambda: cl.setVisible(False))
            self._done_timer.start()
        else:
            cl.setVisible(True)

    def closeEvent(self, e):
        save_settings(self.s)
        if self.engine:
            self.engine.stop_audio()
            try:
                self.engine.osc._stop_server()      # withdraw the OSCQuery announcement
                self.engine.osc.stop_finder()
                self.engine.earshot.stop()
            except Exception:
                pass
        try:
            self.gpu_mon.stop()
        except Exception:
            pass
        try:
            import workers
            workers.stop_all()                      # the AMD helper process, if any
        except Exception:
            pass
        super().closeEvent(e)


def install_crash_handler():
    """Without a console (Windows shortcut) errors would vanish: log them and show them."""
    import traceback

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            paths.crash_log().parent.mkdir(parents=True, exist_ok=True)
            with open(paths.crash_log(), "a", encoding="utf-8") as f:
                f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} v{__version__}\n{text}")
        except OSError:
            pass
        sys.__excepthook__(etype, value, tb)
        if QApplication.instance():
            QMessageBox.critical(None, APP_NAME, f"Something went wrong:\n\n{value}\n\n"
                                 f"Details were saved to:\n{paths.crash_log()}")
    sys.excepthook = hook


INSTANCE_KEY = "chatbox-studio-" + (os.environ.get("USER") or os.environ.get("USERNAME") or "user")


def starts_log() -> Path:
    return paths.data_dir() / "starts.log"


def _who(pid: int) -> str:
    """'name (pid) <- parent (pid) <- ...' on Linux, for the starts log."""
    chain = []
    for _ in range(6):
        if pid <= 1:
            break
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace").strip()
            stat = Path(f"/proc/{pid}/stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        chain.append(f"{cmd[:120] or '?'} ({pid})")
        pid = ppid
    return " <- ".join(chain) or (f"pid {pid} (already closed)" if pid > 1 else "unknown")


def log_start(event: str, pid: Optional[int] = None):
    """Writes who started (or raised) the app to starts.log - for 'it starts by itself'."""
    try:
        import datetime
        line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {event}"
        if not paths.IS_WINDOWS:
            line += f"\n    by: {_who(pid or os.getpid())}"
            hints = {k: os.environ[k] for k in ("DESKTOP_AUTOSTART_ID", "INVOCATION_ID",
                                                  "SESSION_MANAGER", "XDG_ACTIVATION_TOKEN",
                                                  "DESKTOP_STARTUP_ID", "GIO_LAUNCHED_DESKTOP_FILE")
                     if os.environ.get(k) and pid is None}
            if hints:
                line += "\n    env: " + ", ".join(f"{k}={v[:60]}" for k, v in hints.items())
        f = starts_log()
        f.parent.mkdir(parents=True, exist_ok=True)
        old = f.read_text(errors="replace").splitlines()[-400:] if f.exists() else []
        f.write_text("\n".join(old + [line]) + "\n")
    except Exception:
        pass


def _peer_pid(conn) -> Optional[int]:
    """The process that connected to our single-instance socket (Linux)."""
    if paths.IS_WINDOWS:
        return None
    try:
        import socket
        import struct
        fd = int(conn.socketDescriptor())
        s = socket.socket(fileno=os.dup(fd))
        try:
            cred = s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        finally:
            s.close()
        return struct.unpack("3i", cred)[0]
    except Exception:
        return None


def already_running() -> bool:
    """If another copy is open, ask it to come to the front and return True."""
    from PySide6.QtNetwork import QLocalSocket
    for wait in (800, 2000):          # the open copy may be busy (loading a model)
        sock = QLocalSocket()
        sock.connectToServer(INSTANCE_KEY)
        if sock.waitForConnected(wait):
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            return True
        if sock.error() == QLocalSocket.ServerNotFoundError:
            break                     # nobody listening: no need to wait longer
    return False


def listen_for_second_start(win):
    """A second start (menu click, installer) brings this window to the front instead."""
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(INSTANCE_KEY)          # clears a stale socket after a crash
    server = QLocalServer(win)
    server.listen(INSTANCE_KEY)

    def on_conn():
        conn = server.nextPendingConnection()
        if conn:
            log_start("brought to the front by a second start", _peer_pid(conn) or -1)
            conn.disconnectFromServer()
        win.showNormal()
        win.raise_()
        win.activateWindow()
    server.newConnection.connect(on_conn)
    return server


def install_main_thread_gc(app):
    """Python's automatic garbage collection can start in any thread. If it starts in one
    of our background threads (speech, chatbox, media, stats) and frees Qt widgets there,
    Qt crashes. So automatic collection is switched off and done here, on the GUI thread."""
    import gc
    gc.disable()
    t0, t1, t2 = gc.get_threshold()

    def tick():
        c0, c1, c2 = gc.get_count()
        if c2 >= t2:
            gc.collect(2)
        elif c1 >= t1:
            gc.collect(1)
        elif c0 >= t0:
            gc.collect(0)
    timer = QTimer(app, interval=250, timeout=tick)
    timer.start()
    app._gc_timer = timer
    return timer


def main():
    install_crash_handler()
    if paths.IS_WINDOWS:
        try:   # own taskbar icon instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ChatboxStudio")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    install_main_thread_gc(app)
    app.setDesktopFileName("chatbox-studio")
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "icon.png")))
    if already_running():
        log_start("second start - the open window was brought to the front")
        return                      # the open copy came to the front
    log_start("started" + (f" with {' '.join(sys.argv[1:])}" if sys.argv[1:] else ""))
    notes = []
    paths.migrate_from_old_name(notes.append)
    settings = load_settings()
    try:
        load_fonts()
    except Exception:
        pass
    app.setStyleSheet(theme.build_style(settings.ui_theme, settings.ui_scale))
    win = Window(settings)
    for n in notes:
        win.add_log(n)
    win._instance_server = listen_for_second_start(win)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
