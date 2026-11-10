"""
Speech engine for Chatbox Studio.

Pipeline:  microphone -> Silero VAD (CPU) -> Whisper (GPU) -> VRChat OSC chatbox

Speed tricks used here:
  * One Whisper encoder pass per utterance. Language detection and decoding both
    reuse the same encoder output (faster-whisper's normal API would encode twice).
  * Language detection only compares the languages you speak, so short phrases are
    never misdetected as something random.
  * Routes into English: Whisper translates by itself (no second model).
    Routes into other languages: Whisper transcribes, NLLB-200 translates (translator.py).
  * Greedy decoding by default, model warmed up right after loading.
  * VAD runs on the CPU in 32 ms frames, so end-of-speech is detected quickly.
"""

from __future__ import annotations

import os

# Make CUDA's GPU numbering match nvidia-smi (must be set before CUDA is initialised).
os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")

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
import translator as nllb
from chatbox import Chatbox
from filters import FilterSet

SAMPLE_RATE = 16000
FRAME = 512                      # Silero VAD frame: 512 samples = 32 ms at 16 kHz
FRAME_MS = FRAME * 1000 / SAMPLE_RATE
CHATBOX_LIMIT = 144              # VRChat chatbox character limit


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    mic_device: Optional[str] = None      # sounddevice name; None = system default
    gpu_index: int = 0                    # nvidia-smi index; -1 = CPU (picked on first start)
    speech_device: str = ""               # "nvidia:1", "amd:0", "cpu" (empty: from gpu_index)
    model: str = "large-v3"
    compute_type: str = "auto"            # auto picks int8 on Pascal, int8_float16 on Ampere
    # Translation routes: speech in `src` is sent to the chatbox in `dst`. Several routes
    # can be on at once (A->B and B->A, or A->B + A->C for two translations). The default
    # only writes down English; you add your own languages.
    routes: list = field(default_factory=lambda: [
        {"src": "en", "dst": "en", "enabled": True},
    ])
    join_with: str = "newline"            # how several translations are joined: newline | bar | slash
    translator_model: str = "nllb-200-distilled-600M"
    translator_device: str = "same"       # same (as speech) | "nvidia:0" | "amd:0" | cpu
    nllb_for_english: bool = False        # use NLLB instead of Whisper for X -> English
    chatbox_template: str = "{text}"      # {text} {original} {src} {dst}
    translate_typed: bool = False
    beam_size: int = 1                    # 1 = greedy (fastest)
    vad_threshold: float = 0.5
    end_silence_ms: int = 500             # pause that ends a sentence
    min_speech_ms: int = 250
    max_utterance_s: float = 10.0
    osc_enabled: bool = True
    osc_host: str = "127.0.0.1"
    osc_port: int = 9000
    osc_listen_port: int = 9001           # where VRChat (or an OSC router like VOR) sends to
    oscquery: bool = True                 # if that port is taken: free port + OSCQuery
    typing_indicator: bool = True
    notify_sound: bool = False
    follow_vrc_mute: bool = True
    ui_theme: str = "dark"                # dark | light | contrast
    ui_scale: float = 1.0                 # text size
    min_send_interval_s: float = 1.5
    # text filters (see filters.py)
    filters_enabled: bool = True
    remove_fillers: bool = True
    min_words: int = 0
    filter_typed: bool = False
    replace_rules: list = field(default_factory=list)   # {find, replace, whole_word, regex, enabled}
    block_rules: list = field(default_factory=list)     # {pattern, whole_word, regex, enabled}
    chatbox: dict = field(default_factory=dict)          # see chatbox.py
    earshot: dict = field(default_factory=dict)          # see earshot.py
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Hardware discovery
# --------------------------------------------------------------------------- #
def list_gpus() -> list[tuple[int, str]]:
    """[(device number, "name (VRAM)")] for the installed engine: NVIDIA (CUDA) or AMD
    (ROCm). Device numbers are CTranslate2's."""
    import gpus
    try:
        found = gpus.ai_gpus()
        if found:
            return [(g.index, f"{g.name} ({round(g.vram_mb / 1024)} GB)") for g in found]
    except Exception:
        pass
    try:
        import ctranslate2
        return [(i, f"GPU {i}") for i in range(ctranslate2.get_cuda_device_count())]
    except Exception:
        return []


def _default_hostapi() -> int:
    import sounddevice as sd
    try:
        return int(sd.default.hostapi)
    except Exception:
        return 0


def list_microphones() -> list[str]:
    """Input devices of the default audio system (on Windows every mic would otherwise
    show up several times, once per MME / DirectSound / WASAPI)."""
    import sounddevice as sd
    api = _default_hostapi()
    names = []
    for dev in sd.query_devices():
        if dev["max_input_channels"] > 0 and dev["hostapi"] == api and dev["name"] not in names:
            names.append(dev["name"])
    return names


def resolve_microphone(name: Optional[str]) -> Optional[int]:
    """Device index for a saved microphone name (None = system default)."""
    if not name:
        return None
    import sounddevice as sd
    api = _default_hostapi()
    candidates = [(i, d) for i, d in enumerate(sd.query_devices())
                  if d["max_input_channels"] > 0 and d["name"] == name]
    for i, d in candidates:
        if d["hostapi"] == api:
            return i
    return candidates[0][0] if candidates else None


def gpu_compute_capability(gpu_index: int) -> float:
    try:
        out = subprocess.run(
            ["nvidia-smi", "-i", str(gpu_index), "--query-gpu=compute_cap",
             "--format=csv,noheader"], capture_output=True, text=True, timeout=5,
            **paths.hidden_subprocess_kwargs()).stdout
        return float(out.strip())
    except Exception:
        return 0.0


