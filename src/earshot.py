"""
Earshot - conversation-aware mode.

Earshot also listens to the game (what the other players say), transcribes it on the same
GPU with the same Whisper model while you're quiet, and keeps a short memory of the
conversation. That memory makes your own translations better:

  * Whisper gets the recent conversation as a hint, so names, game words and topics
    people just used are recognised more often.
  * "Room language" as a route target: answer in the language the people around you
    speak, switched automatically.
  * Speaker echo guard: when your microphone only picked up the game (speakers, no
    headset), that "sentence" is not sent.
  * Smart translation (optional): a local AI model (Qwen2.5 via CTranslate2) translates
    your sentence with the conversation in mind - what "it" means, names, tone.
  * What the others say is shown in the Earshot tab, translated into your language.

Nothing leaves this PC, nothing is saved unless you press Save, and what other players say
is never sent to your chatbox.
"""

from __future__ import annotations

import difflib
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

import languages
import paths

SAMPLE_RATE = 16000
IS_WINDOWS = os.name == "nt"

DEFAULTS = {
    "enabled": False,
    "source": "vrchat" if not IS_WINDOWS else "output",   # vrchat | output | device
    "device": None,                 # input device name for source == "device"
    "whisper_context": False,       # experimental: give Whisper your recent sentences
    "echo_guard": True,
    "show_heard": True,
    "show_in": "en",                # translate what others say into this language
    "context_minutes": 3.0,
    "room_fallback": "en",          # room language until one is known
    "smart": False,
    "smart_model": "qwen2.5-3b",
    "smart_device": "same",
    "listen_device": "same",        # "same" = share the speech model, or its own device
    "listen_model": "small",        # Whisper model for the game when it has its own device
    "smart_path": "",               # custom CTranslate2 chat model folder
    "smart_english": True,          # also use the AI for X -> English
    "vad_threshold": 0.5,
}


def settings_of(s) -> dict:
    d = getattr(s, "earshot", None)
    if not isinstance(d, dict):
        d = {}
    for k, v in DEFAULTS.items():
        d.setdefault(k, v)
    if not d.get("ctx_off_127"):          # 1.27: the context features made things worse
        d["whisper_context"] = False
        d["smart"] = False
        d["ctx_off_127"] = True
    try:
        s.earshot = d
    except Exception:
        pass
    return d


# --------------------------------------------------------------------------- #
# Conversation memory
# --------------------------------------------------------------------------- #
@dataclass
class Line:
    t: float
    who: str                  # "them" | "you"
    lang: str
    text: str
    translation: str = ""
    shown_lang: str = ""


class RoomContext:
    def __init__(self):
        self.lines: deque[Line] = deque(maxlen=80)
        self._lock = threading.Lock()

    def add(self, line: Line):
        with self._lock:
            self.lines.append(line)

    def clear(self):
        with self._lock:
            self.lines.clear()

    def recent(self, minutes: float, who: Optional[str] = None) -> list[Line]:
        cut = time.time() - minutes * 60
        with self._lock:
            return [ln for ln in self.lines if ln.t >= cut and (who is None or ln.who == who)]

    def room_language(self, minutes: float = 3.0) -> tuple[Optional[str], int]:
        """The language the others spoke most (by characters) lately, and how many lines."""
        score: dict[str, int] = {}
        lines = self.recent(minutes, "them")
        for ln in lines:
            score[ln.lang] = score.get(ln.lang, 0) + len(ln.text)
        if not score:
            return None, 0
        best = max(score, key=score.get)
        n = sum(1 for ln in lines if ln.lang == best)
        if n < 2 and score[best] < 25:          # one short line isn't enough to switch
            return None, n
        return best, n

    def whisper_prompt(self, minutes: float, max_chars: int = 160) -> str:
        """Your own recent sentences, newest last, for Whisper's prompt. Other people's lines
        are left out: they're often misheard game audio and in another language, which
        pulls Whisper towards the wrong language and invented words."""
        out = ""
        for ln in reversed(self.recent(min(minutes, 1.5), "you")):
            piece = ln.text.strip()
            if not piece:
                continue
            if len(out) + len(piece) + 1 > max_chars:
                break
            out = piece + " " + out
        return out.strip()

    def transcript(self, minutes: float, max_lines: int = 12) -> list[Line]:
        return self.recent(minutes)[-max_lines:]


