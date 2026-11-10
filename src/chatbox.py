"""
Chatbox composer: builds the VRChat chatbox message from several elements
(speech, custom text, clock, now playing, PC stats, ...) and sends it at a rate
VRChat accepts.

Templates: {placeholder} is replaced; a part in [square brackets] disappears when a
placeholder inside it is empty, e.g. "🎵 {title}[ — {artist}]".
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional

import media as M
import paths
from lyrics import LyricsProvider

LIMIT = 144                  # VRChat chatbox length: 144 UTF-16 units (an emoji counts 2)


def ulen(text: str) -> int:
    """Length the way VRChat counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2
SAMPLE_BASE = ("This sample shows how much text fits in your chatbox at once. "
               "Every word here counts toward the limit, so you can see the real size. ")


def sample_text(n: int) -> str:
    """Exactly n characters of readable sample text."""
    n = max(1, min(LIMIT, int(n)))
    head = f"[{n} characters] "
    body = (head + SAMPLE_BASE * 3)[:n]
    body = body.ljust(n, ".")
    return body[:-1] + "." if body.endswith(" ") else body

ELEMENT_TYPES = {
    "speech": {
        "label": "Speech / translation", "icon": "💬",
        "defaults": {"width": 144, "mode": "pages", "page_seconds": 0.0, "scroll_words": 3,
                     "scroll_seconds": 1.6, "hold_seconds": 6.0, "flow": "live", "limit": 0,
                     "icon": ""},
    },
    "text": {
        "label": "Custom text & info", "icon": "📝",
        "defaults": {"text": "Hi! I'm using a voice translator 🌍", "icon": "", "gpu_index": 0},
    },
    "rotating": {
        "label": "Rotating messages", "icon": "🔁",
        "defaults": {"messages": "Hello!\nHave a nice day ✨",
                     "interval": 10.0, "random": False, "icon": ""},
    },
    "time": {
        "label": "Clock", "icon": "🕒",
        "defaults": {"timezone": "local", "style": "24h", "format": "{time}", "icon": "🕒"},
    },
    "countdown": {
        "label": "Countdown", "icon": "⏳",
        "defaults": {"target": "2026-12-24 18:00", "label": "Christmas",
                     "format": "{label} in {left}", "done": "🎉 {label}!", "icon": "⏳"},
    },
    "media": {
        "label": "Now playing", "icon": "🎵",
        "defaults": {"format": "{title}[ — {artist}]", "fallback_format": "{app}",
                     "icon": "🎵", "fallback_icon": "🔊",
                     "show_paused": False, "max_title": 40, "filters": None},
    },
    "lyrics": {
        "label": "Lyrics (experimental)", "icon": "🎤",
        "defaults": {"format": "{line}", "icon": "🎤", "online": False, "offset": 0.0,
                     "folder": ""},
    },
    "afk": {
        "label": "AFK", "icon": "💤",
        "defaults": {"format": "AFK for {duration}", "icon": "💤"},
    },
    "playtime": {
        "label": "Play time", "icon": "⏱",
        "defaults": {"since": "vrchat", "format": "{elapsed}", "icon": "⏱"},
    },
    "window": {
        "label": "Active app", "icon": "🖥",
        "defaults": {"format": "{app}", "show_title": False, "icon": "🖥"},
    },
}

# Priority: when space runs out, lower priorities make room first.
# "keep" is never pushed away (speech then pages through the space that's left).
PRIORITIES = [("keep", "Always keep"), ("high", "High"), ("normal", "Normal"), ("low", "Low")]
PRIORITY_RANK = {"low": 0, "normal": 1, "high": 2, "keep": 3}
DEFAULT_PRIORITY = {"speech": "high", "afk": "high", "time": "normal", "countdown": "normal",
                    "text": "normal", "rotating": "low", "media": "low",
                    "playtime": "low", "window": "low", "lyrics": "low"}

# Icon suggestions per element (any emoji or text can be typed in, or none).
ICON_CHOICES = {
    "speech": ["", "💬", "🗣", "🎙", "🌍", "🔤"],
    "text": ["", "📝", "💭", "ℹ", "✨", "⭐", "💜", "💻", "⚙", "🥽", "🎮", "📊", "🐧", "🪟"],
    "rotating": ["", "🔁", "💭", "✨", "📢"],
    "time": ["", "🕒", "⏰", "⌚", "🕐", "🌍", "🗾", "🌙", "☀"],
    "countdown": ["", "⏳", "⌛", "📅", "🎉", "🎂"],
    "media": ["", "🎵", "🎶", "🎧", "🎼", "📻", "▶", "💿"],
    "fallback": ["", "🔊", "🔉", "🎶", "📢"],
    "afk": ["", "💤", "🌙", "😴", "🚶"],
    "playtime": ["", "⏱", "🕹", "🎮", "⌛"],
    "window": ["", "🖥", "🪟", "👀"],
    "lyrics": ["", "🎤", "🎶", "🎵", "📜"],
}


def split_icon(fmt: str) -> tuple[str, str]:
    """'🕒 {time}' -> ('🕒', '{time}'). Used to convert formats saved before 1.9."""
    m = re.match(r"^\s*(\S{1,3})\s+(.*)$", fmt or "", re.S)
    if m and not any(ch.isalnum() or ch in "{[" for ch in m.group(1)):
        return m.group(1), m.group(2)
    return "", fmt or ""