def pick_compute_type(spec, requested: str = "auto") -> str:
    """int8 on Pascal cards like the GTX 10 series (no fast FP16); int8_float16 on RTX and AMD cards.
    spec: a device string ("nvidia:1", "amd:0", "cpu") or an NVIDIA index (old callers)."""
    import ctranslate2
    import devices
    if isinstance(spec, int):
        spec = f"nvidia:{spec}" if spec >= 0 else "cpu"
    vendor, gpu_index = devices.parse(spec)
    if vendor == "cpu":
        return "int8"                      # CPU
    supported = ctranslate2.get_supported_compute_types("cuda", gpu_index)
    if requested != "auto" and requested in supported:
        return requested
    if vendor == "amd":                    # AMD: FP16 math, int8 weights where supported
        order = ("int8_float16", "float16", "int8", "float32")
        for ct in order:
            if ct in supported:
                return ct
        return "default"
    cc = gpu_compute_capability(gpu_index)
    if 0 < cc < 7.0:
        order = ("int8", "int8_float32", "float32")
    else:
        order = ("int8_float16", "int8", "float16", "int8_float32", "float32")
    for ct in order:
        if ct in supported:
            return ct
    return "default"


# --------------------------------------------------------------------------- #
# Streaming voice activity detection (Silero, bundled with faster-whisper)
# --------------------------------------------------------------------------- #
class StreamingVAD:
    CONTEXT = 64

    def __init__(self):
        import onnxruntime
        from faster_whisper.vad import get_assets_path

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(
            os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
            providers=["CPUExecutionProvider"], sess_options=opts,
        )
        self.reset()

    def reset(self):
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros(self.CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame])[None, :].astype(np.float32)
        out, self.h, self.c = self.session.run(None, {"input": x, "h": self.h, "c": self.c})
        self.context = frame[-self.CONTEXT:]
        return float(np.asarray(out).reshape(-1)[0])


class Segmenter:
    """Turns a stream of 32 ms frames into finished utterances."""

    PREROLL_FRAMES = 10   # keep 320 ms before speech starts so first syllables aren't cut
    TAIL_FRAMES = 5       # keep 160 ms of silence after speech

    def __init__(self, settings: Settings):
        self.s = settings
        self.preroll: deque[np.ndarray] = deque(maxlen=self.PREROLL_FRAMES)
        self.frames: list[np.ndarray] = []
        self.in_speech = False
        self.silence = 0
        self.speech = 0

    def push(self, frame: np.ndarray, prob: float):
        """Returns (finished_utterance_or_None, is_speaking)."""
        is_voice = prob >= self.s.vad_threshold
        if not self.in_speech:
            if is_voice:
                self.in_speech = True
                self.frames = list(self.preroll) + [frame]
                self.silence, self.speech = 0, 1
            else:
                self.preroll.append(frame)
            return None, self.in_speech

        self.frames.append(frame)
        if is_voice:
            self.silence = 0
            self.speech += 1
        else:
            self.silence += 1

        ended = self.silence * FRAME_MS >= self.s.end_silence_ms
        too_long = len(self.frames) * FRAME_MS >= self.s.max_utterance_s * 1000
        if not (ended or too_long):
            return None, True

        frames = self.frames
        if ended:
            keep = len(frames) - self.silence + self.TAIL_FRAMES
            frames = frames[:max(keep, 1)]
        long_enough = self.speech * FRAME_MS >= self.s.min_speech_ms

        self.in_speech = False
        self.frames = []
        self.preroll.clear()
        if too_long and not ended:
            # keep listening: the sentence continues in the next chunk
            self.in_speech = True
            self.silence, self.speech = 0, 0
        if not long_enough:
            return None, self.in_speech
        return np.concatenate(frames), self.in_speech


# --------------------------------------------------------------------------- #
# Whisper
# --------------------------------------------------------------------------- #
HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.",
    "thank you for watching.", "thank you for watching!", "bye.", "you", ".",
    "please subscribe.", "subtitles by the amara.org community",
}
HALLUCINATION_RE = re.compile(
    r"(subtitles?|captions?|untertitel)\s+(by|von|im auftrag)|amara\.org|zdf|"
    r"thanks? (you )?for watching|copyright ©", re.IGNORECASE)


@dataclass
class Output:
    """One translation of a sentence (one route)."""
    dst: str
    text: str
    translated: bool = False
    raw: str = ""              # text before filters/replacements
    note: str = ""             # warning shown in the transcript
    blocked: str = ""          # filter reason if this output was not sent
    smart: bool = False        # translated by Earshot's smart translation


@dataclass
class Result:
    text: str                  # first decoded text (used for hallucination checks)
    language: str
    lang_prob: float
    audio_s: float
    latency_ms: float
    dropped: bool = False
    reason: str = ""
    filtered: bool = False     # True if every output was blocked by a user filter
    original: str = ""         # what you said, in the language you spoke it in
    english: str = ""          # Whisper's own English translation, if made
    outputs: list = field(default_factory=list)
    chat: str = ""             # final chatbox message