# --------------------------------------------------------------------------- #
# Game audio capture
# --------------------------------------------------------------------------- #
def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def find_vrchat_node() -> Optional[dict]:
    """PipeWire: VRChat's playback stream (works with Proton/Wine too)."""
    exe = _which("pw-dump")
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=5).stdout
        objs = json.loads(out or "[]")
    except Exception:
        return None
    best = None
    for o in objs:
        info = o.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Stream/Output/Audio":
            continue
        hay = " ".join(str(props.get(k, "")) for k in (
            "application.name", "application.process.binary", "node.name",
            "application.process.id", "media.name", "node.description")).lower()
        if "vrchat" in hay:
            cand = {"id": o.get("id"), "serial": props.get("object.serial"),
                    "name": props.get("application.name") or props.get("node.name") or "VRChat",
                    "running": info.get("state") == "running"}
            if best is None or (cand["running"] and not best["running"]):
                best = cand
    return best


def list_input_devices() -> list[str]:
    try:
        import sounddevice as sd
        return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception:
        return []


class GameAudio:
    """Delivers 16 kHz mono float32 chunks of what the game plays to on_chunk().
    state(text) reports what it's doing."""

    def __init__(self, cfg: dict, on_chunk: Callable[[np.ndarray], None],
                 on_state: Callable[[str, bool], None], log: Callable[[str], None]):
        self.cfg = cfg
        self.on_chunk = on_chunk
        self.on_state = on_state
        self.log = log
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self.stop()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), daemon=True,
                                        name="earshot-audio")
        self._thread.start()

    def stop(self):
        self._stop.set()
        p = self._proc
        if p is not None:
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._proc = None

    # ---- runner
    def _run(self, stop: threading.Event):
        src = self.cfg.get("source", "output")
        while not stop.is_set():
            try:
                if src == "device":
                    self._run_device(stop)
                elif IS_WINDOWS:
                    self._run_loopback_windows(stop)
                elif src == "vrchat":
                    node = find_vrchat_node()
                    if node is None:
                        self.on_state("Waiting for VRChat's sound… (start VRChat, or pick "
                                      "“Everything you hear”)", False)
                        stop.wait(5)
                        continue
                    self.on_state(f"Hearing {node['name']} only", True)
                    self._run_proc(["pw-record", "--target", str(node["serial"] or node["id"]),
                                    "--rate", str(SAMPLE_RATE), "--channels", "1",
                                    "--format", "f32", "-"], stop)
                else:
                    self._run_monitor_linux(stop)
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                self.on_state(f"Can't hear the game: {e}", False)
                if msg != getattr(self, "_last_err", None):
                    self._last_err = msg
                    self.log(f"Earshot: game audio failed ({msg}). Retrying every few seconds.")
            stop.wait(3)

    def _run_proc(self, cmd: list[str], stop: threading.Event):
        exe = _which(cmd[0])
        if not exe:
            raise RuntimeError(f"{cmd[0]} is not installed")
        self._proc = subprocess.Popen([exe] + cmd[1:], stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, **paths.hidden_subprocess_kwargs())
        out = self._proc.stdout
        n = int(SAMPLE_RATE * 0.064) * 4
        while not stop.is_set():
            data = out.read(n)
            if not data:
                break
            usable = len(data) - len(data) % 4
            if usable:
                self.on_chunk(np.frombuffer(data[:usable], dtype="<f4").copy())
        try:
            self._proc.terminate()
        except Exception:
            pass

    def _run_monitor_linux(self, stop):
        if _which("parec"):
            self.on_state("Hearing everything you hear (default output)", True)
            self._run_proc(["parec", "--device=@DEFAULT_MONITOR@", "--format=float32le",
                            f"--rate={SAMPLE_RATE}", "--channels=1", "--raw",
                            "--latency-msec=60"], stop)
        elif _which("pw-record"):
            self.on_state("Hearing everything you hear (default output)", True)
            self._run_proc(["pw-record", "-P", "stream.capture.sink=true", "--rate",
                            str(SAMPLE_RATE), "--channels", "1", "--format", "f32", "-"], stop)
        else:
            raise RuntimeError("needs parec (pulseaudio-utils) or pw-record (pipewire)")

    def _run_loopback_windows(self, stop):
        import soundcard as sc
        import soxr
        spk = sc.default_speaker()
        mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        rate = 48000
        rs = soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")
        self.on_state(f"Hearing everything on {spk.name}", True)
        with mic.recorder(samplerate=rate, blocksize=1024) as rec:
            while not stop.is_set():
                data = rec.record(numframes=int(rate * 0.064))
                mono = data.mean(axis=1) if data.ndim > 1 else data
                out = rs.resample_chunk(np.asarray(mono, dtype=np.float32))
                if len(out):
                    self.on_chunk(out)
                if sc.default_speaker().name != spk.name:      # output device changed
                    break

    def _run_device(self, stop):
        import sounddevice as sd
        import soxr
        name = self.cfg.get("device")
        idx = None
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d["max_input_channels"] > 0:
                idx = i
                break
        if idx is None:
            raise RuntimeError(f"input device {name!r} not found")
        rate = int(sd.query_devices(idx)["default_samplerate"])
        rs = soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32") \
            if rate != SAMPLE_RATE else None
        q: queue.Queue = queue.Queue()

        def cb(indata, _f, _t, _s):
            q.put(indata[:, 0].copy())
        self.on_state(f"Hearing {name}", True)
        with sd.InputStream(device=idx, channels=1, samplerate=rate, dtype="float32",
                            blocksize=int(rate * 0.064), callback=cb):
            while not stop.is_set():
                try:
                    chunk = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                self.on_chunk(rs.resample_chunk(chunk) if rs else chunk)


