"""
Chatbox Studio Setup - pick which parts to install (Windows and Linux).

  setup_gui.py                 the setup window (first install / add or remove parts)
  setup_gui.py --update        the same, after an update (your current parts are ticked)
  setup_gui.py --defaults      no window: installs the recommended parts without models
                               (used when there is no display, or for unattended installs)

The installer scripts (install.sh / Install.bat) set up Python and this window's own
packages first, then start it.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import components as K  # noqa: E402
import paths  # noqa: E402


def mb(n: float) -> str:
    return f"{n / 1024:.1f} GB" if n >= 1000 else f"{int(n)} MB"


# --------------------------------------------------------------------------- #
# Without a window
# --------------------------------------------------------------------------- #
def run_cli(update: bool) -> int:
    import subprocess
    hw = K.detect()
    have = K.current(hw)
    want = K.recommend(hw, K.load_state())
    if not K.load_state():
        want.nllb = None                    # no big downloads without asking
    steps = [s for s in K.plan(hw, want, have, update) if s.kind != "task" or
             "check" in s.cmd]
    print(f"Chatbox Studio setup - {len(steps)} steps (no models; the app downloads the "
          "speech model on first start, the translator with one click).")
    for s in steps:
        print(f"\n==> {s.label}", flush=True)
        r = subprocess.run(s.cmd)
        if r.returncode == 4:
            want.groups.discard("rocm")
            print("AMD: the ROCm 7.2 system libraries are missing. Install them with\n"
                  f"  bash {Path(__file__).resolve().parent / 'tools' / 'install_rocm.sh'}\n"
                  "then run chatbox-studio-setup. Until then the app runs on the CPU.")
            continue
        if r.returncode == 3:
            print("Windows Smart App Control blocked a library - see README.txt.")
            return 3
        if r.returncode != 0 and s.kind != "task":
            print(f"ERROR: {s.label} failed.")
            return 1
    K.save_state(want)
    K.apply_settings(want)
    return 0


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #
def run_gui(mode: str) -> int:
    from PySide6.QtCore import QProcess, Qt, QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame,
                                   QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                                   QProgressBar, QPushButton, QRadioButton, QScrollArea,
                                   QStackedWidget, QVBoxLayout, QWidget)
    import theme
    from version import APP_NAME, __version__
    from PySide6.QtGui import QColor, QPainter

    class MemBar(QWidget):
        """Graphics memory of one card: grey = other programs now (VRChat, desktop...),
        colour = what Chatbox Studio will use, rest = free."""
        COL = {"ok": "#3fb950", "tight": "#e5a54b", "no": "#f85149"}

        def __init__(self):
            super().__init__()
            self.setFixedHeight(12)
            self.total, self.others, self.need, self.state = 1, 0, 0, "ok"

        def set(self, total, others, need, state):
            self.total, self.others, self.need, self.state = max(1, total), others, need, state
            self.update()

        def paintEvent(self, _e):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            r = self.rect()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(127, 127, 127, 60))
            p.drawRoundedRect(r, 5, 5)
            w = r.width()
            x1 = int(w * min(1.0, self.others / self.total))
            x2 = int(w * min(1.0, (self.others + self.need) / self.total))
            if x2 > 0:
                p.setBrush(QColor(self.COL[self.state]))
                p.drawRoundedRect(0, 0, x2, r.height(), 5, 5)
            if x1 > 0:
                p.setBrush(QColor("#8b949e"))
                p.drawRoundedRect(0, 0, x1, r.height(), 5, 5)
            p.end()

    app = QApplication(sys.argv)
    app.setApplicationName(f"{APP_NAME} Setup")
    app.setDesktopFileName("chatbox-studio")
    ui_theme, scale = "dark", 1.0
    try:
        import json
        cfg = json.loads(K.CONFIG.read_text())
        ui_theme, scale = cfg.get("ui_theme", "dark"), float(cfg.get("ui_scale", 1.0))
    except Exception:
        pass
    app.setStyleSheet(theme.build_style(ui_theme, scale))
    icon = Path(__file__).resolve().parent / "assets" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    hw = K.detect()
    have = K.current(hw)
    want = K.recommend(hw, K.load_state())
    first = K.load_state() is None and mode != "update"

    def hint(text):
        lb = QLabel(text, objectName="hint", wordWrap=True)
        lb.setTextFormat(Qt.PlainText)
        return lb

    def section(text):
        return QLabel(text, objectName="section")

    win = QWidget(objectName="root")
    win.setAttribute(Qt.WA_StyledBackground, True)
    win.setWindowTitle(f"{APP_NAME} {__version__} - Setup")
    win.resize(900, 820)
    outer = QVBoxLayout(win)
    outer.setContentsMargins(18, 14, 18, 14)
    title = QLabel(f"{APP_NAME} Setup", objectName="title")
    title.setStyleSheet("font-size: 20px;")
    outer.addWidget(title)
    sub = hint({"update": f"Updated to {__version__}. Click Install to finish - or change "
                          "your parts first.",
                "modify": "Add or remove parts. Your settings stay as they are."}.get(
        mode, "Speak in VRChat - it shows up in your chatbox, translated if you like. Runs on "
              "your PC; nothing is sent to the internet."))
    outer.addWidget(sub)
    pages = QStackedWidget()
    outer.addWidget(pages, 1)

    import gpus as GP
    state0 = K.load_state()

    def short_name(g):
        return g.name.replace("NVIDIA GeForce ", "").replace("AMD Radeon ", "Radeon ")

    def pc_line() -> str:
        seen, cards = set(), []
        for g in hw.gpus:                    # one entry per chip, even with two engines
            key = (g.name, g.device_id or g.name)
            if key in seen or any(g.name == x.name for x in hw.gpus if x is not g and
                                  x.vendor != "vulkan" and g.vendor == "vulkan"):
                continue
            seen.add(key)
            cards.append(f"{short_name(g)} ({g.vram_mb / 1024:.0f} GB"
                         + (" shared" if g.integrated else "") + ")")
        if not cards:
            cards = ["no graphics chip the AI can use - it runs on the CPU (slower)"]
        return (f"Your PC: {', '.join(cards)} · {hw.ram_gb:.0f} GB RAM · "
                f"{hw.free_gb:.0f} GB free disk")

    # ---------------------------------------------------------------- page 0: simple
    p0 = QWidget()
    l0 = QVBoxLayout(p0)
    l0.setContentsMargins(0, 8, 0, 0)
    l0.setSpacing(10)
    l0.addWidget(hint(pc_line()))
    l0.addWidget(section("WHAT DO YOU WANT TO INSTALL?"))
    pgroup = QButtonGroup(win)
    cards = {}

    def card(key, title_, desc):
        fr = QFrame(objectName="panel")
        fl_ = QVBoxLayout(fr)
        fl_.setContentsMargins(14, 10, 14, 10)
        fl_.setSpacing(2)
        top = QHBoxLayout()
        rb = QRadioButton(title_)
        f = rb.font()
        f.setWeight(QFont.DemiBold)
        f.setPointSizeF(f.pointSizeF() * 1.1)
        rb.setFont(f)
        size = QLabel(objectName="hint")
        top.addWidget(rb, 1)
        top.addWidget(size)
        fl_.addLayout(top)
        d = hint(desc)
        d.setContentsMargins(26, 0, 0, 0)
        fl_.addWidget(d)
        what = QLabel(objectName="hint", wordWrap=True)
        what.setContentsMargins(26, 0, 0, 0)
        fl_.addWidget(what)
        fr.mousePressEvent = lambda _e, r=rb: r.setChecked(True)
        pgroup.addButton(rb)
        l0.addWidget(fr)
        cards[key] = (rb, size, what)

    if state0:
        card("keep", "Keep what I have", "Update the app, keep your parts and models.")
    card("standard", "Standard  (recommended)",
         "Speech recognition and the translator: speak in your language, the chatbox shows "
         "it in another one.")
    card("speech", "Speech only",
         "Smaller. Shows what you say as you say it, or translated into English - no "
         "translator for other languages (you can add it later).")
    card("custom", "Custom…",
         "Choose every part yourself: speech model, translator, which graphics card runs "
         "what, extras and the experimental Earshot.")
    l0.addStretch()
    pages.addWidget(p0)

    # ---------------------------------------------------------------- page 1: custom
    p1 = QWidget()
    scroll = QScrollArea(widgetResizable=True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    inner = QWidget(objectName="inner")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(4, 4, 12, 4)
    lay.setSpacing(7)
    scroll.setWidget(inner)
    p1l = QVBoxLayout(p1)
    p1l.setContentsMargins(0, 0, 0, 0)
    back_row = QHBoxLayout()
    back_btn = QPushButton("‹ Back to the simple choice")
    back_row.addWidget(back_btn)
    back_row.addStretch()
    p1l.addLayout(back_row)
    p1l.addWidget(scroll)
    lay.addWidget(hint(pc_line()))

    def dev_combo(current, same_label=None, ct2=False):
        """ct2: for the translator / smart AI, which can't use Vulkan devices."""
        box = QComboBox()
        if same_label:
            box.addItem(same_label, "same")
        for g in hw.gpus:
            if ct2 and g.vendor == "vulkan":
                continue
            eng = {"nvidia": "NVIDIA CUDA", "amd": "AMD ROCm", "vulkan": "Vulkan"}[g.vendor]
            mem = f"{g.vram_mb / 1024:.0f} GB" + (" shared" if g.integrated else "")
            box.addItem(f"{short_name(g)} ({mem}, {eng})", K.gpu_key(g))
            if g.vendor == "vulkan":
                box.setItemData(box.count() - 1, "Vulkan (whisper.cpp): works with the normal "
                                "graphics driver on any chip. Speech recognition only - the "
                                "translator then runs on the CPU.", Qt.ToolTipRole)
        box.addItem("CPU (no graphics card)", "cpu")
        box.setCurrentIndex(max(0, box.findData(current)))
        return box

    def dev_row(text, box):
        row = QHBoxLayout()
        lbl = QLabel(text)
        lbl.setMinimumWidth(150)
        row.addWidget(lbl)
        row.addWidget(box, 1)
        lay.addLayout(row)
        return lbl

    checks = {}

    def group_box(key):
        g = K.GROUP[key]
        row = QHBoxLayout()
        cb = QCheckBox(g.label, checked=g.key in want.groups)
        cb.setToolTip(g.desc)
        if g.required:
            cb.setEnabled(False)
            cb.setChecked(True)
        row.addWidget(cb, 1)
        state = "installed" if g.key in have.groups and K.group_installed(g.key) else ""
        size = QLabel((f"{state} · " if state else "") + (mb(g.size_mb) if g.size_mb else ""),
                      objectName="hint")
        row.addWidget(size)
        lay.addLayout(row)
        checks[key] = cb
        return cb, size

    # speech
    lay.addWidget(section("SPEECH RECOGNITION"))
    wgroup = QButtonGroup(win)
    wrows = {}
    for name, label, size, vram in K.WHISPER:
        rb = QRadioButton(f"{label}   ·   {mb(size)}, ~{vram / 1024:.1f} GB memory")
        rb._sizes = (label, size, vram)
        rb.setChecked(name == want.whisper)
        wgroup.addButton(rb)
        wrows[name] = rb
        lay.addWidget(rb)
    dev = dev_combo(want.gpu)
    dev_row("Runs on", dev)

    # translator
    lay.addWidget(section("TRANSLATOR"))
    nllb_on = QCheckBox("Translate into other languages",
                        checked=bool(want.nllb))
    lay.addWidget(nllb_on)
    nllb_box = QComboBox()
    for name, label, size, _v in K.NLLB:
        nllb_box.addItem(f"{label}   ·   {mb(size)}", name)
    nllb_box.setCurrentIndex(max(0, nllb_box.findData(want.nllb or K.NLLB[0][0])))
    dev_row("Model", nllb_box)
    tr_dev = dev_combo(want.tr_dev, "Same as speech recognition", ct2=True)
    dev_row("Runs on", tr_dev)

    # graphics card support + extras
    lay.addWidget(section("GRAPHICS CARD SUPPORT"))
    for key, vendor in (("cuda", "nvidia"), ("rocm", "amd"), ("vulkan", "vulkan")):
        cb, size = group_box(key)
        if not any(x.vendor == vendor for x in hw.gpus):
            cb.setChecked(False)
            cb.setEnabled(False)
            cb.setVisible(False)
            size.setVisible(False)
    gpu_hint = hint("Ticked by itself for the cards you pick above."
                    + (" AMD ROCm on Linux needs AMD's ROCm 7.2 libraries (the setup helps) - "
                       "the Vulkan entry of the same card needs nothing extra."
                       if not paths.IS_WINDOWS and any(g.vendor == "amd" for g in hw.gpus)
                       else ""))
    lay.addWidget(gpu_hint)
    if not hw.gpus:
        gpu_hint.setText("No NVIDIA or AMD graphics card found - everything runs on the CPU.")

    lay.addWidget(section("EXTRAS FOR THE CHATBOX"))
    group_box("media")
    group_box("vr")
    group_box("core")                     # required, shown so its size is visible
    lay.addWidget(hint("Song and artist, which app you use, VR headset batteries - small, no "
                       "AI."))

    # earshot
    lay.addWidget(section("EARSHOT - LISTEN TO THE GAME (EXPERIMENTAL)"))
    group_box("earshot")
    lay.addWidget(hint("Hears what people around you say and shows it translated. "
                       "Experimental: its conversation features often make things worse, so "
                       "they're off unless you switch them on in the app."))
    listen_dev = dev_combo(want.listen_dev, "Share the speech model (waits while you talk)")
    dev_row("Game speech runs on", listen_dev)
    listen_model = QComboBox()
    for name, label, size, vram in K.WHISPER:
        listen_model.addItem(f"{label}   ·   ~{vram / 1024:.1f} GB memory", name)
    listen_model.setCurrentIndex(max(0, listen_model.findData(want.listen_model)))
    dev_row("…with the model", listen_model)
    smart_on = QCheckBox("Smart translation with a local AI (big download)",
                         checked=bool(want.smart))
    lay.addWidget(smart_on)
    smart_box = QComboBox()
    for name, label, size, vram in K.SMART:
        smart_box.addItem(f"{label}   ·   {mb(size)}, ~{vram / 1024:.1f} GB memory", name)
    smart_box.setCurrentIndex(max(0, smart_box.findData(want.smart or K.SMART[0][0])))
    dev_row("AI model", smart_box)
    smart_dev = dev_combo(want.smart_dev, "Same as speech recognition", ct2=True)
    dev_row("AI runs on", smart_dev)
    lay.addStretch()
    pages.addWidget(p1)

    # ---------------------------------------------------------------- always visible
    foot = QFrame(objectName="panel")
    fl = QVBoxLayout(foot)
    fl.setContentsMargins(12, 8, 12, 8)
    fl.setSpacing(4)

    def mem_row(label):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        name = QLabel(label)
        name.setFixedWidth(150)
        bar_ = MemBar()
        txt = QLabel(objectName="hint")
        txt.setMinimumWidth(330)
        row.addWidget(name)
        row.addWidget(bar_, 1)
        row.addWidget(txt)
        fl.addWidget(box)
        bar_.row = box
        return bar_, txt
    vram_rows = {}                       # one memory meter per graphics card
    for g in hw.gpus[:4]:
        short = g.name.replace("NVIDIA GeForce ", "").replace("AMD Radeon ", "Radeon ")
        vram_rows[K.gpu_key(g)] = (g, *mem_row(short[:22] if len(hw.gpus) > 1
                                               else "Graphics memory"))
    if not vram_rows:
        vram_rows["cpu"] = (None, *mem_row("Graphics memory"))
    vram_hint = QLabel(objectName="hint", wordWrap=True)
    vram_hint.setVisible(False)
    fl.addWidget(vram_hint)
    live = {"usage": {}, "vrchat": False, "busy": False}
    disk_bar, disk_txt = mem_row("Disk space")
    disk_lbl = QLabel(objectName="hint", wordWrap=True)     # simple view: one line
    fl.addWidget(disk_lbl)
    fit_lbl = QLabel(objectName="hint", wordWrap=True)
    fl.addWidget(fit_lbl)
    outer.addWidget(foot)
    used = {"total": None}

    def measure():
        import threading

        def work():
            used.update(K.used_space())
            QTimer.singleShot(0, win, refresh)
        threading.Thread(target=work, daemon=True).start()

    def measure_vram():
        """Live memory use of every card, every few seconds (in a thread)."""
        if live["busy"] or not hw.gpus:
            return
        live["busy"] = True
        import threading

        def work():
            try:
                import vram
                live["usage"] = vram.measure(hw.gpus)
                live["vrchat"] = vram.vrchat_running()
            except Exception:
                live["usage"] = {}
            live["busy"] = False
            QTimer.singleShot(0, win, lambda: update_mem(collect()))
        threading.Thread(target=work, daemon=True).start()

    def update_mem(c):
        usage = {k: u for k, u in live["usage"].items() if u.ok}
        state, text = K.fit(hw, c, usage)
        icon_ = {"ok": "✅", "tight": "⚠️", "no": "⛔"}[state]
        fit_lbl.setText(f"{icon_} {text}")
        per = c.per_gpu()
        cpu_mb = sum(mb_ for _r, _l, d, mb_ in c.roles() if d == "cpu")
        for key, (g, bar_, txt_) in vram_rows.items():
            parts = per.get(key, [])
            u = usage.get(key)
            if g is None:
                bar_.set(1, 0, 0, "ok")
                txt_.setText(f"none - runs on the CPU (uses ~{cpu_mb / 1024:.1f} GB RAM)")
                continue
            bar_.row.setVisible(bool(parts) or pages.currentIndex() == 1)
            need = sum(v for _n, v in parts)
            others = u.others_mb if u else 0
            st_ = K.fit_gpu(hw, key, parts, u)[0] if parts else "ok"
            bar_.set(g.vram_mb, others, need, st_)
            bits = []
            if u:
                vrc = u.vrchat_mb()
                bits.append(f"in use {others / 1024:.1f}" + (f" (VRChat {vrc / 1024:.1f})"
                                                             if vrc else ""))
            if parts:
                bits.append(f"Chatbox {need / 1024:.1f}")
            left = g.vram_mb - others - need
            bits.append(f"{max(0, left) / 1024:.1f} left of {g.vram_mb / 1024:.0f} GB"
                        if left >= 0 else f"{-left / 1024:.1f} GB short")
            txt_.setText("  ·  ".join(bits) if (u or parts) else "not used")
            tip = []
            if parts:
                tip.append("Chatbox Studio: " + ", ".join(
                    f"{n} {v / 1024:.1f} GB" for n, v in parts))
            if u:
                tip.append(f"In use right now: {u.used_mb / 1024:.1f} GB of "
                           f"{u.total_mb / 1024:.1f} GB")
                tip += [f"   {n}  {v / 1024:.2f} GB" for n, v in u.procs[:10]]
                rest = others - sum(v for _n, v in u.procs)
                if rest > 64:
                    tip.append(f"   desktop, drivers and others  {rest / 1024:.2f} GB")
                if u.ours_mb:
                    tip.append(f"Chatbox Studio running now: {u.ours_mb / 1024:.2f} GB "
                               "(counted as free - it's replaced)")
            bar_.setToolTip("\n".join(tip))
            txt_.setToolTip("\n".join(tip))
        if usage:
            vram_hint.setVisible(True)
            vram_hint.setText(
                "🎮 Measured live while VRChat runs (grey = other programs). Busy worlds use "
                "more - leave some room."
                if live["vrchat"] else
                "Grey = other programs right now. Tip: start VRChat while this window is open "
                "to see what's really left while you play.")
        else:
            vram_hint.setVisible(False)
        return state, text

    def collect() -> K.Choice:
        c = K.Choice()
        c.groups = {k for k, cb in checks.items() if cb.isChecked()} | {"core"}
        c.whisper = next(n for n, rb in wrows.items() if rb.isChecked())
        c.gpu = dev.currentData()
        c.tr_dev = tr_dev.currentData()
        c.smart_dev = smart_dev.currentData()
        c.listen_dev = listen_dev.currentData()
        c.listen_model = listen_model.currentData()
        c.nllb = nllb_box.currentData() if nllb_on.isChecked() else None
        c.smart = smart_box.currentData() if smart_on.isChecked() else None
        if c.smart:
            c.groups.add("earshot")
        if "nvidia" not in c.vendors():
            c.groups.discard("cuda")
        if "amd" not in c.vendors():
            c.groups.discard("rocm")
        return c

    def refresh(*_):
        if loading["on"]:
            return
        c = collect()
        for key, vendor in (("cuda", "nvidia"), ("rocm", "amd"), ("vulkan", "vulkan")):
            cb = checks[key]
            if not cb.isVisible() and not any(x.vendor == vendor for x in hw.gpus):
                continue
            used_ = vendor in c.vendors()
            cb.setEnabled(used_)
            if not used_:
                cb.setChecked(False)
            elif vendor not in refresh.last_vendors:
                cb.setChecked(True)                  # picked that card: you'll want its support
        refresh.last_vendors = c.vendors()
        c = collect()
        on_vk = c.spec("speech").startswith("vulkan:")
        for name, rb in wrows.items():           # whisper.cpp models are smaller downloads
            label, size, vram = rb._sizes
            if on_vk:
                size, vram = K.ggml_size(name), K.ggml_vram(name)
            rb.setText(f"{label}   ·   {mb(size)}, ~{vram / 1024:.1f} GB memory")
        nllb_box.setEnabled(nllb_on.isChecked())
        tr_dev.setEnabled(nllb_on.isChecked())
        smart_box.setEnabled(smart_on.isChecked())
        smart_dev.setEnabled(smart_on.isChecked())
        ear = "earshot" in c.groups
        listen_dev.setEnabled(ear)
        own = ear and listen_dev.currentData() != "same"
        listen_model.setEnabled(own)
        state, _fit_text = update_mem(c)      # graphics memory meters + fit check
        # disk space
        steps = K.plan(hw, c, have, mode == "update", fresh=run.get("fresh", False))
        dl = K.download_mb(steps)
        free_mb = hw.free_gb * 1000
        custom_view = pages.currentIndex() == 1
        foot.setVisible(pages.currentIndex() < 3)
        disk_bar.row.setVisible(custom_view)
        disk_lbl.setVisible(not custom_view)
        disk_lbl.setText(f"💾 Download about {mb(dl)}" + (
            f" · {mb(K.space_after(used, steps))} on disk after the install"
            if used["total"] is not None else "") + f" · {hw.free_gb:.0f} GB free")
        if used["total"] is None:
            disk_txt.setText(f"measuring…  ·  download about {mb(dl)}")
        else:
            now = used["total"]
            after = K.space_after(used, steps)
            room = now + free_mb
            short = after - now > free_mb
            disk_bar.set(room, min(now, after), max(0, after - now),
                         "no" if short else ("tight" if after - now > free_mb * 0.8 else "ok"))
            delta = after - now
            disk_txt.setText(f"now {mb(now)} → {mb(after)}  ({'+' if delta >= 0 else '−'}"
                             f"{mb(abs(delta))}, download {mb(dl)}) · {hw.free_gb:.0f} GB free"
                             + ("  ⚠ not enough space!" if short else ""))
            disk_bar.setToolTip(f"Chatbox Studio uses {mb(now)} now: app {mb(used['app'])}, "
                                f"models {mb(used['models'] + used['speech'])}")
        next_btn.setEnabled(pages.currentIndex() > 1 or state != "no" or not c.smart)
    refresh.last_vendors = want.vendors()
    loading = {"on": False}

    def apply_choice(c: K.Choice):
        """Puts a choice into the Custom page's widgets (collect() reads them back)."""
        loading["on"] = True
        try:
            for k, cb in checks.items():
                if not K.GROUP[k].required:
                    cb.setChecked(k in c.groups)
            if c.whisper in wrows:
                wrows[c.whisper].setChecked(True)
            for box, val in ((dev, c.gpu), (tr_dev, c.tr_dev), (smart_dev, c.smart_dev),
                             (listen_dev, c.listen_dev), (listen_model, c.listen_model)):
                box.setCurrentIndex(max(0, box.findData(val)))
            nllb_on.setChecked(bool(c.nllb))
            if c.nllb:
                nllb_box.setCurrentIndex(max(0, nllb_box.findData(c.nllb)))
            smart_on.setChecked(bool(c.smart))
            if c.smart:
                smart_box.setCurrentIndex(max(0, smart_box.findData(c.smart)))
        finally:
            loading["on"] = False
        refresh.last_vendors = c.vendors()
        refresh()

    presets = {k: K.preset(hw, k, state0) for k in cards if k != "custom"}
    for k, c_ in presets.items():
        rb, size, what = cards[k]
        try:
            dl_ = K.download_mb(K.plan(hw, c_, have, mode == "update"))
        except Exception:
            dl_ = 0
        size.setText(f"download {mb(dl_)}" if dl_ else "nothing to download")
        where = "on the CPU" if c_.spec("speech") == "cpu" else \
            f"on {short_name(K.find_gpu(hw, c_.spec('speech')))}"
        what.setText(f"→ {K.describe(c_)} · runs {where}")
    cards["custom"][2].setVisible(False)
    cards["custom"][1].setText("")

    def on_preset(btn):
        key = next(k for k, v in cards.items() if v[0] is btn)
        if key == "custom":
            pages.setCurrentIndex(1)
            refresh()
        else:
            apply_choice(presets[key])

    def go_simple():
        pages.setCurrentIndex(0)
        refresh()
    back_btn.clicked.connect(go_simple)

    # ---------------------------------------------------------------- page 2
    p2 = QWidget()
    l2 = QVBoxLayout(p2)
    l2.setContentsMargins(0, 8, 0, 0)
    step_lbl = QLabel("", objectName="title")
    l2.addWidget(step_lbl)
    bar = QProgressBar()
    bar.setRange(0, 1000)
    l2.addWidget(bar)
    sub_bar = QProgressBar()
    sub_bar.setRange(0, 100)
    sub_bar.setTextVisible(False)
    sub_bar.setFixedHeight(8)
    l2.addWidget(sub_bar)
    log = QPlainTextEdit(readOnly=True)
    log.setMaximumBlockCount(4000)
    log.setFont(QFont("monospace"))
    l2.addWidget(log, 1)
    pages.addWidget(p2)

    # ---------------------------------------------------------------- page 3
    p3 = QWidget()
    l3 = QVBoxLayout(p3)
    l3.setContentsMargins(0, 20, 0, 0)
    done_title = QLabel("", objectName="title")
    done_title.setStyleSheet("font-size: 18px;")
    l3.addWidget(done_title)
    done_text = hint("")
    l3.addWidget(done_text)
    start_cb = QCheckBox(f"Start {APP_NAME} now", checked=True)
    l3.addWidget(start_cb)
    rocm_btn = QPushButton("Install ROCm… (opens a terminal)")
    rocm_btn.setVisible(False)
    l3.addWidget(rocm_btn)
    l3.addStretch()
    pages.addWidget(p3)

    # ---------------------------------------------------------------- buttons
    btns = QHBoxLayout()
    fresh_btn = QPushButton("Fresh install…")
    fresh_btn.setToolTip("Reinstalls every ticked part and model from scratch - fixes a broken "
                         "install")
    remove_btn = QPushButton("Uninstall completely…")
    remove_btn.setToolTip(f"Removes {APP_NAME} from this PC")
    btns.addWidget(fresh_btn)
    btns.addWidget(remove_btn)
    btns.addStretch()
    cancel_btn = QPushButton("Cancel")
    next_btn = QPushButton("Install", objectName="primary")
    btns.addWidget(cancel_btn)
    btns.addWidget(next_btn)
    outer.addLayout(btns)

    run = {"steps": [], "i": 0, "proc": None, "done_w": 0.0, "total_w": 1.0, "errors": [],
           "blocked": False, "choice": None, "cancelled": False, "fresh": False,
           "reset": False, "need_rocm": False}
    for w in [dev, tr_dev, smart_dev, listen_dev, listen_model, nllb_on, nllb_box, smart_on,
              smart_box, *checks.values(), *wrows.values()]:
        sig = getattr(w, "currentIndexChanged", None) or getattr(w, "toggled")
        sig.connect(refresh)
    first_key = "keep" if "keep" in cards else "standard"
    cards[first_key][0].setChecked(True)
    apply_choice(presets[first_key])
    pgroup.buttonClicked.connect(on_preset)
    measure()
    measure_vram()
    vram_timer = QTimer(win)
    vram_timer.timeout.connect(lambda: measure_vram() if pages.currentIndex() < 2 else None)
    vram_timer.start(3000)

    def append(text):
        log.appendPlainText(text.rstrip("\n"))

    def finish():
        errors = run["errors"]
        next_btn.setEnabled(True)
        cancel_btn.setVisible(False)
        if run["blocked"]:
            done_title.setText("Windows blocked a library")
            done_text.setText(
                "Windows “Smart App Control” only lets programs run that Microsoft already "
                "knows, and some AI libraries this app uses are not signed.\n\nTo use the app, "
                "turn it off: Windows Security › App & browser control › Smart App Control "
                "settings › Off. (Windows can't switch it back on later without a reset; "
                "Microsoft Defender keeps running.) Then run the setup again.")
            start_cb.setChecked(False)
            start_cb.setVisible(False)
        elif run["need_rocm"]:
            done_title.setText("One more step for your AMD card")
            done_text.setText(
                "Linux needs AMD's ROCm 7.2 libraries for the graphics card (HIP, hipBLAS, "
                "hipRAND). Click “Install ROCm…” - a terminal opens and installs them from "
                "AMD's official repository (asks for your password, about 3 GB). Log out and "
                "back in once, then open the setup again and click Install.\n\nUntil then "
                f"{APP_NAME} runs on the CPU." + (
                    "\n\nOther steps failed too:\n• " + "\n• ".join(
                        e for e in errors if "AMD" not in e) if any(
                        "AMD" not in e for e in errors) else ""))
            rocm_btn.setVisible(True)
        elif errors and not run["cancelled"]:
            done_title.setText("Finished with problems")
            done_text.setText("These steps failed:\n• " + "\n• ".join(errors) +
                              "\n\nCheck your internet connection and run the setup again - "
                              "it continues where it stopped. The log has the details.")
        elif run["cancelled"]:
            done_title.setText("Stopped")
            done_text.setText("Setup was stopped. Run it again any time to finish.")
            start_cb.setChecked(False)
        else:
            done_title.setText("All done ✓")
            done_text.setText(f"{APP_NAME} is ready. You can add or remove parts any time: "
                              f"start “{APP_NAME} Setup” from the menu, or use the button in "
                              "the app (⚙ Settings › Parts & about).")
        if run["choice"] is not None and not run["blocked"] and not (
                errors and any("speech recognition" in e for e in errors)):
            c = run["choice"]
            if any("AMD" in e for e in errors):
                c.groups.discard("rocm")             # not working yet: run on the CPU
            K.save_state(c)
            K.apply_settings(c, reset=run["reset"])
        refresh()
        next_btn.setText("Finish")
        sub.setText("")
        pages.setCurrentIndex(3)
        foot.setVisible(False)

    def next_step():
        if run["cancelled"]:
            return finish()
        i = run["i"]
        if i >= len(run["steps"]):
            bar.setValue(1000)
            return finish()
        s = run["steps"][i]
        step_lbl.setText(f"{i + 1}/{len(run['steps'])}  {s.label}")
        append(f"\n==> {s.label}")
        sub_bar.setValue(0)
        p = QProcess(win)
        p.setProcessChannelMode(QProcess.MergedChannels)
        env = p.processEnvironment()
        env = env.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PIP_DISABLE_PIP_VERSION_CHECK", "1")
        p.setProcessEnvironment(env)
        buf = {"text": ""}

        def on_out():
            buf["text"] += bytes(p.readAllStandardOutput()).decode("utf-8", "replace")
            *lines, buf["text"] = buf["text"].replace("\r", "\n").split("\n")
            for line in lines:
                if line.startswith("PCT "):
                    try:
                        set_sub(int(line[4:]))
                    except ValueError:
                        pass
                    continue
                if line.startswith("Progress ") and " of " in line:      # pip --progress-bar raw
                    try:
                        a, b = line[9:].split(" of ")
                        set_sub(int(a) * 100 // max(1, int(b)))
                    except ValueError:
                        pass
                    continue
                if line.startswith("BLOCKED"):
                    run["blocked"] = True
                if line.startswith("NEED_ROCM"):
                    run["need_rocm"] = True
                if line.strip():
                    append(line)

        def set_sub(v):
            sub_bar.setValue(v)
            bar.setValue(int((run["done_w"] + s.weight * v / 100) / run["total_w"] * 1000))

        def on_done(code, _status):
            on_out()
            if code == 3:
                run["blocked"] = True
            elif code == 4:
                run["need_rocm"] = True
                run["errors"].append(s.label)
                append("   (ROCm system libraries missing)")
            elif code != 0:
                run["errors"].append(s.label)
                append(f"   (failed, exit code {code})")
                if s.kind == "pip" and "speech recognition" in s.label:
                    run["i"] = len(run["steps"])       # nothing works without the core
            run["done_w"] += s.weight
            run["i"] += 1
            run["proc"] = None
            if run["blocked"]:
                return finish()
            QTimer.singleShot(50, next_step)
        p.readyReadStandardOutput.connect(on_out)
        p.finished.connect(on_done)
        run["proc"] = p
        p.start(s.cmd[0], s.cmd[1:])
        if not p.waitForStarted(10000):
            run["errors"].append(s.label)
            append("   (could not start)")
            run["i"] += 1
            QTimer.singleShot(50, next_step)

    def start_install():
        c = collect()
        state, text = K.fit(hw, c)                     # the card itself
        if state != "no":
            live_state, live_text = K.fit(hw, c, {k: u for k, u in live["usage"].items()
                                                  if u.ok})
            if live_state == "no":                      # only full because of VRChat & co
                if QMessageBox.question(
                        win, "Not much graphics memory left",
                        live_text + "\n\nThis is measured right now - other programs may use "
                        "less later. Install anyway?") != QMessageBox.Yes:
                    return
        if state == "no" and c.smart:
            QMessageBox.warning(win, "Too big", text)
            return
        if state == "no":
            if QMessageBox.question(win, "Too big for this card?", text + "\n\nInstall anyway?") \
                    != QMessageBox.Yes:
                return
        run["choice"] = c
        run["steps"] = K.plan(hw, c, have, mode == "update", fresh=run["fresh"])
        run["total_w"] = sum(s.weight for s in run["steps"]) or 1
        pages.setCurrentIndex(2)
        back_row_hide()
        next_btn.setEnabled(False)
        cancel_btn.setText("Stop")
        sub.setText("Installing - this can take a while for the big downloads. You can keep "
                    "using your PC.")
        fresh_btn.setEnabled(False)
        remove_btn.setEnabled(False)
        next_step()

    def fresh_install():
        box = QMessageBox(win)
        box.setWindowTitle("Fresh install")
        box.setText("Reinstall every ticked part and model from scratch?")
        box.setInformativeText("This downloads everything again (see “Space after install”). "
                               "Use it when something is broken.")
        reset = QCheckBox("Also reset my settings (routes, chatbox, filters)")
        box.setCheckBox(reset)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        if box.exec() != QMessageBox.Yes:
            return
        run["fresh"] = True
        run["reset"] = reset.isChecked()
        refresh()
        start_install()

    def uninstall():
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        d = QDialog(win)
        d.setWindowTitle(f"Uninstall {APP_NAME}")
        dl_ = QVBoxLayout(d)
        dl_.addWidget(QLabel(f"Remove {APP_NAME} completely from this PC?"))
        sizes = used if used["total"] is not None else K.used_space()
        keep_s = QCheckBox("Keep my settings (for a later reinstall)")
        keep_m = QCheckBox(f"Keep the downloaded models ({mb(sizes['models'] + sizes['speech'])})")
        dl_.addWidget(keep_s)
        dl_.addWidget(keep_m)
        dl_.addWidget(hint("The app, its Python, the shortcuts and the menu entries are "
                           f"removed. That frees about {mb(sizes['total'])} "
                           "(less if you keep the models)."))
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        go = bb.addButton("Uninstall", QDialogButtonBox.DestructiveRole)
        go.clicked.connect(d.accept)
        bb.rejected.connect(d.reject)
        dl_.addWidget(bb)
        if d.exec() != QDialog.Accepted:
            return
        try:
            K.uninstall_everything(keep_s.isChecked(), keep_m.isChecked())
        except Exception as e:
            QMessageBox.warning(win, "Uninstall failed", str(e))
            return
        QMessageBox.information(win, "Uninstall", f"{APP_NAME} is removed as soon as this "
                                "window closes. Thanks for trying it!")
        run["uninstalled"] = True
        win.close()

    def install_rocm():
        import subprocess
        script = Path(__file__).resolve().parent / "tools" / "install_rocm.sh"
        for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"],
                     ["konsole", "-e"], ["xfce4-terminal", "-x"], ["xterm", "-e"]):
            import shutil as _sh
            if _sh.which(term[0]):
                subprocess.Popen(term + ["bash", str(script)], start_new_session=True)
                return
        QMessageBox.information(win, "Install ROCm", f"Open a terminal and run:\n\n  bash {script}")

    def start_app():
        import subprocess
        root = paths.data_dir()
        here = Path(__file__).resolve().parent
        try:
            if paths.IS_WINDOWS:
                pyw = Path(sys.executable).with_name("pythonw.exe")
                subprocess.Popen([str(pyw if pyw.exists() else sys.executable),
                                  str(here / "app.py")], cwd=str(here),
                                 **paths.hidden_subprocess_kwargs())
            else:
                runner = root / "run.sh"
                pkg = os.environ.get("CHATBOX_STUDIO_LAUNCHER", "")   # system package
                cmd = ([pkg] if pkg and Path(pkg).exists() else [str(runner)] if runner.exists()
                       else [sys.executable, str(here / "app.py")])
                subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception as e:
            QMessageBox.warning(win, "Couldn't start", str(e))

    def back_row_hide():
        back_btn.setVisible(False)

    def on_next():
        if pages.currentIndex() in (0, 1):
            start_install()
        elif pages.currentIndex() == 3:
            if start_cb.isChecked() and start_cb.isVisible():
                start_app()
            win.close()

    def on_cancel():
        if pages.currentIndex() == 2 and run["proc"] is not None:
            if QMessageBox.question(win, "Stop setup?", "Stop after killing the current step? "
                                    "You can run the setup again later.") == QMessageBox.Yes:
                run["cancelled"] = True
                run["proc"].kill()
            return
        win.close()

    next_btn.clicked.connect(on_next)
    cancel_btn.clicked.connect(on_cancel)
    fresh_btn.clicked.connect(fresh_install)
    remove_btn.clicked.connect(uninstall)
    rocm_btn.clicked.connect(install_rocm)
    if first:
        fresh_btn.setVisible(False)
        remove_btn.setVisible(False)
    if mode == "update" and not first:
        next_btn.setText("Install")
    win.show()
    code = app.exec()
    return 0 if not run["errors"] and not run["blocked"] else (3 if run["blocked"] else 1)


def main(argv: list[str]) -> int:
    if "--defaults" in argv:
        return run_cli("--update" in argv)
    mode = "update" if "--update" in argv else ("modify" if "--modify" in argv else "first")
    if not paths.IS_WINDOWS and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) \
            and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        print("No display - installing the recommended parts without the setup window.")
        return run_cli(mode == "update")
    return run_gui(mode)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
