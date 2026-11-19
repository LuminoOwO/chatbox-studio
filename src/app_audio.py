"""
Recording one program's sound (Earshot: "A program…", e.g. Discord or Vesktop).

* Linux (PipeWire): the program's playback stream, found with pw-dump and recorded with
  pw-record --target - like "VRChat only".
* Windows 11: WASAPI process loopback (ActivateAudioInterfaceAsync with
  AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK) on the program's process tree - the
  program's own sound, nothing else. Pure ctypes, no extra package. Windows 10 doesn't
  have it (it came with build 20348).

A program is matched by a name: its executable ("discord", "vesktop.exe") or, on Linux,
its PipeWire application name. Case doesn't matter, ".exe" is optional.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional

import numpy as np

IS_WINDOWS = os.name == "nt"
SAMPLE_RATE = 16000

# programs people mean when they say "Discord" (name shown, names it runs as)
KNOWN = [
    ("Discord", ["discord", "discordptb", "discordcanary", "webrtc voiceengine"]),
    ("Vesktop", ["vesktop"]),
    ("Equibop", ["equibop"]),
    ("WebCord", ["webcord"]),
    ("TeamSpeak", ["ts3client_win64", "ts3client_linux_amd64", "teamspeak", "ts3client"]),
    ("Mumble", ["mumble"]),
    ("Steam (voice chat)", ["steamwebhelper", "steam"]),
    ("Firefox", ["firefox"]),
    ("Chrome", ["chrome"]),
    ("Spotify", ["spotify"]),
    ("VRChat", ["vrchat"]),
]


def _norm(name: str) -> str:
    n = os.path.basename(str(name or "")).strip().lower()
    return n[:-4] if n.endswith(".exe") else n


def aliases(name: str) -> list[str]:
    """The names a program can run as ('discord' also covers Discord PTB / Canary)."""
    n = _norm(name)
    for shown, names in KNOWN:
        if n == shown.lower() or n in names:
            return names
    return [n]


def windows_supported() -> bool:
    return IS_WINDOWS and sys.getwindowsversion().build >= 20348


# --------------------------------------------------------------------------- listing
def list_programs() -> list[str]:
    """Programs that play sound right now (Linux) / that run right now (Windows), for the
    picker. The user can also type a name."""
    out: list[str] = []
    if IS_WINDOWS:
        try:
            import psutil
            seen = set()
            for p in psutil.process_iter(["name", "username"]):
                n = p.info.get("name") or ""
                if not n.lower().endswith(".exe") or not p.info.get("username"):
                    continue                    # system processes have no user for us
                k = _norm(n)
                if k not in seen and k not in _WIN_SKIP:
                    seen.add(k)
                    out.append(n[:-4])
        except Exception:
            pass
    else:
        for s in playback_streams():
            if s["app"] not in out:
                out.append(s["app"])
    known = [shown for shown, names in KNOWN
             if any(_norm(x) in names for x in out) or shown.lower() in map(str.lower, out)]
    rest = sorted((x for x in out if not any(_norm(x) in names for _s, names in KNOWN)),
                  key=str.lower)
    return known + rest


_WIN_SKIP = {"explorer", "svchost", "conhost", "runtimebroker", "dllhost", "sihost",
             "taskhostw", "ctfmon", "searchhost", "startmenuexperiencehost", "textinputhost",
             "shellexperiencehost", "applicationframehost", "systemsettings", "smartscreen",
             "securityhealthsystray", "widgets", "widgetservice", "lockapp", "dwm",
             "python", "pythonw", "chatbox studio", "cmd", "powershell", "openconsole",
             "windowsterminal", "msedgewebview2", "crashpad_handler", "useroobebroker"}


# --------------------------------------------------------------------------- Linux
def playback_streams() -> list[dict]:
    """PipeWire playback streams: [{id, serial, app, binary, running}]."""
    exe = shutil.which("pw-dump")
    if not exe:
        return []
    try:
        objs = json.loads(subprocess.run([exe], capture_output=True, text=True,
                                         timeout=5).stdout or "[]")
    except Exception:
        return []
    out = []
    for o in objs:
        info = o.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        out.append({"id": o.get("id"), "serial": props.get("object.serial"),
                    "app": str(props.get("application.name") or props.get("node.name") or "?"),
                    "binary": str(props.get("application.process.binary") or ""),
                    "hay": " ".join(str(props.get(k, "")) for k in (
                        "application.name", "application.process.binary", "node.name",
                        "media.name", "node.description")).lower(),
                    "running": info.get("state") == "running"})
    return out


def find_stream(name: str) -> Optional[dict]:
    """The best playback stream of a program (a running one first)."""
    names = aliases(name)
    best = None
    for s in playback_streams():
        if any(n and (n == _norm(s["binary"]) or n == s["app"].lower() or n in s["hay"])
               for n in names):
            if best is None or (s["running"] and not best["running"]):
                best = s
    return best


# --------------------------------------------------------------------------- Windows
def find_process(name: str) -> Optional[int]:
    """The top process of a program (its tree is recorded), or None when it doesn't run."""
    import psutil
    names = set(aliases(name))
    procs = {}
    for p in psutil.process_iter(["pid", "name", "ppid"]):
        if _norm(p.info.get("name")) in names:
            procs[p.info["pid"]] = p.info.get("ppid")
    if not procs:
        return None
    roots = [pid for pid, ppid in procs.items() if ppid not in procs]
    return min(roots or procs)


