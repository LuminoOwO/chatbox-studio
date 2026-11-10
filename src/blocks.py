"""
Chatbox blocks (1.19+): the chatbox is a row of small blocks you drag into place.

Block types
  speech    your transcribed / translated sentences (only one)
  value     one value, e.g. {gpu_name}, {time}, {title}
  text      your own text; it can hold values ("CPU: {cpu}%") and then only shows when
            all of them have something. Parts in [ ] disappear on their own.
  rotating  messages that take turns
  newline   starts a new line in the chatbox

Joining: blocks on one line are put next to each other. Two value blocks get a space
between them; text blocks are joined exactly as typed (they bring their own spaces).

Priority: a number per block. Speech is 1, everything else 2 by default. When the chatbox
runs out of room, the highest number goes first - all blocks with that number together -
then the next. 0 = never removed. While you talk, only blocks with a higher number than
speech make room.
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Optional

import sysinfo

# --------------------------------------------------------------------------- #
# Catalog of everything you can put into the chatbox
# --------------------------------------------------------------------------- #
# (key, label, category)
OWN_KEYS = [
    ("time", "Time", "Time & date"),
    ("date", "Date", "Time & date"),
    ("weekday", "Weekday", "Time & date"),
    ("tz", "Time zone name", "Time & date"),
    ("countdown_left", "Countdown: time left", "Countdown"),
    ("countdown_label", "Countdown: name", "Countdown"),
    ("countdown_date", "Countdown: date", "Countdown"),
    ("countdown_done", "Countdown: name, once it's over", "Countdown"),
    ("title", "Song / video title", "Music"),
    ("artist", "Artist", "Music"),
    ("album", "Album", "Music"),
    ("player", "Player (Spotify, Firefox, ...)", "Music"),
    ("site", "Website (YouTube, ...)", "Music"),
    ("position", "Position (1:23)", "Music"),
    ("duration", "Length (3:45)", "Music"),
    ("bar", "Progress bar ━━●──", "Music"),
    ("status", "▶ playing / ⏸ paused", "Music"),
    ("audio_app", "Program playing sound (no song info)", "Music"),
    ("lyric_line", "Lyrics: current line", "Music"),
    ("lyric_next", "Lyrics: next line", "Music"),
    ("afk", "“AFK” while you're AFK", "Status"),
    ("afk_time", "How long you're AFK", "Status"),
    ("playtime", "Time in VRChat", "Status"),
    ("app_time", "Time since this app started", "Status"),
    ("active_app", "Program in the foreground", "Status"),
    ("window_title", "Window title (careful: private!)", "Status"),
    ("room_lang", "Language people around you speak (Earshot)", "Status"),
]
SYS_KEYS = [(k, d, g) for k, d, g in sysinfo.PLACEHOLDERS]
CATALOG = OWN_KEYS + SYS_KEYS
KEY_INFO = {k: (d, g) for k, d, g in CATALOG}
CATEGORIES = ["Speech", "Text & layout"] + list(dict.fromkeys(g for _k, _d, g in CATALOG))

# Short names shown on the blocks themselves
SHORT = {"cpu": "CPU %", "gpu": "GPU %", "ram": "RAM %", "time": "Time", "title": "Title"}


_CAT_PREFIX = {"CPU": "CPU", "Graphics": "GPU"}


def short_name(key: str) -> str:
    if key in SHORT:
        return SHORT[key]
    d, cat = KEY_INFO.get(key, (key, ""))
    d = re.sub(r"\s*\(([^)]*)\)$", lambda m: " %" if m.group(1) == "%" else "", d) or key
    pre = _CAT_PREFIX.get(cat, "")
    if pre and not any(w in d.lower() for w in (pre.lower(), "graphics", "vram", "driver",
                                                "pcie", "all ")):
        d = f"{pre} {d[0].lower()}{d[1:]}" if not d[1:2].isupper() else f"{pre} {d}"
    return d


# Privacy groups (buttons under the title): which keys belong to which group
PRIVACY_GROUPS = [("speech", "💬 Speech"), ("text", "📝 Text"), ("time", "🕒 Time"),
                  ("music", "🎵 Music"), ("status", "💤 Status"), ("pc", "💻 PC"),
                  ("vr", "🥽 VR"), ("vrchat", "🎮 VRChat")]
_VR_GROUPS = {"VR headset", "WiVRn", "Controllers & trackers"}


def privacy_group(key: str) -> str:
    cat = KEY_INFO.get(key, ("", ""))[1]
    if cat in ("Time & date", "Countdown"):
        return "time"
    if cat == "Music":
        return "music"
    if cat == "Status":
        return "status"
    if cat in _VR_GROUPS:
        return "vr"
    if cat == "VRChat":
        return "vrchat"
    return "pc"


_PH = re.compile(r"\{(\w+)\}")


def keys_of(block: dict) -> list[str]:
    t = block.get("type")
    if t == "value":
        return [block.get("key", "")]
    if t == "text":
        return _PH.findall(str(block.get("text", "")))
    if t == "rotating":
        return _PH.findall(str(block.get("messages", "")))
    return []


def groups_of(block: dict) -> set:
    t = block.get("type")
    if t == "speech":
        return {"speech"}
    ks = [k for k in keys_of(block) if k in KEY_INFO]
    return {privacy_group(k) for k in ks} or ({"text"} if t in ("text", "rotating") else set())


# Options a block can have, by the categories of the values in it
def option_sets(block: dict) -> list[str]:
    cats = {KEY_INFO[k][1] for k in keys_of(block) if k in KEY_INFO}
    out = []
    if "Time & date" in cats:
        out.append("time")
    if "Countdown" in cats:
        out.append("countdown")
    if "title" in keys_of(block) or "artist" in keys_of(block):
        out.append("media")
    if cats & {"Graphics"}:
        out.append("gpu")
    return out


# --------------------------------------------------------------------------- #
# Making blocks
# --------------------------------------------------------------------------- #
SPEECH_DEFAULTS = {"width": 144, "mode": "pages", "page_seconds": 0.0, "scroll_words": 3,
                   "scroll_seconds": 1.6, "hold_seconds": 6.0, "flow": "live", "limit": 0}
DISPLAY_DEFAULTS = {"show_mode": "full", "win_chars": 40, "win_step": 2, "win_seconds": 2.0,
                    "win_lines": 1}
OPT_DEFAULTS = {"timezone": "local", "style": "24h", "target": "2026-12-24 18:00",
                "label": "Christmas", "max_title": 40}


def new_block(kind: str, **over) -> dict:
    b = {"id": uuid.uuid4().hex[:8], "type": kind, "prio": 1 if kind == "speech" else 2,
         "visible": True}
    if kind == "speech":
        b.update(SPEECH_DEFAULTS)
        b["enabled"] = True
    elif kind == "value":
        b["key"] = "time"
    elif kind == "text":
        b["text"] = "Text"
    elif kind == "rotating":
        b.update(messages="Hello!\nHave a nice day ✨",
                 interval=10.0, random=False)
    if kind not in ("speech", "newline"):
        b.update(DISPLAY_DEFAULTS)
        b["opts"] = dict(OPT_DEFAULTS)
    b.update(over)
    if "opts" in over:
        b["opts"] = {**OPT_DEFAULTS, **over["opts"]}
    return b


# Ready-made text blocks in the list ("Text & layout")
TEXT_STARTERS = [
    ("Text", "Text", "Your own text - click it to type"),
    (" · ", " · ", "Separator"),
    (" | ", " | ", "Separator"),
    (" — ", " — ", "Separator"),
    ("Space", " ", "A space"),
    ("CPU: {cpu}%", "CPU: {cpu}%", "Text with a value - only shown while it has one"),
    ("🎵 {title}[ — {artist}]", "🎵 {title}[ — {artist}]", "Song and artist"),
    ("🕒 {time}", "🕒 {time}", "Clock"),
    ("💤 AFK for {afk_time}", "💤 AFK for {afk_time}", "Only while you're AFK"),
]


def default_blocks() -> list:
    return [new_block("speech")]


# --------------------------------------------------------------------------- #
# Converting the element setups of 1.18 and older
# --------------------------------------------------------------------------- #
PRIO_NUM = {"keep": 0, "high": 1, "normal": 2, "low": 3}
_RENAME = {
    "countdown": {"label": "countdown_label", "left": "countdown_left", "date": "countdown_date"},
    "media": {"app": "player"},
    "lyrics": {"line": "lyric_line", "next": "lyric_next"},
    "afk": {"duration": "afk_time"},
    "window": {"app": "active_app", "title": "window_title"},
}


def _rename(fmt: str, kind: str, extra: Optional[dict] = None) -> str:
    table = dict(_RENAME.get(kind, {}))
    table.update(extra or {})
    return _PH.sub(lambda m: "{" + table.get(m.group(1), m.group(1)) + "}", fmt or "")


def _icon(icon: str, text: str) -> str:
    icon = (icon or "").strip()
    return f"{icon} {text}" if icon and text else text


def migrate(cfg: dict) -> dict:
    """1.18 'elements' -> 1.19 'blocks'. The old list is kept as 'elements_old'."""
    import chatbox as C
    import media as M
    old = C.normalize_elements(copy.deepcopy(cfg))
    blocks: list = []
    sep = old.get("separator", "newline")
    sep_text = {"bar": " | ", "dot": " · ", "space": "  "}.get(
        sep, str(old.get("custom_separator", " | ")).replace("\\n", "\n"))
    media_filters = None
    lyrics = {}
    gpu_index = 0
    speech_seen = False

    def add_sep():
        if not blocks:
            return
        if sep == "newline" or sep_text == "\n":
            blocks.append(new_block("newline"))
        else:
            blocks.append(new_block("text", text=sep_text, prio=blocks[-1].get("prio", 2)))

    for el in old.get("elements", []):
        kind = el["type"]
        prio = PRIO_NUM.get(el.get("priority", "normal"), 2)
        disp = {k: el[k] for k in DISPLAY_DEFAULTS if k in el}
        common = dict(prio=prio, visible=el.get("visible", True), **disp)
        if kind == "media" and media_filters is None:
            media_filters = el.get("filters")
        if kind == "lyrics":
            lyrics = {"online": bool(el.get("online")), "folder": el.get("folder", ""),
                      "offset": float(el.get("offset") or 0)}
        if kind == "text":
            gpu_index = int(el.get("gpu_index") or gpu_index)
        enabled = el.get("enabled", True)
        if kind == "speech":
            speech_seen = True
            b = new_block("speech", **{k: el[k] for k in SPEECH_DEFAULTS if k in el})
            b.update(prio=prio, enabled=enabled, visible=el.get("visible", True))
            if el.get("icon"):
                b["icon"] = el["icon"]
            if not enabled:
                blocks.append(b)       # kept (switched off) so its settings survive
                continue
            add_sep()
            blocks.append(b)
            continue
        if not enabled:
            continue
        add_sep()
        icon = el.get("icon", "")
        if kind == "text":
            text = str(el.get("text", ""))
            lines = [x for x in text.replace("\\n", "\n").split("\n")]
            if el.get("show_mode", "full") == "full" and len(lines) > 1:
                first = True
                for line in lines:
                    if not first:
                        blocks.append(new_block("newline"))
                    blocks.append(new_block("text", text=_icon(icon, line) if first else line,
                                            **common))
                    first = False
            else:
                blocks.append(new_block("text", text=_icon(icon, text), **common))
        elif kind == "rotating":
            blocks.append(new_block("rotating", messages=el.get("messages", ""),
                                    interval=el.get("interval", 10.0),
                                    random=bool(el.get("random")), **common))
            if icon:
                blocks.insert(len(blocks) - 1, new_block("text", text=icon + " ", prio=prio))
        elif kind == "time":
            blocks.append(new_block("text", text=_icon(icon, el.get("format", "{time}")),
                                    opts={"timezone": el.get("timezone", "local"),
                                          "style": el.get("style", "24h")}, **common))
        elif kind == "countdown":
            opts = {"target": el.get("target", ""), "label": el.get("label", "")}
            blocks.append(new_block("text", text=_icon(icon, _rename(el.get("format", ""), kind)),
                                    opts=opts, **common))
            done = str(el.get("done", "") or "")
            if done.strip():
                blocks.append(new_block("text", text=_rename(done, kind, {"label": "countdown_done"}),
                                        opts=opts, **common))
        elif kind == "media":
            blocks.append(new_block("text", text=_icon(icon, _rename(el.get("format", ""), kind)),
                                    opts={"max_title": int(el.get("max_title") or 0)}, **common))
            if el.get("filters", {}).get("fallback", True):
                fb = _rename(el.get("fallback_format", "{app}"), kind, {"app": "audio_app"})
                blocks.append(new_block("text", text=_icon(el.get("fallback_icon", ""), fb),
                                        **common))
        elif kind == "lyrics":
            blocks.append(new_block("text", text=_icon(icon, _rename(el.get("format", ""), kind)),
                                    **common))
        elif kind == "afk":
            blocks.append(new_block("text", text=_icon(icon, _rename(el.get("format", ""), kind)),
                                    **common))
        elif kind == "playtime":
            key = "playtime" if el.get("since", "vrchat") == "vrchat" else "app_time"
            blocks.append(new_block("text", text=_icon(icon, _rename(el.get("format", ""), kind,
                                                                     {"elapsed": key})),
                                    **common))
        elif kind == "window":
            fmt = _rename(el.get("format", "{app}"), kind)
            if not el.get("show_title"):
                fmt = fmt.replace("{window_title}", "")
            blocks.append(new_block("text", text=_icon(icon, fmt.strip()), **common))
    if not speech_seen:
        blocks.insert(0, new_block("speech"))
    # drop separators that ended up at the very end
    while blocks and blocks[-1]["type"] == "newline":
        blocks.pop()
    cfg["blocks"] = blocks
    cfg["elements_old"] = cfg.pop("elements", [])
    cfg["media_filters"] = media_filters or M.default_filters()
    cfg.setdefault("lyrics", {"online": False, "folder": "", "offset": 0.0})
    cfg["lyrics"].update(lyrics)
    cfg.setdefault("gpu_index", gpu_index)
    return cfg


def normalize(cfg: Optional[dict]) -> dict:
    import media as M
    if not cfg:
        cfg = {}
    if "blocks" not in cfg and cfg.get("elements"):
        cfg = migrate(cfg)
    cfg.setdefault("blocks", default_blocks())
    cfg.setdefault("speech_alone", False)
    cfg.setdefault("min_interval", 1.2)
    cfg.setdefault("keepalive", 20.0)
    cfg.setdefault("hide_extras", False)
    cfg.setdefault("hidden_groups", [])
    cfg.setdefault("lyrics", {"online": False, "folder": "", "offset": 0.0})
    cfg.setdefault("gpu_index", 0)
    cfg.setdefault("show_paused", False)
    f = cfg.get("media_filters") or {}
    for k, v in M.default_filters().items():
        f.setdefault(k, v)
    cfg["media_filters"] = f
    out, speech = [], False
    for b in cfg["blocks"]:
        t = b.get("type")
        if t not in ("speech", "value", "text", "rotating", "newline"):
            continue
        if t == "speech":
            if speech:
                continue
            speech = True
            for k, v in SPEECH_DEFAULTS.items():
                b.setdefault(k, v)
            b.setdefault("enabled", True)
            if b.get("flow") not in ("live", "queue"):
                b["flow"] = "live"
        b.setdefault("id", uuid.uuid4().hex[:8])
        b.setdefault("visible", True)
        try:
            b["prio"] = max(0, int(b.get("prio", 1 if t == "speech" else 2)))
        except (TypeError, ValueError):
            b["prio"] = 2
        if t not in ("speech", "newline"):
            for k, v in DISPLAY_DEFAULTS.items():
                b.setdefault(k, v)
            b["opts"] = {**OPT_DEFAULTS, **(b.get("opts") or {})}
        if t == "value":
            b.setdefault("key", "time")
        if t == "text":
            b.setdefault("text", "")
        out.append(b)
    if not speech:
        out.insert(0, new_block("speech"))
    cfg["blocks"] = out
    return cfg