# --------------------------------------------------------------------------- #
# Echo guard
# --------------------------------------------------------------------------- #
def _norm(t: str) -> str:
    return re.sub(r"[^\w ]+", "", t.lower()).strip()


def is_echo(text: str, heard: list[Line], window_s: float = 12.0) -> Optional[Line]:
    """Your mic's sentence is (nearly) what the game just played: speaker bleed."""
    a = _norm(text)
    if len(a) < 6:
        return None
    now = time.time()
    for ln in reversed(heard):
        if now - ln.t > window_s:
            break
        b = _norm(ln.text)
        if not b:
            continue
        if a in b or difflib.SequenceMatcher(None, a, b).ratio() > 0.72:
            return ln
    return None


# --------------------------------------------------------------------------- #
# The Earshot worker
# --------------------------------------------------------------------------- #
class Earshot:
    """Runs inside the Engine. on_line(Line) and on_state(text, ok, level) go to the GUI."""

    def __init__(self, engine, log: Callable[[str], None],
                 on_line: Callable[[Line], None], on_state: Callable[[str, bool], None],
                 on_level: Callable[[float, bool], None]):
        from engine import Segmenter, StreamingVAD
        self.e = engine
        self.log = log
        self.on_line = on_line
        self.on_state = on_state
        self.on_level = on_level
        self.ctx = RoomContext()
        self.cfg = settings_of(engine.s)
        self.audio: Optional[GameAudio] = None
        self._vad = StreamingVAD()
        self._seg_cls = Segmenter
        self._seg = Segmenter(self._seg_settings())
        self._buf = np.zeros(0, dtype=np.float32)
        self._chunks: queue.Queue = queue.Queue(maxsize=200)
        self._utts: deque = deque(maxlen=4)            # newest kept when we fall behind
        self._utt_event = threading.Event()
        self.state_text, self.state_ok = "Off", False
        self.room_lang: Optional[str] = None
        self.smart = None                               # SmartTranslator when loaded
        self.smart_state = "off"                        # off | missing | loading | ready | error | downloading
        self.heard_count = 0
        threading.Thread(target=self._vad_loop, daemon=True, name="earshot-vad").start()
        threading.Thread(target=self._asr_loop, daemon=True, name="earshot-asr").start()

    @property
    def active(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def _seg_settings(self):
        import types
        return types.SimpleNamespace(vad_threshold=float(self.cfg.get("vad_threshold", 0.5)),
                                     end_silence_ms=650, min_speech_ms=350, max_utterance_s=12.0)

    def apply(self):
        """Call after the Earshot settings changed."""
        self.cfg = settings_of(self.e.s)
        if self.active:
            if self.audio is None or self.audio.cfg.get("source") != self.cfg.get("source") \
                    or self.audio.cfg.get("device") != self.cfg.get("device"):
                if self.audio is not None:
                    self.audio.stop()
                self.audio = GameAudio(dict(self.cfg), self._on_chunk, self._set_state, self.log)
                self.audio.start()
        else:
            if self.audio is not None:
                self.audio.stop()
                self.audio = None
            self._set_state("Off", False)
        self._apply_listener()
        self.update_room_language()

    # ---- an own Whisper model for the game (when it has its own device)
    listener = None
    _listener_key = None
    listener_state = "shared"

    def _apply_listener(self):
        import devices
        spec = str(self.cfg.get("listen_device") or "same")
        want = None
        if self.active and spec not in ("same", ""):
            want = (devices.resolve(spec, self.e.s), str(self.cfg.get("listen_model") or "small"))
        if want == self._listener_key:
            return
        self._listener_key = want
        old, self.listener = self.listener, None
        if callable(getattr(type(old), "close", None)):
            old.close()
        del old
        if want is None:
            self.listener_state = "shared"
            return
        self.listener_state = "loading"

        def work(key=want):
            import workers
            try:
                tr = workers.make("whisper", key[0], key[1], "auto", log=self.log)
            except Exception as ex:
                self.log(f"Earshot: its own speech model failed to load ({ex}) - "
                         "sharing yours instead.")
                self.listener_state = "error"
                return
            if self._listener_key == key:
                self.listener = tr
                self.listener_state = "ready"
                self.log(f"Earshot listens with its own {key[1]} model on "
                         f"{devices.label(key[0])} - it no longer waits for you to be quiet.")
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.audio is not None:
            self.audio.stop()
            self.audio = None

    def _set_state(self, text: str, ok: bool):
        self.state_text, self.state_ok = text, ok
        self.on_state(text, ok)

    # ---- audio -> utterances
    def _on_chunk(self, chunk: np.ndarray):
        try:
            self._chunks.put_nowait(chunk)
        except queue.Full:
            pass

    def _vad_loop(self):
        from engine import FRAME
        level_t = 0.0
        while True:
            chunk = self._chunks.get()
            if not self.active:
                self._buf = np.zeros(0, dtype=np.float32)
                continue
            self._buf = np.concatenate([self._buf, chunk])
            while len(self._buf) >= FRAME:
                frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
                prob = self._vad(frame)
                utt, _speaking = self._seg.push(frame, prob)
                if utt is not None:
                    self._utts.append(utt)
                    self._utt_event.set()
                now = time.monotonic()
                if now - level_t > 0.08:
                    level_t = now
                    rms = float(np.sqrt(np.mean(frame ** 2)))
                    self.on_level(min(1.0, rms * 8), prob >= self._seg.s.vad_threshold)

    # ---- utterances -> text (only while you're quiet: your speech always goes first)
    def _engine_idle(self) -> bool:
        e = self.e
        return e.transcriber is not None and not e._speaking and not e._busy and e.utt_q.empty()

    def _asr_loop(self):
        while True:
            self._utt_event.wait()
            self._utt_event.clear()
            while self._utts:
                for _ in range(200):                     # wait up to ~10 s for a quiet moment
                    if self.listener is not None or self._engine_idle():
                        break                            # own model: no need to wait
                    time.sleep(0.05)
                if not self._utts:
                    break
                audio = self._utts.popleft()
                if not self.active:
                    continue
                try:
                    self._transcribe(audio)
                except Exception as ex:
                    self.log(f"Earshot: couldn't transcribe game audio ({ex})")

    def _transcribe(self, audio: np.ndarray):
        e = self.e
        show_in = self.cfg.get("show_in", "en")
        want_en = bool(self.cfg.get("show_heard")) and show_in == "en"
        own = self.listener
        if own is not None:
            r = own.run_room(audio, want_english=want_en, beam_size=1)
        else:
            with e._model_lock:
                tr = e.transcriber
                if tr is None:
                    return
                r = tr.run_room(audio, want_english=want_en, beam_size=1)
        if r is None:
            return
        lang, text, english = r
        line = Line(time.time(), "them", lang, text)
        if self.cfg.get("show_heard") and lang != show_in:
            if show_in == "en" and english:
                line.translation, line.shown_lang = english, "en"
            else:
                tx = e._translate(text, lang, show_in) if lang in languages.NAME and \
                    show_in in languages.NAME else None
                if tx:
                    line.translation, line.shown_lang = tx, show_in
        self.ctx.add(line)
        self.heard_count += 1
        self.update_room_language()
        self.on_line(line)

    # ---- context for your own speech
    def update_room_language(self):
        lang, _n = self.ctx.room_language(float(self.cfg.get("context_minutes", 3)))
        self.room_lang = lang

    def target_room_language(self) -> str:
        return self.room_lang or self.cfg.get("room_fallback", "en") or "en"

    def whisper_prompt(self) -> str:
        if not (self.active and self.cfg.get("whisper_context")):
            return ""
        return self.ctx.whisper_prompt(float(self.cfg.get("context_minutes", 3)))

    def check_echo(self, text: str) -> Optional[Line]:
        if not (self.active and self.cfg.get("echo_guard")):
            return None
        return is_echo(text, self.ctx.recent(0.5, "them"))

    def note_own(self, lang: str, text: str, sent: str = ""):
        if self.active and text.strip():
            ln = Line(time.time(), "you", lang, text, sent)
            self.ctx.add(ln)
            self.on_line(ln)

    def smart_translate(self, text: str, src: str, dst: str) -> Optional[str]:
        sm = self.smart
        if not (self.active and self.cfg.get("smart")) or sm is None:
            return None
        if dst == "en" and not self.cfg.get("smart_english", True):
            return None
        hist = self.ctx.transcript(float(self.cfg.get("context_minutes", 3)), 12)
        try:
            return sm.translate(text, src, dst, hist)
        except Exception as ex:
            self.log(f"Smart translation failed, using the normal translator ({ex})")
            return None