class Transcriber:
    def __init__(self, model: str, spec, compute_type: str, log: Callable[[str], None]):
        from faster_whisper import WhisperModel
        import devices

        if isinstance(spec, int):
            spec = f"nvidia:{spec}" if spec >= 0 else "cpu"
        self.spec = spec
        self.name = model
        self.ct = pick_compute_type(spec, compute_type)
        dev, gpu_index = devices.ct2(spec)
        if dev == "cpu":
            gpu_index = -1
        where = devices.label(spec) if gpu_index >= 0 else "CPU"
        log(f"Loading {model} on {where} ({self.ct}) - first run downloads the model...")
        t0 = time.perf_counter()
        if gpu_index >= 0:
            self.model = WhisperModel(model, device="cuda", device_index=gpu_index,
                                      compute_type=self.ct)
        else:
            threads = max(2, (os.cpu_count() or 4) - 2)
            self.model = WhisperModel(model, device="cpu", compute_type=self.ct,
                                      cpu_threads=threads)
        self.multilingual = self.model.model.is_multilingual
        if not self.multilingual:
            raise RuntimeError("English-only models (*.en) only understand English.")

        self._tok_cache = {}
        self._suppress = {}

        # Warm-up: the first CUDA call is slow, do it now instead of on your first sentence.
        self.run(np.zeros(SAMPLE_RATE, dtype=np.float32), ["en"], 1, lambda _l: (True, True))
        log(f"Model ready in {time.perf_counter() - t0:.1f} s.")

    def _tok(self, lang: str, task: str):
        key = (lang, task)
        if key not in self._tok_cache:
            from faster_whisper.tokenizer import Tokenizer
            from faster_whisper.transcribe import get_suppressed_tokens
            tok = Tokenizer(self.model.hf_tokenizer, True, task, lang)
            self._tok_cache[key] = tok
            self._suppress[key] = get_suppressed_tokens(tok, (-1,))
        return self._tok_cache[key], self._suppress[key]

    def _detect(self, enc, allowed: list[str]) -> tuple[str, float]:
        """Most likely language among the ones you speak."""
        probs = dict(self.model.model.detect_language(enc)[0])
        scores = {c: probs.get(f"<|{c}|>", 0.0) for c in allowed}
        total = sum(scores.values()) or 1.0
        best = max(scores, key=scores.get)
        return best, scores[best] / total

    def _prompt_ids(self, tok, context: str) -> list:
        """Earshot: the recent conversation as Whisper's 'previous text' (helps names and
        words people just used). Whisper allows up to 223 prompt tokens."""
        if not context:
            return []
        ids = tok.encode(" " + context.strip())[-150:]
        return [tok.sot_prev] + ids

    def _decode(self, enc, lang: str, task: str, beam_size: int, context: str = ""):
        tok, suppress = self._tok(lang, task)
        prompt = self._prompt_ids(tok, context) + tok.sot_sequence + [tok.no_timestamps]
        res = self.model.model.generate(
            enc, [prompt],
            beam_size=beam_size, max_length=min(440, 200 + len(prompt)),
            return_scores=True, return_no_speech_prob=True,
            suppress_blank=True, suppress_tokens=suppress,
        )[0]
        ids = res.sequences_ids[0]
        avg_logprob = res.scores[0] * len(ids) / (len(ids) + 1) if ids else -10.0
        return tok.decode(ids).strip(), res.no_speech_prob, avg_logprob

    def run_room(self, audio: np.ndarray, want_english: bool, beam_size: int = 1):
        """Earshot: what another player said. Any language. Returns (lang, text, english)
        or None for silence / noise / music."""
        from faster_whisper.audio import pad_or_trim
        m = self.model
        enc = m.encode(pad_or_trim(m.feature_extractor(audio)))
        probs = m.model.detect_language(enc)[0]
        lang, lprob = None, 0.0
        for token, p in probs:
            code = token.strip("<|>")
            if code in languages.NAME:
                lang, lprob = code, p
                break
        if lang is None or lprob < 0.35:
            return None
        text, nsp, alp = self._decode(enc, lang, "transcribe", beam_size)
        low = text.lower().strip()
        if not any(ch.isalnum() for ch in text) or (nsp > 0.5 and alp < -0.8) or \
                alp < -1.4 or HALLUCINATION_RE.search(low) or low in HALLUCINATIONS:
            return None
        english = ""
        if want_english and lang != "en":
            english, _n, _a = self._decode(enc, lang, "translate", beam_size)
        return lang, text, english

    def run(self, audio: np.ndarray, allowed: list[str], beam_size: int,
            plan: Callable[[str], tuple[bool, bool]], context: str = "") -> Result:
        """allowed: languages to choose from. plan(lang) -> (want_english, want_original):
        which texts the routes for that language need. Both reuse one encoder pass."""
        from faster_whisper.audio import pad_or_trim

        t0 = time.perf_counter()
        m = self.model
        enc = m.encode(pad_or_trim(m.feature_extractor(audio)))   # the one encoder pass

        allowed = allowed or ["en"]
        if len(allowed) == 1:
            lang, lprob = allowed[0], 1.0
        else:
            lang, lprob = self._detect(enc, allowed)

        want_en, want_orig = plan(lang) if callable(plan) else plan.get(lang, (False, True))
        if lang == "en":
            want_en, want_orig = False, True
        if not (want_en or want_orig):
            want_orig = True

        original = english = ""
        checks = None
        if want_orig:
            original, nsp, alp = self._decode(enc, lang, "transcribe", beam_size, context)
            checks = (original, nsp, alp)
        if want_en:
            english, nsp, alp = self._decode(enc, lang, "translate", beam_size, context)
            checks = checks or (english, nsp, alp)
        if lang == "en":
            english = original

        text, nsp, alp = checks
        latency = (time.perf_counter() - t0) * 1000
        r = Result(text, lang, lprob, len(audio) / SAMPLE_RATE, latency,
                   original=original, english=english)

        # filter silence/noise hallucinations
        low = text.lower().strip()
        if not any(ch.isalnum() for ch in text):
            r.dropped, r.reason = True, "empty"
        elif nsp > 0.6 and alp < -1.0:
            r.dropped, r.reason = True, "no speech"
        elif HALLUCINATION_RE.search(low) or (low in HALLUCINATIONS and nsp > 0.2):
            r.dropped, r.reason = True, "hallucination"
        return r


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


