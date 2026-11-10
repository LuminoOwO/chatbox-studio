"""
Speech recognition on any graphics chip through Vulkan: whisper.cpp's whisper-server.

Used for devices "vulkan:N" - AMD cards and APUs without ROCm, Intel Arc and Intel
integrated graphics, and any other chip with a Vulkan driver. The server runs as its own
process on 127.0.0.1 with one model loaded; this module sends it the audio of each sentence
and reads the text back. It offers the same methods as engine.Transcriber (run, run_room),
so the rest of the app doesn't care which engine listens.

The engine (whisper-server built with Vulkan) is downloaded by the setup into
<data dir>/engines/whispercpp/, the models (ggml format) into <models>/ggml/.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import paths

SAMPLE_RATE = 16000
WHISPERCPP_VERSION = "1.9.5"
# Release assets of the Chatbox Studio repository (built by .github/workflows/engines.yml)
ENGINE_URL = ("https://github.com/LuminoOwO/chatbox-studio/releases/download/"
              f"engine-whispercpp-{WHISPERCPP_VERSION}/"
              "whisper-vulkan-{platform}.zip")
MODEL_REPO = "ggerganov/whisper.cpp"
# Whisper model -> (ggml file, download MB, graphics memory MB)
MODELS = {
    "small": ("ggml-small-q8_0.bin", 264, 600),
    "medium": ("ggml-medium-q5_0.bin", 539, 1000),
    "large-v2": ("ggml-large-v2-q5_0.bin", 1080, 1700),
    "large-v3": ("ggml-large-v3-q5_0.bin", 1080, 1700),
}


def engine_dir() -> Path:
    return paths.data_dir() / "engines" / "whispercpp"


def server_exe() -> Path:
    return engine_dir() / ("whisper-server.exe" if paths.IS_WINDOWS else "whisper-server")


def installed() -> bool:
    return server_exe().exists()


def platform_tag() -> str:
    return "windows-x64" if paths.IS_WINDOWS else "linux-x86_64"


def engine_url() -> str:
    return ENGINE_URL.format(platform=platform_tag())


def model_file(name: str) -> Path:
    fname = MODELS.get(name, MODELS["small"])[0]
    return paths.models_dir() / "ggml" / fname


def model_installed(name: str) -> bool:
    f = model_file(name)
    return f.exists() and f.stat().st_size > 10_000_000


def download_model(name: str, progress: Callable[[int], None] = lambda p: None,
                   log: Callable[[str], None] = print) -> Path:
    from huggingface_hub import hf_hub_download
    fname, size_mb, _v = MODELS[name]
    dest = model_file(name).parent
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Downloading the {name} speech model for Vulkan (~{size_mb} MB) ...")
    done = threading.Event()
    part = dest / fname

    def watch():
        while not done.wait(0.5):
            try:
                have = sum(f.stat().st_size for f in dest.rglob(fname + "*")) / 1e6
            except OSError:
                have = 0
            progress(min(99, int(have / size_mb * 100)))
    threading.Thread(target=watch, daemon=True).start()
    try:
        hf_hub_download(MODEL_REPO, fname, local_dir=str(dest))
    finally:
        done.set()
    progress(100)
    return part


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wav_bytes(audio: np.ndarray) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def multipart(fields: dict, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = "----chatbox" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                  f"{v}\r\n".encode())
    out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
              f"filename=\"audio.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode())
    out.write(file_bytes)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def scores(res: dict) -> tuple[float, float]:
    """(no-speech probability, average log probability) of a verbose_json answer."""
    segs = res.get("segments") or []
    if not segs:
        return 1.0, -10.0
    nsp = float(segs[0].get("no_speech_prob", 0.0) or 0.0)
    alps = [float(s.get("avg_logprob", -10.0)) for s in segs]
    return nsp, sum(alps) / len(alps)


def best_language(res: dict, allowed: Optional[list] = None) -> tuple[str, float]:
    """Most likely language (among `allowed`, if given) and its share of the probability."""
    probs = {str(k).lower(): float(v) for k, v in (res.get("language_probabilities") or {}).items()}
    if not probs:
        return (allowed or ["en"])[0], 0.0
    if allowed:
        cand = {c: probs.get(c, 0.0) for c in allowed}
        total = sum(cand.values()) or 1.0
        best = max(cand, key=cand.get)
        return best, cand[best] / total
    best = max(probs, key=probs.get)
    return best, probs[best]


def detected_code(res: dict) -> str:
    probs = res.get("language_probabilities") or {}
    return max(probs, key=probs.get).lower() if probs else ""


_running: "set[Transcriber]" = set()


def stop_all():
    for t in list(_running):
        t.close()


class Transcriber:
    """Same interface as engine.Transcriber, backed by whisper-server on a Vulkan device."""

    def __init__(self, model: str, spec: str, log: Callable[[str], None]):
        import devices
        if not installed():
            raise RuntimeError("the Vulkan speech engine isn't installed - open Chatbox Studio "
                               "Setup and tick \"Graphics support for any GPU (Vulkan)\"")
        if model not in MODELS:
            model = "medium"
        mf = model_file(model)
        if not model_installed(model):
            download_model(model, log=log)
        self.spec = spec
        self.name = model
        self.ct = "Vulkan"
        self.multilingual = True
        self.log = log
        self._lock = threading.Lock()
        self._tail: list[str] = []
        self.device_line = ""
        _vendor, idx = devices.parse(spec)
        self.port = _free_port()
        threads = max(2, min(8, (os.cpu_count() or 4) // 2))
        cmd = [str(server_exe()), "-m", str(mf), "--host", "127.0.0.1", "--port",
               str(self.port), "-t", str(threads), "-nt", "-sns"]
        env = dict(os.environ)
        if idx >= 0:
            env["GGML_VK_VISIBLE_DEVICES"] = str(idx)
        where = devices.label(spec)
        log(f"Loading {model} on {where} (Vulkan, whisper.cpp) ...")
        t0 = time.perf_counter()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env,
                                     cwd=str(engine_dir()), **paths.hidden_subprocess_kwargs())
        _running.add(self)
        threading.Thread(target=self._read_output, daemon=True,
                         name="whispercpp-log").start()
        self._wait_ready(timeout=180)
        if self.device_line:
            log(f"whisper.cpp: {self.device_line}")
        self.run(np.zeros(SAMPLE_RATE, dtype=np.float32), ["en"], 1, lambda _l: (False, True))
        log(f"Model ready in {time.perf_counter() - t0:.1f} s.")

    # ------------------------------------------------------------------ server
    def _read_output(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            self._tail = (self._tail + [line])[-40:]
            low = line.lower()
            if "ggml_vulkan:" in low and ("=" in line or "found" in low) and \
                    not self.device_line.count("="):
                self.device_line = line.split("ggml_vulkan:", 1)[-1].strip()

    def _wait_ready(self, timeout: float):
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                tail = "\n".join(self._tail[-8:])
                raise RuntimeError(f"whisper-server stopped (code {self.proc.returncode}):\n{tail}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=2) as r:
                    if r.status < 500:
                        return
            except urllib.error.HTTPError as e:
                if e.code < 500:
                    return
            except OSError:
                pass
            time.sleep(0.3)
        self.close()
        raise RuntimeError("whisper-server didn't start in time")

    def request(self, audio: np.ndarray, language: str = "auto", translate: bool = False,
                prompt: str = "", beam_size: int = 1) -> dict:
        fields = {"response_format": "verbose_json", "language": language,
                  "translate": "true" if translate else "false", "temperature": "0.0",
                  "temperature_inc": "0.0", "no_timestamps": "true",
                  "beam_size": str(max(1, beam_size)) if beam_size > 1 else "-1"}
        if prompt:
            fields["prompt"] = prompt
        body, ctype = multipart(fields, wav_bytes(audio))
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/inference", data=body,
                                     headers={"Content-Type": ctype}, method="POST")
        with self._lock:
            if self.proc.poll() is not None:
                raise RuntimeError("whisper-server has stopped - reload the model")
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
        if "error" in res:
            raise RuntimeError(f"whisper-server: {res['error']}")
        return res

    def close(self):
        _running.discard(self)
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass

    def __del__(self):
        self.close()

    # ------------------------------------------------------------------ speech
    def run(self, audio: np.ndarray, allowed: list[str], beam_size: int,
            plan, context: str = ""):
        from engine import HALLUCINATION_RE, HALLUCINATIONS, Result
        t0 = time.perf_counter()
        allowed = allowed or ["en"]
        first = None
        if len(allowed) == 1:
            lang, lprob = allowed[0], 1.0
        else:
            first = self.request(audio, "auto", False, context, beam_size)
            lang, lprob = best_language(first, allowed)
            if detected_code(first) != lang:
                first = None                    # it wrote another language: decode again
        want_en, want_orig = plan(lang) if callable(plan) else plan.get(lang, (False, True))
        if lang == "en":
            want_en, want_orig = False, True
        if not (want_en or want_orig):
            want_orig = True
        original = english = ""
        checks = None
        if want_orig:
            res = first or self.request(audio, lang, False, context, beam_size)
            original = (res.get("text") or "").strip()
            checks = (original, *scores(res))
        if want_en:
            res = self.request(audio, lang, True, context, beam_size)
            english = (res.get("text") or "").strip()
            checks = checks or (english, *scores(res))
        if lang == "en":
            english = original
        text, nsp, alp = checks
        latency = (time.perf_counter() - t0) * 1000
        r = Result(text, lang, lprob, len(audio) / SAMPLE_RATE, latency,
                   original=original, english=english)
        low = text.lower().strip()
        if not any(ch.isalnum() for ch in text):
            r.dropped, r.reason = True, "empty"
        elif nsp > 0.6 and alp < -1.0:
            r.dropped, r.reason = True, "no speech"
        elif HALLUCINATION_RE.search(low) or (low in HALLUCINATIONS and nsp > 0.2):
            r.dropped, r.reason = True, "hallucination"
        return r

    def run_room(self, audio: np.ndarray, want_english: bool, beam_size: int = 1):
        """Earshot: what another player said, any language -> (lang, text, english) or None."""
        from engine import HALLUCINATION_RE, HALLUCINATIONS
        import languages
        res = self.request(audio, "auto", False, "", beam_size)
        lang, lprob = best_language(res)
        if lang not in languages.NAME or lprob < 0.35:
            return None
        text = (res.get("text") or "").strip()
        nsp, alp = scores(res)
        low = text.lower()
        if not any(ch.isalnum() for ch in text) or (nsp > 0.5 and alp < -0.8) or \
                alp < -1.4 or HALLUCINATION_RE.search(low) or low in HALLUCINATIONS:
            return None
        english = ""
        if want_english and lang != "en":
            english = (self.request(audio, lang, True, "", beam_size).get("text") or "").strip()
        return lang, text, english
