"""Per-OS locations and small platform helpers (Linux and Windows)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_dll_handles = []


# Current name and the name used up to version 1.8 ("VRC Voice Translator").
WIN_NAME, LINUX_NAME = "Chatbox Studio", "chatbox-studio"
OLD_WIN_NAME, OLD_LINUX_NAME = "VRC Voice Translator", "vrc-voice-translator"


def _win_roaming() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")


def _win_local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def _xdg_config() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _xdg_data() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_dir(old: bool = False) -> Path:
    """Settings. Linux: ~/.config/chatbox-studio  Windows: %APPDATA%\\Chatbox Studio"""
    if IS_WINDOWS:
        return _win_roaming() / (OLD_WIN_NAME if old else WIN_NAME)
    return _xdg_config() / (OLD_LINUX_NAME if old else LINUX_NAME)


def data_dir(old: bool = False) -> Path:
    """App, models and logs. Linux: ~/.local/share/chatbox-studio
    Windows: %LOCALAPPDATA%\\Chatbox Studio"""
    if IS_WINDOWS:
        return _win_local() / (OLD_WIN_NAME if old else WIN_NAME)
    return _xdg_data() / (OLD_LINUX_NAME if old else LINUX_NAME)


def migrate_from_old_name(log=lambda msg: None):
    """Carries settings and downloaded models over from "VRC Voice Translator" (1.0-1.8).
    The installers do this too; this covers people who start the new files directly."""
    import shutil
    moved = []
    old_cfg, new_cfg = config_dir(old=True) / "settings.json", config_dir() / "settings.json"
    if old_cfg.exists() and not new_cfg.exists():
        try:
            new_cfg.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_cfg, new_cfg)
            moved.append("settings")
        except OSError:
            pass
    old_models, new_models = data_dir(old=True) / "models", data_dir() / "models"
    if old_models.is_dir() and not (new_models.is_dir() and any(new_models.iterdir())):
        try:
            new_models.parent.mkdir(parents=True, exist_ok=True)
            if new_models.exists():
                new_models.rmdir()
            shutil.move(str(old_models), str(new_models))
            moved.append("translator model")
        except OSError:
            pass
    if moved:
        log(f"Took over your {' and '.join(moved)} from VRC Voice Translator.")
    return moved


def models_dir() -> Path:
    return data_dir() / "models"


def crash_log() -> Path:
    return data_dir() / "crash.log"


def hidden_subprocess_kwargs() -> dict:
    """Stops console windows flashing up on Windows when calling nvidia-smi."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def setup_cuda_libraries():
    """On Windows, make the CUDA DLLs from the nvidia-* pip packages findable.
    (Linux does the same with LD_LIBRARY_PATH in the launcher script.)"""
    if not IS_WINDOWS:
        return
    import site
    roots = list(site.getsitepackages())
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    dirs = []
    for root in roots:
        nv = Path(root) / "nvidia"
        if nv.is_dir():
            dirs += [str(p) for p in nv.glob("*/bin") if p.is_dir()]
    for d in dirs:
        try:
            _dll_handles.append(os.add_dll_directory(d))   # keep the handles alive
        except OSError:
            pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])


def setup_output_streams():
    """When started without a console (Windows shortcut uses pythonw.exe), stdout/stderr are
    None and libraries that print progress bars would crash. Send them to a log file."""
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        data_dir().mkdir(parents=True, exist_ok=True)
        log = open(data_dir() / "app.log", "w", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
