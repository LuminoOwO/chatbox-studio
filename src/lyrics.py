"""
Synced lyrics for "now playing" (experimental).

How it works:
  1. Lyrics come as LRC: every line has a timestamp, e.g. "[01:23.45] some line".
  2. Sources, in this order:
       - a local .lrc file next to the music file (players like VLC/Elisa report the path),
       - a .lrc file in a folder you choose, named "Artist - Title.lrc",
       - LRCLIB (lrclib.net), a free open lyrics database - only if you switch it on.
         Only title, artist, album and length are sent; no account or key is needed.
  3. The player reports the song position; the line whose timestamp has passed is shown.

Browser videos have messy titles ("Artist - Song (Official Video)"), so they're cleaned
up before searching. Results (also "not found") are cached on disk.
"""

from __future__ import annotations

import bisect
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import paths

API = "https://lrclib.net/api"
_TS = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.I)

_NOISE = re.compile(
    r"\s*[\(\[【]([^\)\]】]*(official|video|audio|lyric|lyrics|visuali[sz]er|music video|mv|hd|hq|"
    r"4k|remaster(ed)?|live|color coded|explicit|clean)[^\)\]】]*)[\)\]】]", re.I)


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """'[00:12.30]line' -> [(12.3, 'line')], sorted. Lines with several stamps repeat."""
    off = 0.0
    m = _OFFSET.search(text or "")
    if m:
        off = int(m.group(1)) / 1000.0
    out = []
    for raw in (text or "").splitlines():
        stamps = list(_TS.finditer(raw))
        if not stamps:
            continue
        words = _TS.sub("", raw).strip()
        for s in stamps:
            mm, ss, frac = s.group(1), s.group(2), s.group(3) or "0"
            t = int(mm) * 60 + int(ss) + int(frac) / (10 ** len(frac))
            out.append((max(0.0, t - off), words))
    out.sort(key=lambda x: x[0])
    return out


def clean_title(title: str, artist: str) -> tuple[str, str]:
    """Makes browser/video titles searchable: ('Band - Song (Official Video)', 'BandVEVO')
    -> ('Song', 'Band')."""
    t = _NOISE.sub("", title or "").strip()
    t = re.sub(r"\s*\|.*$", "", t).strip()                     # "Song | Some Channel"
    t = re.sub(r"\s+(ft\.?|feat\.?)\s+.*$", "", t, flags=re.I).strip()
    a = re.sub(r"\s*-\s*Topic$", "", artist or "").strip()     # YouTube "Artist - Topic"
    a = re.sub(r"VEVO$", "", a).strip()
    if " - " in t:
        left, right = t.split(" - ", 1)
        if not a or a.lower().replace(" ", "") in left.lower().replace(" ", "") or \
                len(left) < 40:
            a, t = left.strip(), right.strip()
    t = _NOISE.sub("", t).strip(" -–—")
    return t, a


class Lyrics:
    def __init__(self, lines: list[tuple[float, str]], source: str):
        self.lines = lines
        self.times = [t for t, _l in lines]
        self.source = source

    def at(self, pos: float) -> tuple[str, str]:
        """(current line, next line) at position `pos` seconds."""
        i = bisect.bisect_right(self.times, pos) - 1
        if i < 0:
            return "", self.lines[0][1] if self.lines else ""
        cur = self.lines[i][1] or "♪"
        nxt = self.lines[i + 1][1] if i + 1 < len(self.lines) else ""
        return cur, nxt


