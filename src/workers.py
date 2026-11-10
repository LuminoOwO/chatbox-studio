"""
Runs AI models on another graphics card vendor, in a helper process.

The app itself uses the standard (NVIDIA / CPU) build of CTranslate2. Models placed on an
AMD card are loaded by model_worker.py, a small helper that starts with the ROCm build
first on its path. The app talks to it through pipes; the helper's objects look like the
normal ones (same methods), so the engine doesn't care where a model runs.

    make("whisper", "amd:0", "large-v3", "auto", log)    -> Transcriber or proxy
    make("nllb", "nvidia:1", name, log)                  -> TextTranslator
    make("smart", "amd:0", cfg, log)                     -> SmartTranslator or proxy
"""

from __future__ import annotations

import os
import pickle
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

import devices
import paths

HERE = Path(__file__).resolve().parent


def make(kind: str, spec: str, *args, log: Callable[[str], None] = print):
    if devices.is_remote(spec):
        return worker("rocm", log).load(kind, spec, *args)
    return build_local(kind, spec, *args, log=log)


def build_local(kind: str, spec: str, *args, log: Callable[[str], None] = print):
    if not devices.supports(spec, kind):
        log(f"The {'translator' if kind == 'nllb' else 'smart AI'} can't use "
            f"{devices.label(spec)} (Vulkan works for speech recognition only) - it runs on "
            "the CPU.")
        spec = "cpu"
    if kind == "whisper" and devices.parse(spec)[0] == "vulkan":
        import whispercpp
        model, _compute = args
        return whispercpp.Transcriber(model, spec, log)
    if kind == "whisper":
        from engine import Transcriber
        model, compute = args
        return Transcriber(model, spec, compute, log)
    if kind == "nllb":
        import translator
        (name,) = args
        return translator.TextTranslator(name, spec, log)
    if kind == "smart":
        import smart
        (cfg,) = args
        return smart.SmartTranslator(cfg, spec, log)
    raise ValueError(kind)


# --------------------------------------------------------------------------- #
# The helper process
# --------------------------------------------------------------------------- #
_workers: dict[str, "Worker"] = {}
_wlock = threading.Lock()


def worker(backend: str, log: Callable[[str], None]) -> "Worker":
    with _wlock:
        w = _workers.get(backend)
        if w is None or not w.alive():
            w = Worker(backend, log)
            _workers[backend] = w
        return w


def stop_all():
    with _wlock:
        for w in _workers.values():
            w.stop()
        _workers.clear()
    try:
        import whispercpp
        whispercpp.stop_all()
    except Exception:
        pass


def worker_env(backend: str) -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CHATBOX_STUDIO_ENGINE"] = backend
    if backend == "rocm" and not paths.IS_WINDOWS:
        import gpus
        libs = gpus.rocm_libs_linux()
        if libs:
            env["LD_LIBRARY_PATH"] = libs + (":" + env["LD_LIBRARY_PATH"]
                                             if env.get("LD_LIBRARY_PATH") else "")
        ov = rocm_override()
        if ov and "HSA_OVERRIDE_GFX_VERSION" not in env:
            env["HSA_OVERRIDE_GFX_VERSION"] = ov
    return env


def rocm_override() -> str:
    """HSA_OVERRIDE_GFX_VERSION for AMD chips ROCm only runs on as a close relative (APUs
    like the Radeon 780M, smaller RDNA2 cards). Only when all such chips agree on one."""
    try:
        import gpus
        archs = {g.arch for g in gpus.amd() if g.arch}
    except Exception:
        return ""
    import gpus
    need = {gpus.ROCM_OVERRIDE[a] for a in archs if a in gpus.ROCM_OVERRIDE
            and a not in gpus.ROCM_ARCHS}
    native = {a for a in archs if a in gpus.ROCM_ARCHS}
    if len(need) == 1:
        ver = need.pop()
        # a native card of another family would be broken by the override
        fam = ver.split(".")[0]
        if all(a.startswith(f"gfx{fam}") for a in native):
            return ver
    return ""


class WorkerError(RuntimeError):
    pass


class Worker:
    def __init__(self, backend: str, log: Callable[[str], None]):
        self.backend = backend
        self.log = log
        exe = sys.executable
        if paths.IS_WINDOWS and Path(exe).name.lower() == "pythonw.exe":
            cand = Path(exe).with_name("python.exe")
            exe = str(cand) if cand.exists() else exe
        self.proc = subprocess.Popen(
            [exe, str(HERE / "model_worker.py"), backend], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=worker_env(backend),
            **paths.hidden_subprocess_kwargs())
        self.lock = threading.Lock()
        self.next_id = 0
        threading.Thread(target=self._read_log, daemon=True, name=f"worker-{backend}").start()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _read_log(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.log(f"[AMD] {line}" if self.backend == "rocm" else line)

    def request(self, op: str, *payload):
        with self.lock:
            if not self.alive():
                raise WorkerError("the AMD helper process has stopped (see the log)")
            self.next_id += 1
            data = pickle.dumps((self.next_id, op, payload), protocol=pickle.HIGHEST_PROTOCOL)
            try:
                self.proc.stdin.write(struct.pack("<I", len(data)) + data)
                self.proc.stdin.flush()
                head = self.proc.stdout.read(4)
                if len(head) < 4:
                    raise WorkerError("the AMD helper process stopped (see the log)")
                (n,) = struct.unpack("<I", head)
                body = self.proc.stdout.read(n)
            except (OSError, ValueError) as e:
                raise WorkerError(f"the AMD helper process stopped ({e})")
        _rid, ok, value = pickle.loads(body)
        if not ok:
            raise WorkerError(value)
        return value

    def load(self, kind: str, spec: str, *args) -> "Proxy":
        obj_id, attrs = self.request("load", kind, spec, args)
        return Proxy(self, obj_id, attrs)

    def stop(self):
        try:
            if self.alive():
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class Proxy:
    """Stands in for a model that lives in the helper process."""

    def __init__(self, w: Worker, obj_id: int, attrs: dict):
        object.__setattr__(self, "_w", w)
        object.__setattr__(self, "_id", obj_id)
        for k, v in attrs.items():
            object.__setattr__(self, k, v)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        w, obj_id = self._w, self._id

        def call(*args, **kwargs):
            return w.request("call", obj_id, name, args, kwargs)
        return call

    def __del__(self):
        try:
            if self._w.alive():
                threading.Thread(target=self._w.request, args=("free", self._id),
                                 daemon=True).start()
        except Exception:
            pass