JOINERS = {"newline": "\n", "bar": " | ", "slash": " / "}


def format_chatbox(template: str, out: Output, src: str, original: str) -> str:
    """Builds one translation's part of the chatbox message.
    Untranslated text ignores formats that would show {original} twice."""
    if "{original}" in (template or "") and (not out.translated or not original):
        return out.text
    msg = (template or "{text}").format_map(_SafeDict(
        text=out.text, original=original, src=src.upper(), dst=out.dst.upper()))
    return msg.replace("\\n", "\n").strip() or out.text


ROOM = "room"          # route target: the language the people around you speak (Earshot)
_room_resolver: Callable[[], str] = lambda: "en"


def active_routes(s: "Settings") -> list[tuple[str, str]]:
    seen, out = set(), []
    for r in s.routes:
        key = (r.get("src"), r.get("dst"))
        if key[1] == ROOM:
            key = (key[0], _room_resolver())
        if r.get("enabled", True) and key[0] in languages.NAME and key[1] in languages.NAME \
                and key not in seen:
            seen.add(key)
            out.append(key)
    return out


# --------------------------------------------------------------------------- #
# VRChat OSC
# --------------------------------------------------------------------------- #
def split_for_chatbox(text: str, limit: int = CHATBOX_LIMIT) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    parts = []
    while len(text) > limit:
        cut = max(text.rfind(" ", 0, limit), text.rfind("\n", 0, limit))
        if cut <= 0:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


class OscBridge:
    """Sends chatbox messages to VRChat and listens for VRChat's mute state.

    Mute-sync states (reported through on_listen_state):
      off      - "Pause when muted in VRChat" is unticked
      waiting  - listening, but VRChat hasn't sent its mute state yet
      ok       - VRChat's mute state is being received
      busy     - another program (or a second copy of this app) holds the port;
                 retried every few seconds
    """

    def __init__(self, settings: Settings, log: Callable[[str], None],
                 on_mute: Callable[[bool], None],
                 on_listen_state: Callable[[str], None] = lambda st: None,
                 on_afk: Callable[[bool], None] = lambda afk: None,
                 on_param: Callable[[str, object], None] = lambda name, value: None):
        from pythonosc.udp_client import SimpleUDPClient
        self.s = settings
        self.log = log
        self.on_mute = on_mute
        self.on_listen_state = on_listen_state
        self.on_afk = on_afk
        self.on_param = on_param
        self.found_port = None            # VRChat's real input port, found with OSCQuery
        self.finder = None
        self.client = SimpleUDPClient(settings.osc_host, self.send_port())
        self.server = None
        self.listen_state = "off"
        self._listen_lock = threading.Lock()
        self._busy_logged = False
        threading.Thread(target=self._retry_loop, daemon=True).start()
        self.set_listening(True)      # always: mute sync and the AFK status both need it
        self.start_finder()

    def send_port(self) -> int:
        if self.s.oscquery and self.found_port:
            return int(self.found_port)
        return int(self.s.osc_port)

    def retarget(self):
        from pythonosc.udp_client import SimpleUDPClient
        self.client = SimpleUDPClient(self.s.osc_host, self.send_port())

    def start_finder(self):
        """Looks for VRChat's real chatbox port with OSCQuery (live, while the app runs)."""
        self.stop_finder()
        if not self.s.oscquery:
            if self.found_port:
                self.found_port = None
                self.retarget()
            return
        try:
            import oscquery
            self.finder = oscquery.VRChatFinder(self._found, self.log)
            self.finder.start()
        except Exception as e:
            self.finder = None
            self.log(f"OSCQuery search for VRChat not available ({type(e).__name__}: {e}). "
                     f"Sending to port {self.s.osc_port}.")

    def stop_finder(self):
        if self.finder is not None:
            self.finder.stop()
            self.finder = None

    def _found(self, port):
        before = self.send_port()
        self.found_port = port
        self.retarget()
        if port:
            note = "" if port == int(self.s.osc_port) else \
                f" (not {self.s.osc_port} - VRChat uses another port here)"
            self.log(f"Found VRChat with OSCQuery: chatbox goes to {self.s.osc_host}:{port}{note}.")
        elif before != int(self.s.osc_port):
            self.log(f"VRChat closed - chatbox goes to port {self.s.osc_port} again.")

    def _set_listen_state(self, state: str):
        if state != self.listen_state:
            self.listen_state = state
            self.on_listen_state(state)

    def _on_mute_msg(self, _address, *args):
        if args:
            self._set_listen_state("ok")
            self.on_mute(bool(args[0]))

    def _on_param(self, address, *args):
        if args:
            self.on_param(address.rsplit("/", 1)[-1], args[0])

    def _on_afk_msg(self, _address, *args):
        if args:
            self._set_listen_state("ok")
            self.on_afk(bool(args[0]))

    def set_listening(self, on: bool):
        """Start or stop listening for VRChat's avatar parameters (live, no restart needed).

        1. Listen on the chosen port (9001 = VRChat's default).
        2. If it's taken (e.g. an OSC router like VOR), listen on a free port instead and
           announce it with OSCQuery, so VRChat sends to us as well.
        3. Without OSCQuery: show "busy" and retry every few seconds."""
        with self._listen_lock:
            if not on:
                self._stop_server()
                self._set_listen_state("off")
                return
            if self.server is not None:
                return
            from pythonosc.dispatcher import Dispatcher
            from pythonosc.osc_server import ThreadingOSCUDPServer
            d = Dispatcher()
            d.map("/avatar/parameters/MuteSelf", self._on_mute_msg)
            d.map("/avatar/parameters/AFK", self._on_afk_msg)
            d.map("/avatar/parameters/*", self._on_param)
            port = int(self.s.osc_listen_port)
            fallback = False
            try:
                self.server = ThreadingOSCUDPServer(("127.0.0.1", port), d)
            except OSError as e:
                if not self.s.oscquery:
                    if not self._busy_logged:
                        self._busy_logged = True
                        self.log(f"Can't listen on port {port}: {e.strerror or e}. Another "
                                 "program (an OSC router like VOR, face tracking, ...) uses it. "
                                 "Set the listen port to the port that program forwards to, or "
                                 "turn on 'Find VRChat with OSCQuery'. Retrying...")
                    self._set_listen_state("busy")
                    return
                self.server = ThreadingOSCUDPServer(("127.0.0.1", 0), d)   # any free port
                fallback = True
            self.actual_port = self.server.server_address[1]
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.via_oscquery = False
            if self.s.oscquery and (fallback or self.actual_port != 9001):
                try:
                    import oscquery
                    from version import APP_NAME
                    self.oscq = oscquery.OSCQueryService(APP_NAME.replace(" ", ""),
                                                         self.actual_port, self.log)
                    self.oscq.start()
                    self.via_oscquery = True
                except Exception as ex:
                    self.oscq = None
                    self.log(f"OSCQuery could not start ({type(ex).__name__}: {ex}).")
            if fallback:
                msg = (f"Port {port} is used by another program (for example an OSC router "
                       f"like VOR). Listening on port {self.actual_port} instead")
                self.log(msg + (" and telling VRChat about it via OSCQuery." if self.via_oscquery
                                else ". VRChat won't find it without OSCQuery."))
            elif self._busy_logged:
                self.log(f"Port {port} is free again - mute sync works now.")
            self._busy_logged = fallback
            self._set_listen_state("oscquery" if self.via_oscquery and fallback else "waiting")

    actual_port = 0
    via_oscquery = False
    oscq = None

    def _stop_server(self):
        if self.oscq is not None:
            try:
                self.oscq.stop()
            except Exception:
                pass
            self.oscq = None
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def restart_listener(self):
        """After changing the listen port or the OSCQuery option."""
        with self._listen_lock:
            self._stop_server()
            self.listen_state = ""
        self.set_listening(True)

    def _retry_loop(self):
        while True:
            time.sleep(5)
            if self.listen_state == "busy":
                self.set_listening(True)

    def typing(self, on: bool):
        if self.s.osc_enabled and self.s.typing_indicator:
            try:
                self.client.send_message("/chatbox/typing", bool(on))
            except OSError:
                pass

    def send_chatbox(self, text: str):
        """Sends one finished chatbox message (timing is handled by chatbox.Chatbox)."""
        if not self.s.osc_enabled:
            return
        target = (self.s.osc_host, self.send_port())
        if target != getattr(self, "_logged_target", None):
            self._logged_target = target
            how = "found with OSCQuery" if self.s.oscquery and self.found_port else \
                ("VRChat's default" if target[1] == 9000 else "your setting; VRChat's default is 9000")
            self.log(f"Chatbox messages go to {target[0]}:{target[1]} ({how}).")
        try:
            self.client.send_message("/chatbox/input",
                                     [text[:CHATBOX_LIMIT], True, bool(self.s.notify_sound)])
        except OSError as e:
            self.log(f"OSC send failed: {e}")