class ProcessLoopback:
    """WASAPI process loopback: 16 kHz mono float32 chunks of one process tree's sound."""

    def __init__(self, pid: int):
        self.pid = pid

    def run(self, on_chunk: Callable[[np.ndarray], None], stop: threading.Event):
        import ctypes
        from ctypes import POINTER, byref, c_void_p, wintypes

        ole32 = ctypes.WinDLL("ole32")
        kernel32 = ctypes.WinDLL("kernel32")
        mmdev = ctypes.WinDLL("Mmdevapi")
        HRESULT = ctypes.c_long

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                        ("d", ctypes.c_ubyte * 8)]

            @classmethod
            def of(cls, s: str):
                import uuid
                u = uuid.UUID(s)
                return cls(u.time_low, u.time_mid, u.time_hi_version,
                           (ctypes.c_ubyte * 8)(*u.bytes[8:]))

            def same(self, other) -> bool:
                return bytes(self) == bytes(other)

        IID_IUnknown = GUID.of("00000000-0000-0000-C000-000000000046")
        IID_IAgileObject = GUID.of("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
        IID_Handler = GUID.of("41D949AB-9862-444A-80F6-C261334DA5EB")
        IID_IAudioClient = GUID.of("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
        IID_IAudioCaptureClient = GUID.of("C8ADBD64-E71E-48a0-A4DE-185C395CD317")

        def method(obj, index, *argtypes):
            vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
            return ctypes.WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(vtbl[index])

        def check(hr, what):
            if hr < 0:
                raise OSError(f"{what} failed (0x{hr & 0xFFFFFFFF:08X})")

        # ---- the completion handler (a tiny COM object)
        done = threading.Event()
        result = {}
        QI = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), POINTER(c_void_p))
        REF = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
        DONE = ctypes.WINFUNCTYPE(HRESULT, c_void_p, c_void_p)

        def qi(this, riid, out):
            r = riid.contents
            if r.same(IID_IUnknown) or r.same(IID_Handler) or r.same(IID_IAgileObject):
                out[0] = this
                return 0
            out[0] = None
            return -2147467262                   # E_NOINTERFACE

        def completed(_this, op):
            hr, unk = HRESULT(), c_void_p()
            r = method(op, 3, POINTER(HRESULT), POINTER(c_void_p))(op, byref(hr), byref(unk))
            result["hr"] = r if r < 0 else hr.value
            result["unk"] = unk
            done.set()
            return 0

        funcs = [QI(qi), REF(lambda _t: 1), REF(lambda _t: 1), DONE(completed)]
        vtable = (c_void_p * 4)(*[ctypes.cast(f, c_void_p) for f in funcs])
        handler = ctypes.pointer(ctypes.pointer(vtable))   # an object = pointer to its vtable

        # ---- activation parameters: this process tree
        class LoopbackParams(ctypes.Structure):
            _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]

        class ActivationParams(ctypes.Structure):
            _fields_ = [("ActivationType", ctypes.c_int), ("Params", LoopbackParams)]

        class Blob(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]

        class PropVariant(ctypes.Structure):
            _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                        ("r3", ctypes.c_ushort), ("blob", Blob)]

        params = ActivationParams(1, LoopbackParams(self.pid, 0))   # PROCESS_LOOPBACK, include tree
        pv = PropVariant(65, 0, 0, 0, Blob(ctypes.sizeof(params),
                                           ctypes.cast(byref(params), c_void_p)))   # VT_BLOB

        ole32.CoInitializeEx(None, 0)                 # COINIT_MULTITHREADED
        op = c_void_p()
        act = mmdev.ActivateAudioInterfaceAsync
        act.restype = HRESULT
        act.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PropVariant), c_void_p,
                        POINTER(c_void_p)]
        check(act("VAD\\Process_Loopback", byref(IID_IAudioClient), byref(pv),
                  ctypes.cast(handler, c_void_p), byref(op)), "Activating the program's sound")
        if not done.wait(5):
            raise OSError("Windows didn't answer (process loopback)")
        check(result["hr"], "Activating the program's sound")
        unk = result["unk"]
        client = c_void_p()
        check(method(unk, 0, POINTER(GUID), POINTER(c_void_p))(unk, byref(IID_IAudioClient),
                                                               byref(client)), "IAudioClient")

        class WaveFormat(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                        ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                        ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                        ("cbSize", wintypes.WORD)]

        # 16-bit PCM in the format we ask for; Windows converts (AUTOCONVERTPCM). 16 kHz mono
        # first, 48 kHz stereo (Microsoft's sample) if that's refused.
        flags = 0x00020000 | 0x00040000 | 0x80000000 | 0x08000000   # LOOPBACK|EVENT|AUTOCONVERT|SRC_QUALITY
        init = method(client, 3, ctypes.c_int, wintypes.DWORD, ctypes.c_longlong,
                      ctypes.c_longlong, POINTER(WaveFormat), c_void_p)
        hr = 0
        for rate, ch in ((SAMPLE_RATE, 1), (48000, 2)):
            fmt = WaveFormat(1, ch, rate, rate * 2 * ch, 2 * ch, 16, 0)
            hr = init(client, 0, flags, 2_000_000, 0, byref(fmt), None)
            if hr >= 0:
                break
        check(hr, "Starting the recording")
        rs = None
        if rate != SAMPLE_RATE:
            try:
                import soxr
                rs = soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")
            except ImportError:
                rs = None
        event = kernel32.CreateEventW(None, False, False, None)
        check(method(client, 13, c_void_p)(client, event), "SetEventHandle")
        cap = c_void_p()
        check(method(client, 14, POINTER(GUID), POINTER(c_void_p))(
            client, byref(IID_IAudioCaptureClient), byref(cap)), "IAudioCaptureClient")
        get_buf = method(cap, 3, POINTER(c_void_p), POINTER(ctypes.c_uint32),
                         POINTER(wintypes.DWORD), c_void_p, c_void_p)
        rel_buf = method(cap, 4, ctypes.c_uint32)
        next_size = method(cap, 5, POINTER(ctypes.c_uint32))
        check(method(client, 10)(client), "Start")
        import psutil
        waits = 0
        try:
            while not stop.is_set():
                kernel32.WaitForSingleObject(event, 200)
                waits += 1
                if waits % 25 == 0 and not psutil.pid_exists(self.pid):
                    return                      # the program closed: find it again
                n = ctypes.c_uint32()
                while not stop.is_set():
                    check(next_size(cap, byref(n)), "Reading the sound")
                    if n.value == 0:
                        break
                    data, frames, fl = c_void_p(), ctypes.c_uint32(), wintypes.DWORD()
                    check(get_buf(cap, byref(data), byref(frames), byref(fl), None, None),
                          "Reading the sound")
                    count = frames.value
                    if fl.value & 2 or not data.value:          # AUDCLNT_BUFFERFLAGS_SILENT
                        chunk = np.zeros(count, dtype=np.float32)
                    else:
                        raw = ctypes.string_at(data.value, count * 2 * ch)
                        chunk = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
                        if ch > 1:
                            chunk = chunk.reshape(-1, ch).mean(axis=1)
                    rel_buf(cap, count)
                    if count and rate != SAMPLE_RATE:
                        chunk = (rs.resample_chunk(chunk) if rs is not None else
                                 np.interp(np.arange(0, count, rate / SAMPLE_RATE),
                                           np.arange(count), chunk).astype(np.float32))
                    if len(chunk):
                        on_chunk(chunk)
        finally:
            for fn in (lambda: method(client, 11)(client),           # Stop
                       lambda: method(cap, 2)(cap), lambda: method(client, 2)(client),
                       lambda: method(unk, 2)(unk), lambda: method(op, 2)(op),
                       lambda: kernel32.CloseHandle(event)):
                try:
                    fn()
                except Exception:
                    pass
        _keep = (funcs, vtable, handler, params, pv)           # alive until here
        del _keep