def with_icon(icon: str, text: str) -> str:
    icon = (icon or "").strip()
    return f"{icon} {text}" if icon and text else text


CLOCK_STYLES = {"24h": "%H:%M", "24h_sec": "%H:%M:%S", "12h": "%I:%M %p", "12h_sec": "%I:%M:%S %p"}
SEPARATORS = {"newline": "\n", "bar": " | ", "dot": " · ", "space": "  "}


# Every element except speech can be shown in a fixed window of characters:
# "full" (as is), "scroll" (a ticker moving along word by word, starting over at the end)
# or "pages" (part by part).
DISPLAY_DEFAULTS = {"show_mode": "full", "win_chars": 40, "win_step": 2, "win_seconds": 2.0,
                    "win_lines": 1}
DISPLAY_MODES = [("full", "Show everything"),
                 ("scroll", "Ticker: words move along, start over"),
                 ("marquee", "Marquee: letters move along, start over"),
                 ("bounce", "Bounce: slide to the end and back"),
                 ("lines", "Lines: scroll up line by line"),
                 ("pages", "Pages: one part after the other"),
                 ("typewriter", "Typewriter: words appear one after another")]
# what "Move by" counts in each mode (None = not used)
STEP_UNIT = {"scroll": "words", "marquee": "letters", "bounce": "letters", "lines": None,
             "pages": None, "typewriter": "words", "full": None}
OLD_INFO_TYPES = ("specs", "stats", "vr")      # merged into "text" in 1.16


def new_element(kind: str, **over) -> dict:
    el = {"id": uuid.uuid4().hex[:8], "type": kind, "enabled": True, "visible": True,
          "priority": DEFAULT_PRIORITY.get(kind, "normal")}
    el.update(ELEMENT_TYPES[kind]["defaults"])
    if kind != "speech":
        el.update(DISPLAY_DEFAULTS)
    if kind == "media":
        el["filters"] = M.default_filters()
    el.update(over)
    return el


def default_config() -> dict:
    return {
        "elements": [
            new_element("speech"),
            new_element("time", enabled=False),
            new_element("media", enabled=False),
            new_element("text", enabled=False),
        ],
        "separator": "newline",
        "custom_separator": " | ",
        "speech_alone": False,
        "min_interval": 1.2,
        "keepalive": 20.0,
        "hide_extras": False,
    }


def normalize_elements(cfg: Optional[dict]) -> dict:
    """Fills in missing keys of the old (1.18 and older) element setup."""
    base = default_config()
    if not cfg:
        return base
    for k, v in base.items():
        cfg.setdefault(k, v)
    els = []
    for el in cfg.get("elements", []):
        kind = el.get("type")
        if kind in OLD_INFO_TYPES:
            # 1.16: PC specs, PC stats and VR headset are one "Custom text & info" element
            fmt = str(el.get("format", ""))
            if "icon" not in el:
                el["icon"], fmt = split_icon(fmt)
            el = {k: v for k, v in el.items() if k not in ("format",)}
            el["type"] = kind = "text"
            el["text"] = fmt
            el.setdefault("priority", "low")
        if kind not in ELEMENT_TYPES:
            continue
        el.setdefault("id", uuid.uuid4().hex[:8])
        el.setdefault("enabled", True)
        el.setdefault("visible", True)
        if el.get("priority") not in PRIORITY_RANK:
            el["priority"] = DEFAULT_PRIORITY.get(kind, "normal")
        if kind == "speech" and el.get("flow") not in ("live", "queue"):
            el["flow"] = "live"          # 1.10: the old queue felt too slow, live is default
            el.pop("queue", None)
        if "icon" not in el and "format" in el:             # settings from before 1.9
            el["icon"], el["format"] = split_icon(el["format"])
            if kind == "media" and "fallback_format" in el:
                el["fallback_icon"], el["fallback_format"] = split_icon(el["fallback_format"])
        for k, v in ELEMENT_TYPES[kind]["defaults"].items():
            el.setdefault(k, v)
        if kind != "speech":
            for k, v in DISPLAY_DEFAULTS.items():
                el.setdefault(k, v)
            if el.get("show_mode") not in dict(DISPLAY_MODES):
                el["show_mode"] = "full"
        if kind == "media":
            f = el.get("filters") or {}
            for k, v in M.default_filters().items():
                f.setdefault(k, v)
            el["filters"] = f
        els.append(el)
    if not any(e["type"] == "speech" for e in els):
        els.insert(0, new_element("speech"))
    cfg["elements"] = els
    return cfg


# --------------------------------------------------------------------------- #
# Templates and formatting helpers
# --------------------------------------------------------------------------- #
_GROUP = re.compile(r"\[([^\[\]]*)\]")
_PH = re.compile(r"\{(\w+)\}")


def fill(template: str, values: dict) -> str:
    def group(m):
        inner = m.group(1)
        names = _PH.findall(inner)
        if not names:
            return m.group(0)            # plain text in brackets, e.g. "[EN]", stays
        if any(not str(values.get(n, "") or "").strip() for n in names):
            return ""
        return inner
    t = _GROUP.sub(group, template or "")
    t = _PH.sub(lambda m: str(values.get(m.group(1), "")) if m.group(1) in values
                else m.group(0), t)
    return t.replace("\\n", "\n").strip()


