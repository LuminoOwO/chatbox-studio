"""
Text translation with Meta's NLLB-200 running on CTranslate2 (same runtime as Whisper).

Used when the chatbox language isn't English (Whisper can only translate *to* English),
or when "Use NLLB for English too" is enabled. The app downloads a ready-converted
int8 copy of the model into the models folder (see paths.py). On Linux,
setup_translator.sh can build the same files from Meta's original model instead.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

import paths
from languages import NLLB

MODELS = {
    "nllb-200-distilled-600M": "NLLB 600M (fast)",
    "nllb-200-distilled-1.3B": "NLLB 1.3B (better)",
}
# int8 CTranslate2 conversions of facebook/nllb-200-* (made with ct2-transformers-converter)
REPOS = {
    "nllb-200-distilled-600M": ("JustFrederik/nllb-200-distilled-600M-ct2-int8", 650),
    "nllb-200-distilled-1.3B": ("JustFrederik/nllb-200-distilled-1.3B-ct2-int8", 1400),
}
NEEDED_FILES = ["model.bin", "config.json", "shared_vocabulary.txt", "sentencepiece.bpe.model"]


def models_dir() -> Path:
    return paths.models_dir()


def model_path(name: str) -> Path:
    return models_dir() / name


def is_installed(name: str) -> bool:
    p = model_path(name)
    return (p / "model.bin").exists() and (p / "sentencepiece.bpe.model").exists()


def _dir_size_mb(path: Path) -> float:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / 1e6


def download(name: str, progress: Callable[[int], None]) -> Path:
    """Downloads the converted model. Calls progress(percent) about twice a second."""
    import threading
    from huggingface_hub import snapshot_download

    repo, size_mb = REPOS[name]
    dest = model_path(name)
    dest.mkdir(parents=True, exist_ok=True)
    done = threading.Event()

    def watch():
        while not done.wait(0.5):
            progress(min(99, int(_dir_size_mb(dest) / size_mb * 100)))
    threading.Thread(target=watch, daemon=True).start()
    try:
        snapshot_download(repo, local_dir=str(dest), allow_patterns=NEEDED_FILES)
    finally:
        done.set()
    if not is_installed(name):
        raise RuntimeError(f"download of {repo} is incomplete")
    progress(100)
    return dest


class TextTranslator:
    def __init__(self, name: str, spec: str, log: Callable[[str], None]):
        import ctranslate2
        import sentencepiece as spm

        path = model_path(name)
        if not is_installed(name):
            raise FileNotFoundError(f"{name} is not installed")
        t0 = time.perf_counter()
        import devices
        dev, gpu_index = devices.ct2(spec)
        self.spec = spec
        if dev == "cuda":
            from engine import pick_compute_type
            ct = pick_compute_type(spec, "auto")
            self.model = ctranslate2.Translator(str(path), device="cuda",
                                                device_index=gpu_index, compute_type=ct)
            where = f"{devices.label(spec)} ({ct})"
        else:
            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            self.model = ctranslate2.Translator(str(path), device="cpu", compute_type="int8",
                                                intra_threads=threads)
            where = f"CPU ({threads} threads)"
        self.sp = spm.SentencePieceProcessor(model_file=str(path / "sentencepiece.bpe.model"))
        self.name = name
        self.translate("Hello!", "en", "en")          # warm-up
        log(f"Translator {name} ready on {where} in {time.perf_counter() - t0:.1f} s.")

    def translate(self, text: str, src: str, dst: str, beam_size: int = 2) -> str:
        if src == dst or not text.strip():
            return text
        src_code, dst_code = NLLB[src], NLLB[dst]
        tokens = [src_code] + self.sp.encode(text, out_type=str) + ["</s>"]
        res = self.model.translate_batch(
            [tokens], target_prefix=[[dst_code]], beam_size=beam_size,
            max_decoding_length=256, repetition_penalty=1.1,
        )[0]
        out = [t for t in res.hypotheses[0] if t not in (dst_code, "</s>")]
        return self.sp.decode(out).strip()
