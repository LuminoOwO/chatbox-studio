#!/usr/bin/env python3
"""
First start of Chatbox Studio from a system package (.deb / .rpm / Arch).

The package puts the app into /opt/chatbox-studio. The Python packages it needs (Qt, the
speech engine, ...) are big and differ per graphics card, so they go into a per-user
environment: ~/.local/share/chatbox-studio/venv - the same place the ZIP installer uses.
This script creates it, with a small progress window, and then starts the launcher again:

    bootstrap.py chatbox-studio [args...]

It uses only the standard library. When the system Python is too new or too old for the
AI packages (or has no venv module), it downloads a standalone Python 3.12 build
(python-build-standalone, ~30 MB) into ~/.local/share/chatbox-studio/python.
"""

from __future__ import annotations

import hashlib
import os
import queue
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "chatbox-studio"
VENV = DATA / "venv"
HASHFILE = VENV / "base.hash"
BASE_REQ = HERE / "req" / "base.txt"
PY_OK = ((3, 10), (3, 13))            # versions every AI package has wheels for
STANDALONE = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
              "20261026/cpython-3.12.16+20261026-x86_64-unknown-linux-gnu-"
              "install_only_stripped.tar.gz")


def base_hash() -> str:
    return hashlib.sha256(BASE_REQ.read_bytes()).hexdigest()


def ready() -> bool:
    try:
        return (VENV / "bin" / "python").exists() and HASHFILE.read_text().strip() == base_hash()
    except OSError:
        return False


class UI:
    """Tk progress window, or plain output when there's no Tk."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.tk = None
        try:
            import tkinter as tk
            from tkinter import ttk
            root = tk.Tk()
            root.title("Chatbox Studio - first start")
            root.geometry("640x360")
            tk.Label(root, text="Preparing Chatbox Studio", font=("", 14, "bold")).pack(
                anchor="w", padx=16, pady=(14, 2))
            tk.Label(root, text="Only the first time: this downloads the setup window "
                     "(about 100 MB). Then you pick what to install.", wraplength=600,
                     justify="left").pack(anchor="w", padx=16)
            self.bar = ttk.Progressbar(root, mode="indeterminate")
            self.bar.pack(fill="x", padx=16, pady=10)
            self.bar.start(12)
            self.text = tk.Text(root, height=12, font=("monospace", 9))
            self.text.pack(fill="both", expand=True, padx=16, pady=(0, 14))
            self.tk, self.root = tk, root
            root.after(100, self._pump)
        except Exception:
            self.tk = None

    def line(self, text: str):
        if self.tk is None:
            print(text, flush=True)
        else:
            self.q.put(text)

    def _pump(self):
        try:
            while True:
                t = self.q.get_nowait()
                if t is None:
                    self.root.destroy()
                    return
                self.text.insert("end", t + "\n")
                self.text.see("end")
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def error(self, text: str):
        if self.tk is None:
            print("ERROR: " + text, file=sys.stderr)
            return
        from tkinter import messagebox
        self.q.put("ERROR: " + text)
        self.root.after(200, lambda: messagebox.showerror("Chatbox Studio", text))

    def run(self, work):
        """Runs work() in a thread; returns its result once the window is closed."""
        out = {}

        def wrap():
            try:
                out["ok"] = work()
            except Exception as e:      # noqa: BLE001
                self.error(f"{type(e).__name__}: {e}")
                out["ok"] = False
            if out.get("ok") and self.tk is not None:
                self.q.put(None)        # close the window
        t = threading.Thread(target=wrap, daemon=True)
        t.start()
        if self.tk is not None:
            self.root.mainloop()
        t.join()
        return out.get("ok", False)


def run(cmd, ui: UI) -> int:
    ui.line("$ " + " ".join(str(c) for c in cmd))
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        line = line.rstrip()
        if line:
            ui.line(line)
    return p.wait()


def system_python_ok(py: str) -> bool:
    try:
        r = subprocess.run([py, "-c", "import sys, venv, ensurepip; "
                            "print('%d.%d' % sys.version_info[:2])"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return False
        v = tuple(int(x) for x in r.stdout.strip().split("."))
        return PY_OK[0] <= v <= PY_OK[1]
    except Exception:
        return False


def standalone_python(ui: UI) -> str:
    target = DATA / "python"
    exe = target / "bin" / "python3"
    if exe.exists():
        return str(exe)
    ui.line("Your system Python doesn't fit the AI packages - downloading Python 3.12 "
            "(python-build-standalone, ~30 MB) ...")
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "python.tar.gz"
        urllib.request.urlretrieve(STANDALONE, tgz)
        with tarfile.open(tgz) as tf:
            tf.extractall(tmp, filter="data") if hasattr(tarfile, "data_filter") \
                else tf.extractall(tmp)
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(Path(tmp) / "python"), str(target))
    return str(exe)


def prepare(ui: UI) -> bool:
    DATA.mkdir(parents=True, exist_ok=True)
    py = shutil.which("python3") or sys.executable
    if not system_python_ok(py):
        py = standalone_python(ui)
    if not (VENV / "bin" / "python").exists():
        ui.line("Creating the Python environment ...")
        shutil.rmtree(VENV, ignore_errors=True)
        if run([py, "-m", "venv", VENV], ui) != 0:
            ui.error("Creating the Python environment failed.")
            return False
    vpy = VENV / "bin" / "python"
    run([vpy, "-m", "pip", "install", "-q", "--upgrade", "pip"], ui)
    ui.line("Installing the setup window ...")
    if run([vpy, "-m", "pip", "install", "--disable-pip-version-check", "-r", BASE_REQ],
           ui) != 0:
        ui.error("Installing the packages failed - check your internet connection and start "
                 "Chatbox Studio again.")
        return False
    HASHFILE.write_text(base_hash())
    ui.line("Done.")
    return True


def main(argv: list[str]) -> int:
    tool = argv[0] if argv else "chatbox-studio"
    if not ready():
        ui = UI()
        if not ui.run(lambda: prepare(ui)):
            return 1
    launcher = shutil.which(tool) or f"/usr/bin/{tool}"
    os.execv(launcher, [launcher] + argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