def fmt_duration(seconds: float, with_seconds: bool = False) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if with_seconds:
        return f"{m}m {s}s" if m else f"{s}s"
    return f"{m}m" if m else "<1m"


def fmt_clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    s = int(max(0, seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(pos: Optional[float], dur: Optional[float], width: int = 10) -> str:
    if pos is None or not dur:
        return ""
    k = min(width - 1, int(pos / dur * width))
    return "━" * k + "●" + "─" * (width - 1 - k)


def truncate(text: str, n: int) -> str:
    if n and len(text) > n:
        return text[: max(1, n - 1)].rstrip() + "…"
    return text


def truncate16(text: str, n: int = LIMIT) -> str:
    """Cuts to n UTF-16 units without splitting a character (VRChat would)."""
    if ulen(text) <= n:
        return text
    out, used = "", 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if used + w > n - 1:
            break
        out += ch
        used += w
    return out.rstrip() + "…"


def tz_label(tz: str) -> str:
    if not tz or tz == "local":
        return ""
    return tz.split("/")[-1].replace("_", " ")


def now_in(tz: str) -> datetime:
    if not tz or tz == "local":
        return datetime.now()
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz))
    except Exception:
        return datetime.now()


def wrap_words(text: str, width: int) -> list[str]:
    """Splits text into pages of at most `width` characters, at word boundaries."""
    width = max(10, width)
    pages, cur = [], ""
    for raw_line in text.split("\n"):
        words = raw_line.split()
        line_start = True
        for w in words:
            while len(w) > width:                       # very long word
                if cur:
                    pages.append(cur)
                    cur = ""
                pages.append(w[:width])
                w = w[width:]
            sep = "" if not cur else ("\n" if line_start else " ")
            if len(cur) + len(sep) + len(w) <= width:
                cur += sep + w
            else:
                pages.append(cur)
                cur = w
            line_start = False
    if cur:
        pages.append(cur)
    return pages or [""]


def scroll_windows(text: str, width: int, step: int) -> list[str]:
    """Sliding windows over the words: each next window starts `step` words later."""
    words = text.replace("\n", " \n ").split(" ")
    words = [w for w in words if w]
    out, i = [], 0
    step = max(1, step)
    while i < len(words):
        cur, j = "", i
        while j < len(words):
            w = words[j]
            add = w if not cur else (w if cur.endswith("\n") or w == "\n" else " " + w)
            if len(cur) + len(add) > width and cur:
                break
            cur += add
            j += 1
        out.append(cur.replace(" \n", "\n").replace("\n ", "\n").strip())
        if j >= len(words):
            break
        i += step
    return out or [""]


def ticker_frames(text: str, width: int, step: int) -> list[str]:
    """A ticker: a window of `width` characters moving along the text `step` words at a
    time and starting over at the end (lines are joined with ' · ')."""
    flat = " · ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(flat) <= width:
        return [flat]
    words = []
    for w in flat.split(" ") + ["•"]:
        if w in ("·", "•", "|", "-", "—", "/") and words:
            words[-1] += " " + w              # keep separators at the end of a word
        elif w:
            words.append(w)
    n = len(words)
    out = []
    for start in range(0, n, max(1, step)):
        cur, k = "", 0
        while k < n:
            w = words[(start + k) % n]
            add = w if not cur else " " + w
            if len(cur) + len(add) > width:
                if not cur:                       # one very long word
                    cur = w[:width]
                break
            cur += add
            k += 1
        out.append(cur)
    return out or [""]


def _flat(text: str) -> str:
    return " · ".join(line.strip() for line in text.splitlines() if line.strip())


def marquee_frames(text: str, width: int, step: int) -> list[str]:
    """Letter by letter, around and around like a LED sign."""
    flat = _flat(text)
    if len(flat) <= width:
        return [flat]
    loop = flat + "   •   "
    twice = loop * 2
    return [twice[i:i + width] for i in range(0, len(loop), max(1, step))]


def bounce_frames(text: str, width: int, step: int) -> list[str]:
    """Slides to the end, then back to the start."""
    flat = _flat(text)
    if len(flat) <= width:
        return [flat]
    last = len(flat) - width
    pos = list(range(0, last, max(1, step))) + [last]
    pos = pos + [pos[0]] if len(pos) == 1 else pos + pos[-2:0:-1]
    return [flat[p:p + width] for p in pos]


def line_frames(text: str, width: int, lines: int) -> list[str]:
    """Like credits: `lines` lines at a time, moving up one line per step."""
    rows = []
    for line in text.splitlines():
        if line.strip():
            rows += wrap_words(line.strip(), width) if len(line) > width else [line.strip()]
    lines = max(1, lines)
    if len(rows) <= lines:
        return ["\n".join(rows)]
    loop = rows + rows[:lines - 1]
    return ["\n".join(loop[i:i + lines]) for i in range(len(rows))]


def typewriter_frames(text: str, width: int, step: int) -> list[str]:
    """Words appear a few at a time; a full window stays for one more step, then the next."""
    step = max(1, step)
    out = []

    def join(toks):
        t = ""
        for tok in toks:
            t += tok if tok == "\n" or not t or t.endswith("\n") else " " + tok
        return t.strip()
    for page in wrap_words(text, width):
        toks = re.findall(r"\S+|\n", page)
        idx = [i for i, t in enumerate(toks) if t != "\n"]
        for k in range(step, len(idx) + step, step):
            out.append(join(toks[:idx[min(k, len(idx)) - 1] + 1]))
        if out:
            out.append(out[-1])
    return out or [""]