# --------------------------------------------------------------------------- #
# Engine: ties everything together, runs on background threads
# --------------------------------------------------------------------------- #
class Engine:
    def __init__(self, settings: Settings, *,
                 on_log: Callable[[str], None],
                 on_result: Callable[[Result], None],
                 on_level: Callable[[float, bool], None],
                 on_state: Callable[[str], None],
                 on_translator: Callable[[str, int], None] = lambda st, pct: None,
                 on_vrc: Callable[[str, bool], None] = lambda st, muted: None,
                 on_preview: Callable[[str, bool], None] = lambda text, sent: None,
                 on_heard: Callable[[object], None] = lambda line: None,
                 on_earshot: Callable[[str, bool], None] = lambda text, ok: None,
                 on_room_level: Callable[[float, bool], None] = lambda lv, v: None,
                 on_smart: Callable[[str, int], None] = lambda st, pct: None):
        self.s = settings
        self.on_vrc = on_vrc
        self.on_translator = on_translator
        self.translator_state = "missing"
        self.log = on_log
        self.on_result = on_result
        self.on_level = on_level
        self.on_state = on_state

        self.enabled = True          # GUI listen toggle
        self.vrc_muted = False
        self.transcriber: Optional[Transcriber] = None
        self.translator: Optional[nllb.TextTranslator] = None
        self._translator_lock = threading.Lock()
        self._warned_no_translator = False
        self.stream = None
        self.audio_q: queue.Queue[np.ndarray] = queue.Queue()
        self.utt_q: queue.Queue[np.ndarray] = queue.Queue()
        self._resampler = None
        self._buf = np.zeros(0, dtype=np.float32)
        self._speaking = False
        self._busy = False
        self._model_lock = threading.Lock()
        self._audio_gen = 0

        self.filters = FilterSet()
        self.update_filters()
        self.osc = OscBridge(settings, self.log, self._set_vrc_mute,
                             lambda st: self.on_vrc(st, self.vrc_muted),
                             lambda afk: self.chatbox.set_afk(afk),
                             lambda name, value: self.chatbox.set_vrc_param(name, value))
        self.chatbox = Chatbox(settings, self.osc.send_chatbox, self.log, on_preview)
        self.vad = StreamingVAD()
        self.segmenter = Segmenter(settings)
        import earshot as ES
        self.on_smart = on_smart
        self.earshot = ES.Earshot(self, self.log, on_heard, on_earshot, on_room_level)
        global _room_resolver
        _room_resolver = self.earshot.target_room_language
        self.chatbox.room_language = lambda: (self.earshot.room_lang
                                              if self.earshot.active else None)
        threading.Thread(target=self._vad_loop, daemon=True).start()
        threading.Thread(target=self._asr_loop, daemon=True).start()
        self.earshot.apply()
        self.load_smart_async()

    def update_filters(self):
        """Call after changing any filter setting."""
        self.filters = FilterSet.from_settings(self.s)
        for err in self.filters.errors:
            self.log(f"Filter skipped - {err}")

    # ---- state -----------------------------------------------------------
    @property
    def listening(self) -> bool:
        return self.enabled and not (self.s.follow_vrc_mute and self.vrc_muted)

    def _set_vrc_mute(self, muted: bool):
        if muted != self.vrc_muted:
            self.vrc_muted = muted
            if self.s.follow_vrc_mute:
                self.log("VRChat mic muted - paused." if muted
                         else "VRChat mic unmuted - listening.")
            self.on_vrc(self.osc.listen_state, muted)
            self._emit_state()

    def set_follow_mute(self, on: bool):
        self.s.follow_vrc_mute = on
        self.on_vrc(self.osc.listen_state, self.vrc_muted)
        self._emit_state()

    def set_enabled(self, on: bool):
        self.enabled = on
        self._emit_state()

    def _emit_state(self):
        if self.transcriber is None:
            self.on_state("loading")
        elif not self.listening:
            self.on_state("paused")
        elif self._busy:
            self.on_state("working")
        elif self._speaking:
            self.on_state("speaking")
        else:
            self.on_state("ready")

    # ---- model -----------------------------------------------------------
    def load_model_async(self):
        def work():
            with self._model_lock:
                old, self.transcriber = self.transcriber, None
                if callable(getattr(type(old), "close", None)):
                    old.close()                    # a whisper.cpp server: stop it now
                del old
                self._emit_state()
                try:
                    import devices
                    import workers
                    self.transcriber = workers.make(
                        "whisper", devices.speech_spec(self.s), self.s.model,
                        self.s.compute_type, log=self.log)
                except Exception as e:
                    self.log(f"Model failed to load: {e}")
                    self.on_state("error")
                    return
            self._emit_state()
        threading.Thread(target=work, daemon=True).start()

    def model_places(self) -> list:
        """[(label, device)] for every AI model loaded right now."""
        import devices
        out = []
        tr = self.transcriber
        if tr is not None:
            out.append((f"Speech ({getattr(tr, 'name', '') or self.s.model})",
                        getattr(tr, "spec", "") or devices.speech_spec(self.s)))
        t = self.translator
        if t is not None:
            out.append(("Translator", getattr(t, "spec", "") or
                        devices.resolve(self.s.translator_device, self.s)))
        es = self.earshot
        sm = getattr(es, "smart", None)
        if sm is not None:
            out.append(("Smart AI", str(getattr(sm, "key", "")).rsplit("|", 1)[-1] or "cpu"))
        lk = getattr(es, "_listener_key", None)
        if getattr(es, "listener", None) is not None and lk:
            out.append((f"Earshot listening ({lk[1]})", lk[0]))
        return [(label, devices.resolve(spec, self.s)) for label, spec in out]

    def configured_places(self) -> list:
        """[(label, device, loaded)] for every model the settings use - also the ones that
        are still loading, not installed yet or failed (so their card is shown too)."""
        import devices
        loaded = self.model_places()
        out = [(label, spec, True) for label, spec in loaded]
        have = {label.split(" (")[0] for label, _s in loaded}
        want = [(f"Speech ({self.s.model})", devices.speech_spec(self.s))]
        try:
            if self.translator_needed():
                want.append(("Translator", devices.resolve(self.s.translator_device, self.s)))
        except Exception:
            pass
        es = getattr(self.s, "earshot", {}) or {}
        if es.get("enabled") and es.get("smart"):
            want.append(("Smart AI", devices.resolve(es.get("smart_device"), self.s)))
        if es.get("enabled") and (es.get("listen_device") or "same") != "same":
            want.append((f"Earshot listening ({es.get('listen_model', 'small')})",
                         devices.resolve(es.get("listen_device"), self.s)))
        for label, spec in want:
            if label.split(" (")[0] not in have:
                out.append((label, spec, False))
        return out

    def translator_needed(self) -> bool:
        if any(r.get("dst") == ROOM and r.get("enabled", True) for r in self.s.routes):
            return True                      # the room language can change any time
        es = getattr(self.s, "earshot", {}) or {}
        if es.get("enabled") and es.get("show_heard") and es.get("show_in", "en") != "en":
            return True
        for src, dst in active_routes(self.s):
            if src != dst and (dst != "en" or self.s.nllb_for_english):
                return True
        return self.s.translate_typed

    def _tr_state(self, state: str, pct: int = 0):
        self.translator_state = state
        self.on_translator(state, pct)

    def load_translator_async(self, force: bool = False):
        """Loads NLLB if it's installed and something needs it."""
        if self.translator_state == "downloading":
            return
        if not nllb.is_installed(self.s.translator_model):
            self.translator = None
            self._tr_state("missing")
            return
        if not force and not self.translator_needed():
            if self.translator is None:
                self._tr_state("installed")
            return
        tr = self.translator
        import devices
        if (not force and tr is not None and tr.name == self.s.translator_model
                and getattr(tr, "spec", "") == devices.resolve(self.s.translator_device, self.s)
                and self.translator_state == "ready"):
            return

        def work():
            with self._translator_lock:
                self.translator = None
                self._tr_state("loading")
                try:
                    self.log(f"Loading translator {self.s.translator_model}...")
                    import devices
                    import workers
                    self.translator = workers.make(
                        "nllb", devices.resolve(self.s.translator_device, self.s),
                        self.s.translator_model, log=self.log)
                    self._tr_state("ready")
                except Exception as e:
                    self.log(f"Translator failed to load: {e}")
                    self._tr_state("error")
        threading.Thread(target=work, daemon=True).start()

    def install_translator_async(self):
        """Downloads the selected NLLB model, then loads it."""
        if self.translator_state == "downloading":
            return
        name = self.s.translator_model

        def work():
            self._tr_state("downloading", 0)
            repo, size = nllb.REPOS[name]
            self.log(f"Downloading {name} (~{size} MB) from huggingface.co/{repo} ...")
            try:
                nllb.download(name, lambda pct: self._tr_state("downloading", pct))
                self.log(f"Translator {name} installed.")
                self.translator_state = "installed"
            except Exception as e:
                self.log(f"Translator download failed: {e}")
                self._tr_state("error")
                return
            self.load_translator_async(force=True)
        threading.Thread(target=work, daemon=True).start()

    # ---- Earshot smart translation (local AI) -----------------------------
    def _smart_state(self, state: str, pct: int = 0):
        self.earshot.smart_state = state
        self.on_smart(state, pct)

    def load_smart_async(self, force: bool = False):
        import smart
        cfg = self.earshot.cfg
        if not (cfg.get("enabled") and cfg.get("smart")):
            if self.earshot.smart is not None:
                self.earshot.smart = None
                self.log("Smart translation unloaded.")
            self._smart_state("off" if not smart.is_installed(cfg) else "installed")
            return
        if not smart.is_installed(cfg):
            self.earshot.smart = None
            self._smart_state("missing")
            return
        import devices
        spec = devices.resolve(cfg.get("smart_device"), self.s)
        cur = self.earshot.smart
        if cur is not None and not force and getattr(cur, "key", "") == smart.model_key(cfg, spec):
            self._smart_state("ready")
            return

        def work():
            self.earshot.smart = None
            self._smart_state("loading")
            try:
                import workers
                self.earshot.smart = workers.make("smart", spec, cfg, log=self.log)
                self._smart_state("ready")
            except Exception as e:
                self.log(f"Smart translation failed to load: {e}")
                self._smart_state("error")
        threading.Thread(target=work, daemon=True).start()

    def install_smart_async(self):
        import smart
        cfg = self.earshot.cfg
        if self.earshot.smart_state == "downloading":
            return

        def work():
            self._smart_state("downloading", 0)
            try:
                smart.download(cfg.get("smart_model"), lambda p: self._smart_state("downloading", p),
                               self.log)
            except Exception as e:
                self.log(f"Smart translation download failed: {e}")
                self._smart_state("error")
                return
            self.load_smart_async(force=True)
        threading.Thread(target=work, daemon=True).start()

    def _translate(self, text: str, src: str, dst: str) -> Optional[str]:
        tr = self.translator          # no lock: don't stall speech while a model loads
        if tr is None:
            return None
        return tr.translate(text, src, dst)

    # ---- audio -----------------------------------------------------------
    def start_audio(self):
        import sounddevice as sd
        self.stop_audio()
        self._audio_gen += 1
        gen = self._audio_gen
        device = resolve_microphone(self.s.mic_device)
        if self.s.mic_device and device is None:
            self.log(f"Microphone '{self.s.mic_device}' not found - using the default one.")
        rate = SAMPLE_RATE
        try:
            sd.check_input_settings(device=device, samplerate=SAMPLE_RATE, channels=1)
        except Exception:
            rate = int(sd.query_devices(device, "input")["default_samplerate"])
        if rate != SAMPLE_RATE:
            import soxr
            self._resampler = soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")
        else:
            self._resampler = None

        def callback(indata, _frames, _time, _status):
            if gen == self._audio_gen:
                self.audio_q.put(indata[:, 0].copy())

        self.stream = sd.InputStream(device=device, channels=1, samplerate=rate,
                                     dtype="float32", blocksize=int(rate * 0.032),
                                     callback=callback)
        self.stream.start()
        name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]
        self.log(f"Microphone: {name} @ {rate} Hz")

    def stop_audio(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    # ---- loops -----------------------------------------------------------
    def _vad_loop(self):
        level_t = 0.0
        while True:
            chunk = self.audio_q.get()
            if self._resampler is not None:
                chunk = self._resampler.resample_chunk(chunk)
            self._buf = np.concatenate([self._buf, chunk])
            while len(self._buf) >= FRAME:
                frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
                if not self.listening:
                    if self._speaking:
                        self._speaking = False
                        self.osc.typing(False)
                    self.segmenter = Segmenter(self.s)
                    self.vad.reset()
                    continue
                prob = self.vad(frame)
                utt, speaking = self.segmenter.push(frame, prob)
                if speaking != self._speaking:
                    self._speaking = speaking
                    if speaking:
                        self.osc.typing(True)
                    elif not self._busy and self.utt_q.empty() and utt is None:
                        self.osc.typing(False)
                    self._emit_state()
                if utt is not None:
                    self.utt_q.put(utt)
                now = time.monotonic()
                if now - level_t > 0.05:
                    level_t = now
                    rms = float(np.sqrt(np.mean(frame ** 2)))
                    self.on_level(min(1.0, rms * 8), prob >= self.s.vad_threshold)

    # ---- routing -----------------------------------------------------------
    def _uses_nllb_for_en(self) -> bool:
        return self.s.nllb_for_english and self.translator is not None

    def _plan(self, lang: str) -> tuple[bool, bool]:
        """Which Whisper texts the routes for `lang` need: (english, original)."""
        targets = [d for sr, d in active_routes(self.s) if sr == lang]
        want_en = lang != "en" and "en" in targets and not self._uses_nllb_for_en()
        need_orig = (
            "{original}" in self.s.chatbox_template
            or any(t == lang or t != "en" for t in targets)
            or ("en" in targets and self._uses_nllb_for_en())
        )
        return want_en, need_orig

    def _untranslated_note(self) -> str:
        return {
            "loading": "not translated - translator is still loading",
            "downloading": "not translated - translator is still downloading",
            "error": "not translated - translator failed, see log",
        }.get(self.translator_state, "not translated - click “Install translator” at the top")

    def _build_outputs(self, src: str, original: str, english: str,
                       targets: list[str]) -> list[Output]:
        outs = []
        for dst in targets:
            smart_text = None
            if dst != src and original:
                smart_text = self.earshot.smart_translate(original, src, dst)
            if smart_text:
                outs.append(Output(dst, smart_text, translated=True, smart=True))
            elif dst == src:
                outs.append(Output(dst, original))
            elif dst == "en" and english and not self._uses_nllb_for_en():
                outs.append(Output(dst, english, translated=True))
            else:
                text = self._translate(original, src, dst) if original else None
                if text is None:
                    outs.append(Output(dst, original or english, note=self._untranslated_note()))
                else:
                    outs.append(Output(dst, text, translated=True))
        return outs

    def _finish(self, src: str, original: str, outs: list[Output],
                filters: Optional[FilterSet] = None) -> str:
        """Applies filters to each output and builds the chatbox message."""
        filters = filters or self.filters
        parts = []
        for o in outs:
            f = filters.apply(o.text)
            o.raw, o.text = o.text, f.text
            if f.blocked:
                o.blocked = f.reason
                continue
            parts.append(format_chatbox(self.s.chatbox_template, o, src, original))
        return JOINERS.get(self.s.join_with, "\n").join(parts)

    def _asr_loop(self):
        while True:
            audio = self.utt_q.get()
            with self._model_lock:
                tr = self.transcriber
                if tr is None:
                    continue
                routes = active_routes(self.s)
                sources = list(dict.fromkeys(src for src, _d in routes))
                if not sources:
                    self.log("No translation routes are switched on - nothing to do.")
                    continue
                self._busy = True
                self._emit_state()
                try:
                    plan = {src: self._plan(src) for src in sources}     # works across processes
                    r = tr.run(audio, sources, self.s.beam_size, plan,
                               self.earshot.whisper_prompt())
                    if not r.dropped:
                        echo = self.earshot.check_echo(r.original or r.text)
                        if echo is not None:
                            r.dropped = True
                            r.reason = f"echo of the game (“{echo.text[:40]}”)"
                    if not r.dropped:
                        t1 = time.perf_counter()
                        targets = [d for s_, d in routes if s_ == r.language]
                        r.outputs = self._build_outputs(r.language, r.original, r.english, targets)
                        r.latency_ms += (time.perf_counter() - t1) * 1000
                except Exception as e:
                    self.log(f"Transcription error: {e}")
                    r = None
                self._busy = False
            if r is not None:
                if not r.dropped and self.s.follow_vrc_mute and self.vrc_muted:
                    r.dropped, r.reason = True, "muted in VRChat before it was sent"
                if not r.dropped:
                    r.chat = self._finish(r.language, r.original, r.outputs)
                    if r.chat.strip():
                        self.chatbox.set_speech(r.chat)
                        self.earshot.note_own(r.language, r.original or r.text, r.chat)
                    else:
                        r.dropped, r.filtered = True, True
                        r.reason = "; ".join(o.blocked for o in r.outputs if o.blocked)
                self.on_result(r)
            if not self._speaking and self.utt_q.empty():
                self.osc.typing(False)
            self._emit_state()

    def typed_language(self) -> str:
        routes = active_routes(self.s)
        return routes[0][0] if routes else "en"

    def send_manual(self, text: str):
        """Returns (chat_message, block_reason). With "Translate typed messages" on, typed
        text counts as the language of the first route and goes through its routes."""
        text = text.strip()
        if not text:
            return "", ""
        filters = self.filters if self.s.filter_typed else FilterSet(enabled=False)
        if self.s.translate_typed:
            src = self.typed_language()
            targets = [d for s_, d in active_routes(self.s) if s_ == src] or [src]
            outs = self._build_outputs(src, text, text if src == "en" else "", targets)
            chat = self._finish(src, text, outs, filters)
            if not chat.strip():
                return text, "; ".join(o.blocked for o in outs if o.blocked) or "blocked"
        else:
            f = filters.apply(text)
            if f.blocked:
                return text, f.reason
            chat = f.text
        self.chatbox.set_speech(chat)
        return chat, ""
