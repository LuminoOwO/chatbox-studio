"""
Earshot smart translation: a local instruct model (Qwen2.5, CTranslate2 int8) translates
your sentence with the recent conversation in mind - names, what "it" refers to, slang,
how formal people are talking. Runs on the same runtime as Whisper and NLLB.

The model is optional and downloaded once (Earshot tab -> Smart translation -> Install).
Any CTranslate2 chat model folder with a tokenizer.json in the ChatML format (Qwen) works
via "Custom folder".
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import languages
import paths

MODELS = {
    "qwen2.5-3b": ("jncraton/Qwen2.5-3B-Instruct-ct2-int8", 3100,
                   "Qwen2.5 3B (good, ~3.1 GB)"),
    "qwen2.5-7b": ("jncraton/Qwen2.5-7B-Instruct-ct2-int8", 7650,
                   "Qwen2.5 7B (best, ~7.7 GB, needs an 8 GB+ card or a fast CPU)"),
}
TOKENIZER_REPO = "Qwen/Qwen2.5-3B-Instruct"          # same tokenizer for all Qwen2.5 sizes
PATTERNS = ["model.bin", "config.json", "vocabulary.json", "vocabulary.txt", "tokenizer.json",
            "tokenizer_config.json", "generation_config.json", "special_tokens_map.json"]


def model_dir(name: str) -> Path:
    return paths.models_dir() / f"smart-{name}"


def folder(cfg: dict) -> Path:
    custom = str(cfg.get("smart_path") or "").strip()
    return Path(custom) if custom else model_dir(cfg.get("smart_model", "qwen2.5-3b"))


def model_key(cfg: dict, spec: str = "") -> str:
    return f"{folder(cfg)}|{spec or cfg.get('smart_device', 'same')}"


def is_installed(cfg: dict) -> bool:
    p = folder(cfg)
    return (p / "model.bin").exists() and (p / "tokenizer.json").exists()


def _dir_mb(p: Path) -> float:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / 1e6


def download(name: str, progress: Callable[[int], None], log: Callable[[str], None]) -> Path:
    from huggingface_hub import hf_hub_download, snapshot_download
    repo, size_mb, _label = MODELS[name]
    dest = model_dir(name)
    dest.mkdir(parents=True, exist_ok=True)
    log(f"Downloading smart translation model (~{size_mb} MB) from huggingface.co/{repo} ...")
    done = threading.Event()

    def watch():
        while not done.wait(0.5):
            progress(min(99, int(_dir_mb(dest) / size_mb * 100)))
    threading.Thread(target=watch, daemon=True).start()
    try:
        try:
            snapshot_download(repo, local_dir=str(dest), allow_patterns=PATTERNS)
        except Exception as e:                 # huggingface_hub's errors are pages long
            name_ = type(e).__name__
            if "NotFound" in name_ or "401" in str(e):
                raise RuntimeError(f"huggingface.co/{repo} isn't available (removed or "
                                   "private) - update Chatbox Studio") from None
            raise RuntimeError(f"{name_}: {str(e).splitlines()[0][:200]}") from None
        if not (dest / "tokenizer.json").exists():
            hf_hub_download(TOKENIZER_REPO, "tokenizer.json", local_dir=str(dest))
    finally:
        done.set()
    if not (dest / "model.bin").exists():
        raise RuntimeError(f"download of {repo} is incomplete")
    progress(100)
    log("Smart translation model installed.")
    return dest


SYSTEM = (
    "You translate chat messages for a player in the social VR game VRChat. "
    "Translate the player's newest message into {dst}. Use the conversation only to "
    "understand it: names, what words like 'it' or 'that' refer to, slang and how casual "
    "people are talking. Keep names as they are. Keep it short and natural like spoken "
    "chat. Do not answer the message, do not explain, do not add anything. "
    "Reply with the translation only."
)


def build_messages(text: str, src: str, dst: str, history) -> list[tuple[str, str]]:
    name = languages.NAME.get
    conv = []
    for ln in history:
        who = "Player" if ln.who == "you" else "Someone nearby"
        conv.append(f"{who} ({name(ln.lang, ln.lang)}): {ln.text.strip()}")
    user = ""
    if conv:
        user += "Conversation so far:\n" + "\n".join(conv) + "\n\n"
    user += (f"Translate the player's new message from {name(src, src)} to "
             f"{name(dst, dst)}:\n{text.strip()}")
    return [("system", SYSTEM.format(dst=name(dst, dst))), ("user", user)]


def chatml(messages) -> str:
    out = ""
    for role, content in messages:
        out += f"<|im_start|>{role}\n{content}<|im_end|>\n"
    return out + "<|im_start|>assistant\n"


def clean(out: str, src_text: str) -> str:
    t = out.strip()
    t = re.sub(r"^translation\s*:\s*", "", t, flags=re.I)
    if len(t) >= 2 and t[0] in "\"“'«" and t[-1] in "\"”'»":
        t = t[1:-1].strip()
    t = " ".join(x.strip() for x in t.splitlines() if x.strip())
    # a model that starts chatting instead of translating: give up, use NLLB
    if not t or len(t) > max(60, len(src_text) * 4):
        return ""
    return t


class SmartTranslator:
    def __init__(self, cfg: dict, spec: str, log: Callable[[str], None]):
        import ctranslate2
        from tokenizers import Tokenizer
        path = folder(cfg)
        if not is_installed(cfg):
            raise FileNotFoundError(f"no model in {path}")
        import devices
        self.key = model_key(cfg, spec)
        t0 = time.perf_counter()
        dev, gpu_index = devices.ct2(spec)
        if dev == "cuda":
            from engine import pick_compute_type
            ct = pick_compute_type(spec, "auto")
            self.gen = ctranslate2.Generator(str(path), device="cuda", device_index=gpu_index,
                                             compute_type=ct)
            where = f"{devices.label(spec)} ({ct})"
        else:
            threads = max(2, min(8, (os.cpu_count() or 4) // 2))
            self.gen = ctranslate2.Generator(str(path), device="cpu", compute_type="int8",
                                             intra_threads=threads)
            where = f"CPU ({threads} threads)"
        self.tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        self.end_ids = [i for i in (self.tok.token_to_id("<|im_end|>"),
                                    self.tok.token_to_id("<|endoftext|>")) if i is not None]
        self._lock = threading.Lock()
        self.translate("Hello, how are you?", "en", "en", [])        # warm-up
        log(f"Smart translation ready on {where} in {time.perf_counter() - t0:.1f} s.")

    def translate(self, text: str, src: str, dst: str, history) -> str:
        prompt = chatml(build_messages(text, src, dst, history))
        tokens = self.tok.encode(prompt, add_special_tokens=False).tokens
        with self._lock:
            res = self.gen.generate_batch(
                [tokens], max_length=min(200, 24 + len(text) * 2), sampling_topk=1,
                beam_size=1, include_prompt_in_result=False,
                end_token=["<|im_end|>", "<|endoftext|>"], repetition_penalty=1.05)[0]
        ids = [i for i in res.sequences_ids[0] if i not in self.end_ids]
        return clean(self.tok.decode(ids, skip_special_tokens=True), text)