class LyricsProvider:

    def __init__(self, log: Callable[[str], None], user_agent: str):
        self.log = log
        self.ua = user_agent
        self._mem: dict[str, Optional[Lyrics]] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self.status: dict[str, str] = {}             # key -> human readable state
        self._cache_dir = paths.data_dir() / "lyrics-cache"
        self._logged = set()

    @staticmethod
    def key(title: str, artist: str, duration: Optional[float]) -> str:
        return f"{artist.strip().lower()}|{title.strip().lower()}|{int(duration or 0)}"

    def get(self, item, online: bool, folder: str = "") -> Optional[Lyrics]:
        """Returns lyrics if known; otherwise starts looking them up in the background."""
        title, artist = clean_title(item.title, item.artist)
        if not title:
            return None
        k = self.key(title, artist, item.duration)
        with self._lock:
            if k in self._mem:
                return self._mem[k]
            if k in self._pending:
                return None
            self._pending.add(k)
        self.status[k] = "looking up…"
        threading.Thread(target=self._lookup, daemon=True, name="lyrics",
                         args=(k, title, artist, item, online, folder)).start()
        return None

    def state(self, item) -> str:
        title, artist = clean_title(item.title, item.artist)
        return self.status.get(self.key(title, artist, item.duration), "")

    # ---- lookup -----------------------------------------------------------
    def _lookup(self, k, title, artist, item, online, folder):
        found, where = None, ""
        try:
            found, where = self._local(item, title, artist, folder)
            if found is None:
                found, where = self._cached(k)
            if found is None and online:
                found, where = self._lrclib(k, title, artist, item)
        except Exception as e:
            self._log_once("lookup", f"Lyrics lookup failed: {e}")
        with self._lock:
            # a miss is kept only for this session if we didn't ask online
            self._mem[k] = found
            self._pending.discard(k)
        if found:
            self.status[k] = f"synced lyrics from {where}"
        elif online:
            self.status[k] = "no synced lyrics found"
        else:
            self.status[k] = "no local .lrc file (online lookup is off)"

    def _local(self, item, title, artist, folder):
        cands = []
        url = getattr(item, "url", "") or ""
        if url.startswith("file://"):
            p = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path))
            cands.append(p.with_suffix(".lrc"))
        if folder:
            safe = lambda s: re.sub(r'[\\/:*?"<>|]', "_", s)
            cands += [Path(folder) / f"{safe(artist)} - {safe(title)}.lrc",
                      Path(folder) / f"{safe(title)}.lrc"]
        for c in cands:
            try:
                if c.is_file():
                    lines = parse_lrc(c.read_text(encoding="utf-8", errors="replace"))
                    if lines:
                        return Lyrics(lines, "local file"), "a local file"
            except OSError:
                continue
        return None, ""

    def _cache_file(self, k):
        import hashlib
        return self._cache_dir / (hashlib.sha1(k.encode()).hexdigest() + ".json")

    def _cached(self, k):
        f = self._cache_file(k)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, ""
        if data.get("synced"):
            return Lyrics(parse_lrc(data["synced"]), "LRCLIB"), "LRCLIB (cached)"
        return None, ""

    def _store(self, k, synced: str):
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_file(k).write_text(json.dumps({"synced": synced, "t": time.time()}),
                                           encoding="utf-8")
        except OSError:
            pass

    def _http(self, path: str, params: dict):
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        req = urllib.request.Request(f"{API}/{path}?{q}", headers={"User-Agent": self.ua})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def _lrclib(self, k, title, artist, item):
        dur = int(round(item.duration)) if item.duration else None
        best = None
        if artist and dur:
            r = self._http("get", {"track_name": title, "artist_name": artist,
                                   "album_name": item.album, "duration": dur})
            if r and r.get("syncedLyrics"):
                best = r
        if best is None:
            res = self._http("search", {"track_name": title, "artist_name": artist}) or []
            if not res and artist:
                res = self._http("search", {"q": f"{artist} {title}"}) or []
            res = [x for x in res if x.get("syncedLyrics")]
            if dur:
                res = [x for x in res if abs((x.get("duration") or 0) - dur) <= 4] or []
            best = res[0] if res else None
        if best is None:
            return None, ""
        self._store(k, best["syncedLyrics"])
        return Lyrics(parse_lrc(best["syncedLyrics"]), "LRCLIB"), "LRCLIB"

    def _log_once(self, key, msg):
        if key not in self._logged:
            self._logged.add(key)
            self.log(msg)
