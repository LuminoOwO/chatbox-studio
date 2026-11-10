"""
"Now playing" and per-program audio detection, on Linux and Windows.

               Linux                                 Windows
media      MPRIS over D-Bus (jeepney)            System Media Transport Controls (winrt)
audio      PipeWire/PulseAudio streams with      Core Audio sessions with peak meters
           peak levels (pulsectl)                (pycaw)
windows    X11 window titles (python-xlib)       Win32 window titles (ctypes)

Window titles are used to find out which website a browser is playing from
("Song - YouTube - Mozilla Firefox" -> YouTube), because browsers don't report it.

Everything here is optional: if a library is missing or blocked, that part just
reports nothing and the rest keeps working.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlparse

IS_WINDOWS = sys.platform == "win32"


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass
class MediaItem:
    kind: str                       # "media" (title known) or "audio" (only the program)
    app: str                        # friendly program name, e.g. "Firefox"
    app_id: str = ""                # raw id: exe name, MPRIS bus name, Windows AppUserModelId
    title: str = ""
    artist: str = ""
    album: str = ""
    playing: bool = False
    position: Optional[float] = None    # seconds
    duration: Optional[float] = None    # seconds
    stamp: float = 0.0              # time.monotonic() when position was read
    url: str = ""
    site: str = ""                  # "YouTube", "Twitch", ... if known
    window: str = ""                # matching window title (browsers)
    pid: Optional[int] = None
    level: float = 0.0              # audio peak 0..1 (kind == "audio")

    def current_position(self, now: Optional[float] = None) -> Optional[float]:
        if self.position is None:
            return None
        pos = self.position
        if self.playing:
            pos += (now or time.monotonic()) - self.stamp
        if self.duration:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def fields(self) -> dict:
        """Text that filter rules are matched against."""
        return {
            "app": f"{self.app} {self.app_id}",
            "site": f"{self.site} {self.url} {self.window}",
            "title": f"{self.title} {self.artist} {self.album}",
        }


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #
FRIENDLY = {
    "firefox": "Firefox", "firefox-bin": "Firefox", "firefox-esr": "Firefox",
    "308046b0af4a39cb": "Firefox", "f0dc299d809b9700": "Firefox", "mozilla firefox": "Firefox",
    "chrome": "Chrome", "google-chrome": "Chrome", "google chrome": "Chrome",
    "chromium": "Chromium", "chromium-browser": "Chromium",
    "msedge": "Edge", "microsoft-edge": "Edge", "microsoft edge": "Edge",
    "brave": "Brave", "brave-browser": "Brave", "opera": "Opera", "vivaldi": "Vivaldi",
    "librewolf": "LibreWolf", "zen": "Zen", "floorp": "Floorp", "waterfox": "Waterfox",
    "spotify": "Spotify", "vlc": "VLC", "mpv": "mpv", "rhythmbox": "Rhythmbox",
    "elisa": "Elisa", "strawberry": "Strawberry", "audacious": "Audacious",
    "clementine": "Clementine", "celluloid": "Celluloid", "totem": "Videos",
    "microsoft.zunemusic": "Media Player", "microsoft.zunevideo": "Movies & TV",
    "itunes": "iTunes", "applemusic": "Apple Music", "foobar2000": "foobar2000",
    "musicbee": "MusicBee", "aimp": "AIMP", "winamp": "Winamp", "deezer": "Deezer",
    "tidal": "TIDAL", "amazon music": "Amazon Music", "plexamp": "Plexamp",
    "discord": "Discord", "vesktop": "Discord", "webrtc voiceengine": "Discord",
    "steam": "Steam", "steamwebhelper": "Steam", "vrchat": "VRChat",
    "soundpad": "Soundpad", "voicemod": "Voicemod", "soundux": "Soundux",
    "voicemeeter": "Voicemeeter", "obs": "OBS", "obs64": "OBS",
}

BROWSERS = {
    "Firefox": ["firefox"], "Chrome": ["chrome"], "Chromium": ["chromium", "chrome"],
    "Edge": ["msedge", "edge"], "Brave": ["brave"], "Opera": ["opera"],
    "Vivaldi": ["vivaldi"], "LibreWolf": ["librewolf"], "Zen": ["zen"],
    "Floorp": ["floorp"], "Waterfox": ["waterfox"],
}

SITES = [  # (name, hostname suffixes, text that appears in window titles)
    ("YouTube Music", ["music.youtube.com"], ["youtube music"]),
    ("YouTube", ["youtube.com", "youtu.be"], ["youtube"]),
    ("Twitch", ["twitch.tv"], ["twitch"]),
    ("SoundCloud", ["soundcloud.com"], ["soundcloud"]),
    ("Spotify", ["spotify.com"], ["spotify"]),
    ("Netflix", ["netflix.com"], ["netflix"]),
    ("Bandcamp", ["bandcamp.com"], ["bandcamp"]),
    ("Apple Music", ["music.apple.com"], ["apple music"]),
    ("Deezer", ["deezer.com"], ["deezer"]),
    ("TIDAL", ["tidal.com"], ["tidal"]),
    ("Kick", ["kick.com"], ["kick"]),
    ("Vimeo", ["vimeo.com"], ["vimeo"]),
    ("Crunchyroll", ["crunchyroll.com"], ["crunchyroll"]),
    ("Disney+", ["disneyplus.com"], ["disney+"]),
    ("Prime Video", ["primevideo.com", "amazon."], ["prime video"]),
    ("Dailymotion", ["dailymotion.com"], ["dailymotion"]),
    ("Mixcloud", ["mixcloud.com"], ["mixcloud"]),
    ("Bilibili", ["bilibili.com"], ["bilibili", "哔哩哔哩"]),
    ("Niconico", ["nicovideo.jp"], ["niconico", "ニコニコ"]),
    ("TikTok", ["tiktok.com"], ["tiktok"]),
    ("Reddit", ["reddit.com"], ["reddit"]),
    ("X", ["x.com", "twitter.com"], [" / x", "twitter"]),
    ("Myinstants", ["myinstants.com"], ["myinstants"]),
]

BROWSER_SUFFIX = re.compile(
    r"\s+[-—–]\s+(Mozilla Firefox|Firefox|Google Chrome|Chromium|Microsoft​? Edge|"
    r"Brave|Opera|Vivaldi|LibreWolf|Zen Browser|Floorp|Waterfox)(\s+Private Browsing)?$",
    re.IGNORECASE)


def friendly_name(raw: str) -> str:
    """'firefox.exe' / 'org.mpris.MediaPlayer2.spotify' / AUMID -> 'Firefox' / 'Spotify'."""
    if not raw:
        return "Unknown"
    s = raw.strip()
    if s.startswith("org.mpris.MediaPlayer2."):
        s = s[len("org.mpris.MediaPlayer2."):]
        s = re.sub(r"\.instance_?\d+.*$|\.instance.*$", "", s)
    s = s.split("!")[-1] if "!" in s and not s.lower().startswith("microsoft.zune") else s
    low = s.lower()
    if low.startswith("microsoft.zune"):
        low = low.split("_")[0]
    low = re.sub(r"\.exe$", "", low)
    if "_" in low and "." in low:                 # Store package family names
        low = low.split("_")[0].split(".")[-1]
    if low in FRIENDLY:
        return FRIENDLY[low]
    for key, name in FRIENDLY.items():
        if len(key) > 4 and key in low:
            return name
    s = re.sub(r"\.exe$", "", s, flags=re.IGNORECASE)
    return s[:1].upper() + s[1:] if s.islower() else s


def site_from_url(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    if not host:
        return ""
    for name, hosts, _t in SITES:
        if any(host == h or host.endswith("." + h) or (h.endswith(".") and h in host)
               for h in hosts):
            return name
    return host[4:] if host.startswith("www.") else host


def site_from_title(title: str) -> str:
    """Finds the website in a browser window title."""
    low = title.lower()
    for name, _h, needles in SITES:
        if any(n in low for n in needles):
            return name
    # "Page - Site - Browser" -> "Site"
    rest = BROWSER_SUFFIX.sub("", title)
    parts = [p.strip() for p in re.split(r"\s+[-—–|]\s+", rest) if p.strip()]
    if len(parts) >= 2 and len(parts[-1]) <= 30:
        return parts[-1]
    return ""


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
DEFAULT_FALLBACK_IGNORE = [
    "VRChat", "Discord", "WEBRTC VoiceEngine", "Steam", "steamwebhelper", "wine",
    "vrserver", "vrcompositor", "vrmonitor", "SteamVR", "OVRServer", "Oculus",
    "System sounds", "speech-dispatcher", "TeamSpeak", "Mumble", "Zoom", "Teams",
]


def default_filters() -> dict:
    return {
        "mode": "hide",                 # hide = show everything except matches; only = only matches
        "rules": [],                    # [{"field": any|app|site|title, "text": "...", "enabled": True}]
        "fallback": True,               # show the program when audio plays without media info
        "fallback_ignore": list(DEFAULT_FALLBACK_IGNORE),
        "fallback_threshold": 0.02,
    }


def rule_matches(rule: dict, item: MediaItem) -> bool:
    text = str(rule.get("text", "")).strip().lower()
    if not text or not rule.get("enabled", True):
        return False
    f = item.fields()
    field_ = rule.get("field", "any")
    hay = " ".join(f.values()) if field_ == "any" else f.get(field_, "")
    return text in hay.lower()


def is_allowed(item: MediaItem, filters: dict) -> bool:
    rules = [r for r in filters.get("rules", []) if r.get("enabled", True)
             and str(r.get("text", "")).strip()]
    hit = any(rule_matches(r, item) for r in rules)
    if filters.get("mode", "hide") == "only":
        return hit
    return not hit


def _ignored_for_fallback(item: MediaItem, filters: dict) -> bool:
    hay = f"{item.app} {item.app_id}".lower()
    return any(str(x).strip().lower() in hay
               for x in filters.get("fallback_ignore", []) if str(x).strip())


def _same_program(a: MediaItem, b: MediaItem) -> bool:
    if a.pid and b.pid and a.pid == b.pid:
        return True
    return a.app.lower() == b.app.lower()


def choose(media: list[MediaItem], audio: list[MediaItem], filters: dict,
           show_paused: bool = False) -> tuple[Optional[MediaItem], str]:
    """Picks what the chatbox shows. Returns (item, why)."""
    visible = [m for m in media if is_allowed(m, filters)]
    hidden = [m for m in media if m not in visible]
    for m in visible:
        if m.playing:
            return m, "playing"
    if show_paused and visible:
        return visible[0], "paused"
    if not filters.get("fallback", True):
        return None, "nothing playing"
    thr = float(filters.get("fallback_threshold", 0.02))
    for a in sorted(audio, key=lambda x: -x.level):
        if a.level < thr or _ignored_for_fallback(a, filters):
            continue
        if any(_same_program(a, h) for h in hidden):
            continue            # its media is hidden by a filter: don't leak the program either
        if not is_allowed(a, filters):
            continue
        return a, "audio"
    return None, "nothing playing"


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
def _proc_name(pid: Optional[int]) -> str:
    if not pid:
        return ""
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


class LinuxBackend:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self._bus = None
        self._pulse = None
        self._x = None
        self._x_failed = False

    # ---- MPRIS ----
    def _conn(self):
        if self._bus is None:
            from jeepney.io.blocking import open_dbus_connection
            self._bus = open_dbus_connection(bus="SESSION")
        return self._bus

    def media(self) -> list[MediaItem]:
        from jeepney import DBusAddress, Properties
        from jeepney.bus_messages import message_bus
        try:
            conn = self._conn()
            names = conn.send_and_get_reply(message_bus.ListNames(), timeout=2).body[0]
        except Exception:
            self._bus = None
            raise
        out = []
        for name in sorted(n for n in names if n.startswith("org.mpris.MediaPlayer2.")):
            try:
                player = DBusAddress("/org/mpris/MediaPlayer2", bus_name=name,
                                     interface="org.mpris.MediaPlayer2.Player")
                props = conn.send_and_get_reply(Properties(player).get_all(), timeout=1).body[0]
                props = {k: v[1] for k, v in props.items()}
                meta = {k: v[1] for k, v in props.get("Metadata", {}).items()}
                try:
                    root = DBusAddress("/org/mpris/MediaPlayer2", bus_name=name,
                                       interface="org.mpris.MediaPlayer2")
                    identity = conn.send_and_get_reply(
                        Properties(root).get("Identity"), timeout=1).body[0][1]
                except Exception:
                    identity = ""
                try:
                    pid = int(conn.send_and_get_reply(
                        message_bus.GetConnectionUnixProcessID(name), timeout=1).body[0])
                except Exception:
                    pid = None
            except Exception:
                continue
            artist = meta.get("xesam:artist", "")
            if isinstance(artist, (list, tuple)):
                artist = ", ".join(str(a) for a in artist)
            length = meta.get("mpris:length")
            pos = props.get("Position")
            out.append(MediaItem(
                kind="media",
                app=friendly_name(identity or name),
                app_id=name,
                title=str(meta.get("xesam:title", "") or ""),
                artist=str(artist or ""),
                album=str(meta.get("xesam:album", "") or ""),
                playing=props.get("PlaybackStatus") == "Playing",
                position=(pos / 1e6) if isinstance(pos, int) and pos >= 0 else None,
                duration=(length / 1e6) if isinstance(length, int) and length > 0 else None,
                stamp=time.monotonic(),
                url=str(meta.get("xesam:url", "") or ""),
                pid=pid,
            ))
        return [m for m in out if m.title or m.playing]

    # ---- PipeWire / PulseAudio ----
    def audio(self, skip: Callable[[str], bool]) -> list[MediaItem]:
        import pulsectl
        try:
            if self._pulse is None:
                self._pulse = pulsectl.Pulse("chatbox-studio-meter")
            pulse = self._pulse
            streams = pulse.sink_input_list()
        except Exception:
            self._pulse = None
            raise
        out = []
        monitors = {}
        for si in streams[:12]:
            p = si.proplist
            if p.get("application.id") == "org.PulseAudio.pavucontrol":
                continue
            raw = p.get("application.name") or p.get("application.process.binary") or "Unknown"
            binary = p.get("application.process.binary", "")
            app = friendly_name(raw)
            if skip(f"{app} {raw} {binary}"):
                continue
            level = 0.0
            if not si.corked:
                try:
                    if si.sink not in monitors:
                        monitors[si.sink] = pulse.sink_info(si.sink).monitor_source
                    level = pulse.get_peak_sample(monitors[si.sink], 0.2, si.index)
                except Exception:
                    level = 0.0
            pid = p.get("application.process.id")
            out.append(MediaItem(kind="audio", app=app, app_id=f"{raw} {binary}".strip(),
                                 playing=level > 0, level=level,
                                 pid=int(pid) if str(pid or "").isdigit() else None))
        # one entry per program, loudest stream
        best: dict[str, MediaItem] = {}
        for a in out:
            if a.app not in best or a.level > best[a.app].level:
                best[a.app] = a
        return list(best.values())

    # ---- X11 windows ----
    def _display(self):
        if self._x is None and not self._x_failed:
            try:
                from Xlib import display
                self._x = display.Display()
            except Exception:
                self._x_failed = True       # Wayland-only or no display
        return self._x

    def windows(self) -> list[tuple[Optional[int], str, str]]:
        d = self._display()
        if d is None:
            return []
        from Xlib import X
        try:
            root = d.screen().root
            atom = d.intern_atom
            lst = root.get_full_property(atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
            out = []
            for wid in (lst.value if lst else []):
                w = d.create_resource_object("window", wid)
                out.append(self._win_info(d, w))
            return [w for w in out if w[2]]
        except Exception:
            self._x = None
            return []

    def _win_info(self, d, w):
        from Xlib import X
        atom = d.intern_atom
        try:
            name = w.get_full_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"))
            title = name.value.decode("utf-8", "replace") if name else (w.get_wm_name() or "")
            pidp = w.get_full_property(atom("_NET_WM_PID"), X.AnyPropertyType)
            pid = int(pidp.value[0]) if pidp and len(pidp.value) else None
        except Exception:
            return (None, "", "")
        if isinstance(title, bytes):
            title = title.decode("utf-8", "replace")
        return (pid, _proc_name(pid), str(title))

    def active_window(self):
        d = self._display()
        if d is None:
            return None
        from Xlib import X
        try:
            root = d.screen().root
            p = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
            if not p or not p.value or not p.value[0]:
                return None
            return self._win_info(d, d.create_resource_object("window", p.value[0]))
        except Exception:
            self._x = None
            return None


class WindowsBackend:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self._mgr = None
        self._media_thread_ready = False
        self._audio_thread_ready = False

    def media(self) -> list[MediaItem]:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
        )
        if not self._media_thread_ready:
            try:
                import winrt.runtime
                winrt.runtime.init_apartment(winrt.runtime.MTA)
            except Exception:
                pass                    # already initialised
            self._media_thread_ready = True
        if self._mgr is None:
            self._mgr = Manager.request_async().get()
        out = []
        try:
            sessions = list(self._mgr.get_sessions())
        except Exception:
            self._mgr = None
            raise
        for s in sessions:
            try:
                aumid = s.source_app_user_model_id or ""
                props = s.try_get_media_properties_async().get()
                info = s.get_playback_info()
                playing = info is not None and info.playback_status == Status.PLAYING
                pos = dur = None
                stamp = time.monotonic()
                try:
                    tl = s.get_timeline_properties()
                    end = tl.end_time.total_seconds()
                    if end > 0:
                        dur = end
                        pos = tl.position.total_seconds()
                        upd = tl.last_updated_time
                        if playing and upd is not None and upd.year > 1601:
                            import datetime as _dt
                            age = (_dt.datetime.now(_dt.timezone.utc) - upd).total_seconds()
                            if 0 < age < 24 * 3600:
                                pos += age
                except Exception:
                    pass
                out.append(MediaItem(
                    kind="media", app=friendly_name(aumid), app_id=aumid,
                    title=(props.title if props else "") or "",
                    artist=(props.artist if props else "") or "",
                    album=(props.album_title if props else "") or "",
                    playing=playing, position=pos, duration=dur, stamp=stamp,
                ))
            except Exception:
                continue
        return [m for m in out if m.title or m.playing]

    def audio(self, skip: Callable[[str], bool]) -> list[MediaItem]:
        if not self._audio_thread_ready:
            import comtypes
            comtypes.CoInitialize()
            self._audio_thread_ready = True
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        best: dict[str, MediaItem] = {}
        for s in AudioUtilities.GetAllSessions():
            try:
                proc = s.Process
                exe = proc.name() if proc else ""
                raw = exe or s.DisplayName or "System sounds"
                app = friendly_name(raw) if exe else "System sounds"
                if skip(f"{app} {raw}"):
                    continue
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                level = float(meter.GetPeakValue())
            except Exception:
                continue
            item = MediaItem(kind="audio", app=app, app_id=raw, playing=level > 0,
                             level=level, pid=s.ProcessId or None)
            if app not in best or level > best[app].level:
                best[app] = item
        return list(best.values())

    def windows(self) -> list[tuple[Optional[int], str, str]]:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        out = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    out.append((pid.value or None, buf.value))
            return True
        user32.EnumWindows(cb, 0)
        return [(pid, _proc_name(pid), t) for pid, t in out]

    def active_window(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (pid.value or None, _proc_name(pid.value), buf.value)


# --------------------------------------------------------------------------- #
# Watcher: polls in the background while something needs it
# --------------------------------------------------------------------------- #
def attach_sites(media: list[MediaItem], windows: list[tuple[Optional[int], str, str]]):
    """Finds the website for browser media by matching window titles."""
    for m in media:
        if m.url and not m.site:
            m.site = site_from_url(m.url)
        aliases = BROWSERS.get(m.app)
        if aliases is None:
            continue
        key = m.title.strip().lower()[:40]
        if not key:
            continue
        for pid, proc, title in windows:
            low_proc = (proc or "").lower()
            same = (m.pid and pid == m.pid) or any(a in low_proc for a in aliases)
            if same and key in title.lower():
                m.window = title
                if not m.site:
                    m.site = site_from_title(title)
                break


class MediaWatcher:
    MEDIA_EVERY = 2.0
    AUDIO_EVERY = 3.0

    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self.backend = WindowsBackend(log) if IS_WINDOWS else LinuxBackend(log)
        self.media: list[MediaItem] = []
        self.audio: list[MediaItem] = []
        self.active: Optional[tuple] = None
        self.status = {"media": "off", "audio": "off", "windows": "off"}
        self._errors_logged: set[str] = set()
        self._want_media = False
        self._want_audio = False
        self._want_active = False
        self._holds = 0
        self.audio_skip: Callable[[str], bool] = lambda _s: False
        self._lock = threading.Lock()
        self._started = False

    def _ensure_started(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._media_loop, daemon=True, name="media").start()
            threading.Thread(target=self._audio_loop, daemon=True, name="audio").start()

    def configure(self, media: bool, audio: bool, active: bool):
        self._want_media, self._want_audio, self._want_active = media, audio, active
        if media or audio or active:
            self._ensure_started()

    def hold(self, on: bool):
        """Keeps everything polling (e.g. while the filter dialog shows live results)."""
        self._holds = max(0, self._holds + (1 if on else -1))
        if self._holds:
            self._ensure_started()

    def _fail(self, part: str, e: Exception):
        self.status[part] = "unavailable"
        key = f"{part}:{type(e).__name__}"
        if key not in self._errors_logged:
            self._errors_logged.add(key)
            what = {"media": "now playing", "audio": "program audio",
                    "windows": "window titles"}[part]
            self.log(f"Can't read {what} on this system ({type(e).__name__}: {e}).")

    def _media_loop(self):
        while True:
            if self._want_media or self._want_active or self._holds:
                windows = []
                try:
                    windows = self.backend.windows()
                    self.status["windows"] = "ok"
                except Exception as e:
                    self._fail("windows", e)
                if self._want_active or self._holds:
                    try:
                        self.active = self.backend.active_window()
                    except Exception:
                        self.active = None
                if self._want_media or self._holds:
                    try:
                        media = self.backend.media()
                        attach_sites(media, windows)
                        with self._lock:
                            self.media = media
                        self.status["media"] = "ok"
                    except Exception as e:
                        self._fail("media", e)
                        with self._lock:
                            self.media = []
            time.sleep(self.MEDIA_EVERY)

    def _audio_loop(self):
        while True:
            if self._want_audio or self._holds:
                try:
                    skip = self.audio_skip if not self._holds else (lambda _s: False)
                    audio = self.backend.audio(skip)
                    with self._lock:
                        self.audio = audio
                    self.status["audio"] = "ok"
                except Exception as e:
                    self._fail("audio", e)
                    with self._lock:
                        self.audio = []
            else:
                with self._lock:
                    self.audio = []
            time.sleep(self.AUDIO_EVERY)

    def snapshot(self) -> tuple[list[MediaItem], list[MediaItem]]:
        with self._lock:
            return list(self.media), list(self.audio)