def frames_for(mode: str, text: str, width: int, step: int = 1, lines: int = 1) -> list[str]:
    if mode == "scroll":
        return ticker_frames(text, width, step)
    if mode == "marquee":
        return marquee_frames(text, width, step)
    if mode == "bounce":
        return bounce_frames(text, width, step)
    if mode == "lines":
        return line_frames(text, width, lines)
    if mode == "typewriter":
        return typewriter_frames(text, width, step)
    return wrap_words(text, width)


def window_text(text: str, el: dict, now_wall: float, min_interval: float = 1.6) -> str:
    """Applies an element's window setting (ticker, marquee, pages, ...) to its text."""
    mode = el.get("show_mode", "full")
    if mode == "full" or not text:
        return text
    width = max(5, min(LIMIT, int(el.get("win_chars") or 40)))
    secs = max(min_interval, float(el.get("win_seconds") or 2.0))
    frames = frames_for(mode, text, width, int(el.get("win_step") or 1),
                        int(el.get("win_lines") or 1))
    return frames[int(now_wall // secs) % len(frames)]


# --------------------------------------------------------------------------- #
# Live values (cached, updated in the background)
# --------------------------------------------------------------------------- #
class Stats:
    """PC specs (read once) and live values (every 2 s) for the stats/specs elements."""

    def __init__(self, log: Callable[[str], None] = lambda m: None):
        self.log = log
        self.values: dict = {}
        self.static: dict = {}
        self.screen: dict = {}
        self._static_for = None
        self._gpu_index = 0
        self._want = False
        self._holds = 0
        threading.Thread(target=self._loop, daemon=True, name="stats").start()

    def want(self, on: bool, gpu_index: int = 0, keys: Optional[set] = None):
        self._want = on
        self._gpu_index = gpu_index
        self._keys = set(keys or ())

    def hold(self, on: bool):
        """Keeps values updating while the settings page shows them."""
        self._holds = max(0, self._holds + (1 if on else -1))

    def set_screen(self, info: dict):
        self.screen = dict(info)

    _keys: set = set()

    def _needs(self, group_keys: set) -> bool:
        return bool(self._holds) or bool(self._keys & group_keys)

    def _extra(self, sysinfo, last: dict, cache: dict, audio):
        """VR, audio and wireless devices - only read when a format uses them."""
        now = time.monotonic()
        jobs = [("vr", sysinfo.VR_KEYS, 8,
                 lambda: sysinfo.vr_info(self.log)),
                ("audio", sysinfo.AUDIO_KEYS, 4, audio.read),
                ("devices", sysinfo.DEVICE_KEYS, 30, sysinfo.upower_devices)]
        for name, keys, every, fn in jobs:
            if not self._needs(keys):
                cache.pop(name, None)
                continue
            if now - last.get(name, 0) < every:
                continue
            last[name] = now
            try:
                cache[name] = fn()
            except Exception as e:
                cache[name] = {}
                key = f"{name}:{type(e).__name__}"
                if key not in self._failed:
                    self._failed.add(key)
                    what = {"vr": "VR headset info", "audio": "audio devices",
                            "devices": "wireless device batteries"}[name]
                    self.log(f"PC specs: {what} not available ({type(e).__name__}: {e})")
        out = {}
        for d in cache.values():
            out.update(d)
        return out

    _failed: set = set()

    def _loop(self):
        import sysinfo
        live = None
        last, cache = {}, {}
        audio = sysinfo.AudioInfo()
        while True:
            if self._want or self._holds:
                if self._static_for != self._gpu_index:
                    self._static_for = self._gpu_index
                    try:
                        self.static = sysinfo.static_info(self._gpu_index, self.log)
                    except Exception as e:
                        self.log(f"PC specs could not be read: {e}")
                try:
                    if live is None:
                        live = sysinfo.Live()
                    lv = live.sample(self._gpu_index)
                except Exception as e:
                    lv = {}
                    if live is None:
                        self.log(f"PC stats not available: {e}")
                        live = False
                if lv.get("uptime_s"):
                    lv["uptime"] = fmt_duration(float(lv.pop("uptime_s")))
                v = dict(self.static)
                v.update(self.screen)
                v.update({k: val for k, val in lv.items() if val != ""})
                v.update({k: val for k, val in self._extra(sysinfo, last, cache, audio).items()
                          if val not in ("", None)})
                self.values = v
            time.sleep(2)


class VRChatClock:
    """When VRChat was started (for 'play time')."""

    def __init__(self):
        self.started: Optional[float] = None
        self._checked = 0.0

    def get(self) -> Optional[float]:
        if time.monotonic() - self._checked > 30:
            self._checked = time.monotonic()
            self.started = None
            try:
                import psutil
                for p in psutil.process_iter(["name", "create_time"]):
                    if (p.info.get("name") or "").lower() in ("vrchat.exe", "vrchat"):
                        self.started = p.info["create_time"]
                        break
            except Exception:
                pass
        return self.started


# --------------------------------------------------------------------------- #
# Speech pages
# --------------------------------------------------------------------------- #
class SpeechItem:
    def __init__(self, text: str, start: float):
        self.text = text
        self.start = start
        self.frames: Optional[list[tuple[str, float]]] = None   # (text, seconds)
        self.width = 0
        self.cut_at: Optional[float] = None     # live mode: end early for newer speech
        self.frame_idx = -1
        self.frame_started: Optional[float] = None

    def shown_text(self, el: dict) -> str:
        limit = int(el.get("limit") or 0)
        return truncate(self.text, limit) if limit else self.text

    def build(self, el: dict, width: int, min_interval: float):
        self.width = width
        text = self.shown_text(el)
        mode = el.get("mode")
        if mode in ("scroll", "marquee", "typewriter"):
            step_s = max(min_interval, float(el.get("scroll_seconds") or min_interval))
            step = int(el.get("scroll_words") or 3)
            if mode == "scroll":
                wins = scroll_windows(text, width, step)
            elif mode == "marquee":       # once through, no loop for speech
                flat = " ".join(text.split())
                wins = [flat[i:i + width] for i in
                        range(0, max(1, len(flat) - width + 1), max(1, step * 4))]
                if len(flat) > width and wins[-1] != flat[-width:]:
                    wins.append(flat[-width:])
            else:
                wins = typewriter_frames(text, width, step)[:-1] or [text[:width]]
            self.frames = [(w, step_s) for w in wins]
        else:
            fixed = float(el.get("page_seconds") or 0)
            pages = wrap_words(text, width)
            self.frames = [(p, max(min_interval, fixed or max(2.5, len(p) / 14)))
                           for p in pages]

    def total(self) -> float:
        return sum(d for _t, d in self.frames or [])


# --------------------------------------------------------------------------- #
# Composer
# --------------------------------------------------------------------------- #
class Chatbox:
    def __init__(self, settings, send: Callable[[str], None], log: Callable[[str], None],
                 on_preview: Callable[[str, bool], None] = lambda text, sent: None,
                 clock: Callable[[], float] = time.monotonic, start_thread: bool = True):
        self.s = settings
        import blocks as B
        self.s.chatbox = B.normalize(getattr(settings, "chatbox", None))
        self._send = send
        self.log = log
        self.on_preview = on_preview
        self.clock = clock
        self.media = M.MediaWatcher(log)
        from version import APP_NAME, __version__
        self.lyrics = LyricsProvider(log, f"{APP_NAME.replace(' ', '')}/{__version__} "
                                          "(VRChat chatbox app)")
        self.stats = Stats(log)
        self.vrchat = VRChatClock()
        self.app_started = time.time()
        self.afk_since: Optional[float] = None
        self.vrc_params: dict = {}
        self._speech: list[SpeechItem] = []
        self._speech_until = 0.0          # end of hold for the last item
        self._lock = threading.Lock()
        self.last_sent = ""
        self.last_sent_at = 0.0
        self._last_preview = None
        self.paused_sending = False
        self.last_dropped: list[dict] = []
        self._configure_watchers()
        if start_thread:
            threading.Thread(target=self._loop, daemon=True, name="chatbox").start()

    # ---- inputs -----------------------------------------------------------
    @property
    def cfg(self) -> dict:
        return self.s.chatbox

    def speech_element(self) -> dict:
        return next(b for b in self.cfg["blocks"] if b["type"] == "speech")

    def set_speech(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        now = self.clock()
        el = self.speech_element()
        min_int = max(1.0, float(self.cfg.get("min_interval", 1.2)))
        with self._lock:
            self._expire(now)
            if not self._speech:
                self._speech = [SpeechItem(text, now)]
            elif el.get("flow", "live") == "queue":
                self._speech.append(SpeechItem(text, None))
                if len(self._speech) > 4:                  # don't fall further behind
                    self._speech = [self._speech[0]] + self._speech[-3:]
            else:
                # live: jump to the new sentence as soon as the line on screen has been
                # visible for one VRChat update, so nothing disappears unseen
                cur = self._speech[0]
                if cur.frames is None or cur.frame_started is None:
                    self._speech = [SpeechItem(text, now)]     # never shown: just replace
                else:
                    cur.cut_at = max(now, cur.frame_started + min_int)
                    self._speech = [cur, SpeechItem(text, None)]

    def clear(self):
        """Removes speech and sends an empty chatbox right away."""
        with self._lock:
            self._speech = []
        self._really_send("")

    def set_vrc_param(self, name: str, value):
        """Avatar parameters VRChat sends over OSC (VRMode, TrackingType, ...)."""
        self.vrc_params[name] = value

    def vrc_values(self) -> dict:
        p = self.vrc_params
        v = {}
        if "VRMode" in p:
            v["vrc_mode"] = "VR" if int(p["VRMode"]) else "Desktop"
        if "TrackingType" in p:
            v["vrc_tracking"] = {1: "Generic", 2: "Hands only", 3: "3-point",
                                 4: "4-point (hip)", 5: "5-point", 6: "Full body"}.get(
                int(p["TrackingType"]), "")
        if p.get("EyeHeightAsMeters"):
            v["vrc_height"] = f"{float(p['EyeHeightAsMeters']):.2f} m"
        if p.get("ScaleFactor"):
            v["vrc_scale"] = f"{float(p['ScaleFactor']):.2f}x"
        if p.get("Seated"):
            v["vrc_seated"] = "seated"
        if p.get("Earmuffs"):
            v["vrc_earmuffs"] = "earmuffs"
        return v

    def set_afk(self, afk: bool):
        if afk and self.afk_since is None:
            self.afk_since = time.time()
        elif not afk:
            self.afk_since = None

    def changed(self):
        """Call after the GUI changed the config."""
        self._configure_watchers()

    def used_keys(self, only_shown: bool = True) -> set:
        import blocks as B
        out = set()
        for b in self.cfg["blocks"]:
            if not only_shown or self._shown(b):
                out |= set(B.keys_of(b))
        return out

    def _configure_watchers(self):
        import sysinfo
        keys = self.used_keys() | set(self._extra_keys)
        media_keys = {"title", "artist", "album", "player", "site", "position", "duration",
                      "bar", "status", "lyric_line", "lyric_next"}
        self.media.configure(bool(keys & (media_keys | {"audio_app"})), "audio_app" in keys,
                             bool(keys & {"active_app", "window_title"}))
        known = {k for k, _d, _g in sysinfo.PLACEHOLDERS}
        sys_keys = keys & known
        self.stats.want(bool(sys_keys), int(self.cfg.get("gpu_index", 0) or 0), sys_keys)
        ignore = [str(x).lower() for x in self.cfg["media_filters"].get("fallback_ignore", [])
                  if str(x).strip()]
        self.media.audio_skip = lambda s, ig=tuple(ignore): any(i in s.lower() for i in ig)

    _extra_keys: tuple = ()

    def want_all_values(self, on: bool):
        """While the block list is on screen, read everything so it can show values."""
        import blocks as B
        self._extra_keys = tuple(k for k, _d, _g in B.CATALOG) if on else ()
        self.stats.hold(on)
        self._configure_watchers()

    def _shown(self, b: dict) -> bool:
        import blocks as B
        if b["type"] == "speech":
            return bool(b.get("enabled", True)) and b.get("visible", True) and \
                "speech" not in self.cfg.get("hidden_groups", [])
        if not b.get("visible", True):
            return False
        if self.cfg.get("hide_extras"):
            return False
        hidden = set(self.cfg.get("hidden_groups", []))
        return not (hidden and B.groups_of(b) & hidden)

    # ---- speech timing ------------------------------------------------------
    def _expire(self, now: float):
        """Drops finished speech items. Must hold the lock."""
        while self._speech:
            cur = self._speech[0]
            if cur.start is None:
                cur.start = now
            if cur.frames is None:
                return
            end = cur.cut_at if cur.cut_at is not None else cur.start + cur.total()
            if len(self._speech) > 1:
                if now >= end:
                    self._speech.pop(0)
                    self._speech[0].start = end if now - end < 1 else now
                    continue
                return
            hold = float(self.speech_element().get("hold_seconds", 6))
            if now >= end + hold:
                self._speech.pop(0)
                continue
            return

    def _speech_text(self, now: float, width: int) -> str:
        el = self.speech_element()
        with self._lock:
            self._expire(now)
            if not self._speech:
                return ""
            cur = self._speech[0]
            if cur.frames is None:
                cur.build(el, width, float(self.cfg.get("min_interval", 1.2)))
                if cur.start is None:
                    cur.start = now
            t = now - cur.start
            idx = len(cur.frames) - 1
            for i, (_text, dur) in enumerate(cur.frames):
                if t < dur:
                    idx = i
                    break
                t -= dur
            if idx != cur.frame_idx:
                cur.frame_idx, cur.frame_started = idx, now
            return cur.frames[idx][0]

    def speech_active(self, now: Optional[float] = None) -> bool:
        with self._lock:
            self._expire(now or self.clock())
            return bool(self._speech)

    # ---- values -------------------------------------------------------------
    last_values: dict = {}
    room_language: Callable[[], Optional[str]] = staticmethod(lambda: None)

    def values(self, now_wall: float, keys: set) -> dict:
        """All values the blocks use right now (without block options)."""
        v = dict(self.stats.values)
        v.update(self.vrc_values())
        media_keys = {"title", "artist", "album", "player", "site", "position", "duration",
                      "bar", "status", "audio_app"}
        if keys & (media_keys | {"lyric_line", "lyric_next"}):
            media, audio = self.media.snapshot()
            filters = self.cfg["media_filters"]
            item, why = M.choose(media, audio, filters, bool(self.cfg.get("show_paused")))
            if item is not None and why == "audio":
                v["audio_app"] = item.app
            elif item is not None:
                pos = item.current_position()
                v.update(title=item.title, artist=truncate(item.artist, 40),
                         album=truncate(item.album, 40), player=item.app, site=item.site or "",
                         position=fmt_clock(pos), duration=fmt_clock(item.duration),
                         bar=progress_bar(pos, item.duration),
                         status="▶" if item.playing else "⏸")
            if keys & {"lyric_line", "lyric_next"}:
                it2, why2 = M.choose(media, [], filters, False)
                if it2 is not None and why2 == "playing":
                    ly_cfg = self.cfg.get("lyrics") or {}
                    ly = self.lyrics.get(it2, bool(ly_cfg.get("online")),
                                         str(ly_cfg.get("folder", "")))
                    pos = it2.current_position()
                    if ly is not None and pos is not None:
                        line, nxt = ly.at(pos + float(ly_cfg.get("offset") or 0))
                        if line:
                            v["lyric_line"], v["lyric_next"] = line, nxt or ""
        if self.afk_since is not None:
            v["afk"] = "AFK"
            v["afk_time"] = fmt_duration(time.time() - self.afk_since)
        if "playtime" in keys:
            start = self.vrchat.get()
            if start is not None:
                v["playtime"] = fmt_duration(time.time() - start)
        v["app_time"] = fmt_duration(time.time() - self.app_started)
        try:
            code = self.room_language()
            if code:
                import languages
                v["room_lang"] = languages.name(code)
        except Exception:
            pass
        dt = datetime.now()                     # local clock (blocks can pick a time zone)
        v.update(time=dt.strftime("%H:%M"), date=dt.strftime("%d %b %Y"),
                 weekday=dt.strftime("%A"))
        if keys & {"active_app", "window_title"}:
            a = self.media.active
            import os
            if a and a[0] != os.getpid():
                pid, proc, title = a
                v["active_app"] = M.friendly_name(proc) if proc else ""
                v["window_title"] = truncate(title, 50)
        return v

    def block_values(self, b: dict, base: dict) -> dict:
        """Adds the values that depend on this block's options (time zone, countdown...)."""
        import blocks as B
        ks = set(B.keys_of(b))
        o = b.get("opts") or {}
        v = base
        if ks & {"time", "date", "weekday", "tz"}:
            v = dict(v)
            tz = o.get("timezone", "local")
            dt = now_in(tz)
            t = dt.strftime(CLOCK_STYLES.get(o.get("style", "24h"), "%H:%M"))
            if o.get("style", "").startswith("12h"):
                t = t.lstrip("0")
            v.update(time=t, date=dt.strftime("%d %b %Y"), weekday=dt.strftime("%A"),
                     tz=tz_label(tz))
        if any(k.startswith("countdown_") for k in ks):
            v = dict(v)
            raw = str(o.get("target", "")).strip()
            target = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    target = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    pass
            if target is not None:
                left = (target - datetime.now()).total_seconds()
                v["countdown_label"] = str(o.get("label", ""))
                v["countdown_date"] = target.strftime("%d %b %Y %H:%M")
                if left > 0:
                    v["countdown_left"] = fmt_duration(left, left < 3600)
                else:
                    v["countdown_done"] = str(o.get("label", "")) or "now"
        if "title" in ks and int(o.get("max_title") or 0) and v.get("title"):
            v = dict(v)
            v["title"] = truncate(v["title"], int(o["max_title"]))
        return v

    def fill_block(self, template: str, values: dict) -> str:
        """A text block: shows only when every value in it (outside [ ]) has something.
        Parts in [ ] disappear on their own when their value is empty."""
        import blocks as B
        known = set(B.KEY_INFO)
        outside = _PH.findall(_GROUP.sub("", template))
        if any(k in known and not str(values.get(k, "") or "").strip() for k in outside):
            return ""
        used = set(_PH.findall(template)) & known
        v = {k: "" for k in used}
        v.update(values)
        return fill(template, v)

    def render_block(self, b: dict, base: dict, now_wall: float) -> str:
        t = b["type"]
        v = self.block_values(b, base)
        if t == "value":
            text = str(v.get(b.get("key", ""), "") or "")
        elif t == "text":
            tpl = str(b.get("text", ""))
            if _PH.search(tpl):
                text = self.fill_block(tpl, v)
            else:
                text = tpl.replace("\\n", "\n")           # plain text: exactly as typed
        elif t == "rotating":
            msgs = [m.strip() for m in str(b.get("messages", "")).splitlines() if m.strip()]
            if not msgs:
                return ""
            slot = int(now_wall // max(2.0, float(b.get("interval", 10))))
            m = msgs[random.Random(slot).randrange(len(msgs))] if b.get("random") \
                else msgs[slot % len(msgs)]
            text = self.fill_block(m, v)
        else:
            return ""
        return window_text(text, b, now_wall, float(self.cfg.get("min_interval", 1.2)))

    # ---- building -----------------------------------------------------------
    last_parts: list = []
    SPEECH_MARK = "\x00"

    @staticmethod
    def join_line(items) -> str:
        """Value blocks get a space between them; text blocks are joined as typed."""
        out, prev = "", None
        for b, text in items:
            if prev is not None and b["type"] != "text" and prev["type"] != "text":
                out += " "
            out += text
            prev = b
        return out

    def assemble(self, blocks: list, texts: dict, skip: set) -> str:
        lines = [[]]
        for b in blocks:
            if b["type"] == "newline":
                lines.append([])
            elif b["id"] not in skip and texts.get(b["id"]):
                lines[-1].append((b, texts[b["id"]]))
        out = [self.join_line(items) for items in lines]
        return "\n".join(x for x in out if x.strip())

    def build(self, now: Optional[float] = None, now_wall: Optional[float] = None,
              sample: Optional[str] = None) -> str:
        """The chatbox message right now. `sample` shows that text as speech instead
        (used by "Show sample" in the GUI)."""
        msg, parts = self.build_parts(now, now_wall, sample)
        if sample is None:
            self.last_parts = parts
        return msg

    def build_parts(self, now: Optional[float] = None, now_wall: Optional[float] = None,
                    sample: Optional[str] = None):
        """(message, parts). parts: one dict per block, in order:
        {"id", "text", "state"} with state shown / empty / dropped / hidden / idle / off."""
        now = self.clock() if now is None else now
        now_wall = time.time() if now_wall is None else now_wall
        allb = self.cfg["blocks"]
        shown = [b for b in allb if self._shown(b)]
        sp = next((b for b in shown if b["type"] == "speech"), None)
        active = sp is not None and (sample is not None or self.speech_active(now))
        keys = set()
        import blocks as B
        for b in shown:
            keys |= set(B.keys_of(b))
        keys |= set(self._extra_keys)
        try:
            base = self.values(now_wall, keys)
        except Exception as ex:
            base = dict(self.stats.values)
            self._log_once("values", f"Chatbox values failed: {ex}")
        self.last_values = base

        texts = {}
        for b in shown:
            if b["type"] in ("speech", "newline"):
                continue
            try:
                texts[b["id"]] = self.render_block(b, base, now_wall)
            except Exception as ex:
                texts[b["id"]] = ""
                self._log_once(f"render:{b['id']}", f"Chatbox block failed: {ex}")

        skip: set = set()
        if active and self.cfg.get("speech_alone"):
            skip |= {b["id"] for b in shown if b["type"] != "speech"}

        # how much room speech wants
        need = 0
        if active:
            width_cfg = min(int(sp.get("width") or LIMIT), LIMIT)
            icon = str(sp.get("icon", "") or "").strip()
            pad = ulen(icon) + 1 if icon else 0
            with self._lock:
                cur = self._speech[0] if self._speech else None
                if sample is not None:
                    need = min(width_cfg, max(1, len(sample)))
                elif cur is None:
                    need = 1
                elif cur.frames is not None:
                    need = cur.width                    # keep the page size stable
                else:
                    need = min(width_cfg, max(1, len(cur.shown_text(sp))))
            texts[sp["id"]] = self.SPEECH_MARK * (need + pad)
            speech_prio = int(sp.get("prio", 1))
        else:
            speech_prio = 0

        # Make room: the highest priority number goes first (all blocks with it together).
        # 0 is never removed; while you talk only numbers above speech's make room.
        dropped = []
        while ulen(self.assemble(shown, texts, skip)) > LIMIT:
            cands = [int(b.get("prio", 2)) for b in shown
                     if b["type"] not in ("speech", "newline") and b["id"] not in skip
                     and texts.get(b["id"]) and int(b.get("prio", 2)) > speech_prio]
            if not cands:
                break
            top = max(cands)
            for b in shown:
                if b["type"] not in ("speech", "newline") and b["id"] not in skip and \
                        texts.get(b["id"]) and int(b.get("prio", 2)) == top:
                    skip.add(b["id"])
                    dropped.append(b)
        self.last_dropped = dropped

        speech = ""
        if active:
            texts[sp["id"]] = self.SPEECH_MARK
            free = LIMIT - (ulen(self.assemble(shown, texts, skip)) - 1)
            width = max(10, min(need if need > 1 else width_cfg, free - pad))
            if sample is not None:
                speech = with_icon(icon, sample[:width])
            else:
                speech = with_icon(icon, self._speech_text(now, width))
            texts[sp["id"]] = speech
        msg = self.assemble(shown, texts, skip).strip()

        info = []
        shown_ids = {b["id"] for b in shown}
        dropped_ids = {b["id"] for b in dropped} | (skip - {b["id"] for b in dropped})
        for b in allb:
            bid = b["id"]
            if b["type"] == "speech" and not b.get("enabled", True):
                st, txt = "off", ""
            elif bid not in shown_ids:
                st, txt = "hidden", ""
            elif b["type"] == "newline":
                st, txt = "shown", ""
            elif b["type"] == "speech":
                st, txt = ("shown", speech) if active else ("idle", "")
            elif bid in dropped_ids and texts.get(bid):
                st, txt = "dropped", texts.get(bid, "")
            elif texts.get(bid):
                st, txt = "shown", texts[bid]
            else:
                st, txt = "empty", ""
            info.append({"id": bid, "text": txt, "state": st})
        return truncate16(msg, LIMIT), info

    _logged: set = set()

    def _log_once(self, key, msg):
        if key not in self._logged:
            self._logged.add(key)
            self.log(msg)

    # ---- sending ------------------------------------------------------------
    def _really_send(self, msg: str):
        if not self.paused_sending:
            try:
                self._send(msg)
            except Exception as e:
                self._log_once("send", f"Chatbox send failed: {e}")
        self.last_sent = msg
        self.last_sent_at = self.clock()
        self._last_preview = msg
        self.on_preview(msg, True)

    def tick(self, now: Optional[float] = None) -> Optional[str]:
        """One step of the send loop. Returns the message if one was sent."""
        now = self.clock() if now is None else now
        msg = self.build(now)
        if msg != self._last_preview:
            self._last_preview = msg
            self.on_preview(msg, False)
        min_int = max(1.0, float(self.cfg.get("min_interval", 1.2)))
        since = now - self.last_sent_at
        if msg != self.last_sent and since >= min_int:
            self._really_send(msg)
            return msg
        keep = float(self.cfg.get("keepalive", 20) or 0)
        if msg and msg == self.last_sent and keep and since >= max(keep, min_int):
            self._really_send(msg)
            return msg
        return None

    def _loop(self):
        while True:
            try:
                self.tick()
            except Exception as e:
                self._log_once("tick", f"Chatbox error: {e}")
            time.sleep(0.2)
