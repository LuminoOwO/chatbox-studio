"""
The NLLB translator on ONNX Runtime - with DirectML on Windows, so it runs on any
graphics card with a DirectX 12 driver (AMD, Intel, NVIDIA), not only the ones CUDA or
ROCm support.

Runs in a helper process (model_worker.py directml) with the DirectML build of ONNX
Runtime first on its path: the app's own onnxruntime (voice detection) is another build.

The model: Meta's NLLB-200 exported to ONNX by tools/nllb_onnx_export.py (in
.github/workflows/nllb-onnx.yml): an encoder that also computes the decoder's
cross-attention keys / values, and a decoder that always takes its key/value cache (fp16
weights, float32 inputs/outputs). Published as release files of this repository. The tokenizer and vocabulary are the ones of the
CTranslate2 model next to it (translator.py), so both give the same tokens.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np

import paths
from languages import NLLB

RELEASE = "https://github.com/LuminoOwO/chatbox-studio/releases/download/models-nllb-onnx-1/"
# model -> [(file, size in bytes)] - the size is checked after the download
FILES = {
    "nllb-200-distilled-600M": [("encoder_model.onnx", 879704198),
                                ("decoder_model_merged.onnx", 879857101),
                                ("config.json", 248)],
    "nllb-200-distilled-1.3B": [("encoder_model.onnx", 1635464400),
                                ("decoder_model_merged.onnx", 1635741659),
                                ("config.json", 248)],
}
# graphics memory (MB) while translating: the fp16 weights plus working memory
VRAM = {"nllb-200-distilled-600M": 2100, "nllb-200-distilled-1.3B": 3700}


def onnx_dir(name: str) -> Path:
    return paths.models_dir() / name / "onnx"


def size_mb(name: str) -> int:
    return round(sum(n for _f, n in FILES.get(name, [])) / 1e6)


def is_installed(name: str) -> bool:
    d = onnx_dir(name)
    try:
        return all((d / f).stat().st_size == n for f, n in FILES.get(name, [("x", -1)]))
    except OSError:
        return False


def download(name: str, progress: Callable[[int], None] = lambda pct: None) -> Path:
    """Downloads the ONNX files (resumes a broken download)."""
    d = onnx_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    total = max(1, sum(n for _f, n in FILES[name]))
    done = 0
    for fname, nbytes in FILES[name]:
        dest = d / fname
        if dest.exists() and dest.stat().st_size == nbytes:
            done += nbytes
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(f"{RELEASE}{name}-{fname}",
                                     headers={"User-Agent": "ChatboxStudio"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        with urllib.request.urlopen(req, timeout=60) as r:
            if have and r.status != 206:           # server ignored the range: start over
                have = 0
            with open(part, "ab" if have else "wb") as f:
                got = have
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    progress(min(99, int((done + got) * 100 / total)))
        if part.stat().st_size != nbytes:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{fname} downloaded incompletely - run the setup again")
        os.replace(part, dest)
        done += nbytes
    progress(100)
    return d


def remove(name: str):
    import shutil
    shutil.rmtree(onnx_dir(name), ignore_errors=True)


def _log_softmax(x: np.ndarray) -> np.ndarray:
    m = x.max(axis=-1, keepdims=True)
    return x - m - np.log(np.exp(x - m).sum(axis=-1, keepdims=True))


class OnnxTranslator:
    """Same use as translator.TextTranslator: translate(text, src, dst)."""

    def __init__(self, name: str, spec: str, log: Callable[[str], None],
                 providers: list | None = None):
        import onnxruntime as ort
        import sentencepiece as spm
        import translator

        ct2_dir = translator.model_path(name)
        d = onnx_dir(name)
        if not is_installed(name):
            raise FileNotFoundError(f"the DirectML version of {name} isn't downloaded")
        t0 = time.perf_counter()
        self.name, self.spec = name, spec
        cfg = json.loads((d / "config.json").read_text())
        self.layers = int(cfg["decoder_layers"])
        self.heads = int(cfg["decoder_attention_heads"])
        self.head_dim = int(cfg["d_model"]) // self.heads
        self.eos = int(cfg.get("eos_token_id", 2))
        self.start = int(cfg.get("decoder_start_token_id", self.eos))

        vocab = (ct2_dir / "shared_vocabulary.txt").read_text(encoding="utf-8").split("\n")
        if vocab and vocab[-1] == "":
            vocab.pop()
        self.vocab = vocab
        self.ids = {t: i for i, t in enumerate(vocab)}
        self.unk = self.ids.get("<unk>", 3)
        self.sp = spm.SentencePieceProcessor(model_file=str(ct2_dir / "sentencepiece.bpe.model"))

        so = ort.SessionOptions()
        so.log_severity_level = 3
        where = "CPU"
        if providers is None:
            import devices
            dev = devices.dml_index(spec)
            if dev < 0:
                raise RuntimeError(f"DirectML can't find {devices.label(spec)}")
            so.enable_mem_pattern = False                 # required by DirectML
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            providers = [("DmlExecutionProvider", {"device_id": dev}), "CPUExecutionProvider"]
            where = f"{devices.label(spec, 'nllb')} (DirectML device {dev})"
        self.enc = ort.InferenceSession(str(d / "encoder_model.onnx"), so, providers=providers)
        self.dec = ort.InferenceSession(str(d / "decoder_model_merged.onnx"), so,
                                        providers=providers)
        used = self.dec.get_providers()
        if providers and not isinstance(providers[0], str) and used[0] != "DmlExecutionProvider":
            raise RuntimeError(f"DirectML didn't start (ONNX Runtime uses {used[0]})")
        self.enc_outputs = [o.name for o in self.enc.get_outputs()]
        self.dec_outputs = [o.name for o in self.dec.get_outputs()]
        self.translate("Hello!", "en", "de")             # warm-up (also checks it works)
        log(f"Translator {name} ready on {where} in {time.perf_counter() - t0:.1f} s.")

    # ---- tokens
    def _encode(self, text: str, src: str) -> list[int]:
        pieces = [NLLB[src]] + self.sp.encode(text, out_type=str) + ["</s>"]
        return [self.ids.get(p, self.unk) for p in pieces]

    def _decode(self, ids: list[int], dst: str) -> str:
        skip = {self.eos, self.start, self.ids.get(NLLB[dst], -1)}
        return self.sp.decode([self.vocab[i] for i in ids if i not in skip]).strip()

    # ---- decoder steps (format 2: see tools/nllb_onnx_export.py)
    def _step(self, input_ids, enc_mask, past: dict, cross: dict):
        feed = {"input_ids": input_ids, "encoder_attention_mask": enc_mask, **past, **cross}
        outs = self.dec.run(None, feed)
        res = dict(zip(self.dec_outputs, outs))
        new_past = {f"past.{i}.{kv}": res[f"present.{i}.{kv}"]
                    for i in range(self.layers) for kv in ("key", "value")}
        return res["logits"].astype(np.float32), new_past

    def translate(self, text: str, src: str, dst: str, beam_size: int = 2,
                  max_len: int = 256, repetition_penalty: float = 1.1) -> str:
        if src == dst or not text.strip():
            return text
        ids = np.array([self._encode(text, src)], dtype=np.int64)
        mask = np.ones_like(ids)
        cross1 = dict(zip(self.enc_outputs,
                          self.enc.run(None, {"input_ids": ids, "attention_mask": mask})))
        prefix = [self.start, self.ids[NLLB[dst]]]
        empty = np.zeros((1, self.heads, 0, self.head_dim), dtype=np.float32)
        past = {f"past.{i}.{kv}": empty for i in range(self.layers) for kv in ("key", "value")}

        # beam search like CTranslate2: score = sum of log-probs / length
        logits, past = self._step(np.array([prefix], dtype=np.int64), mask, past, cross1)
        beams = [(list(prefix), 0.0)]
        finished: list[tuple[float, list[int]]] = []
        cross_n = {1: cross1}
        for _step_no in range(max_len):
            logits = logits.copy()
            for b, (toks, _s) in enumerate(beams):
                seen = np.array(sorted(set(toks)), dtype=np.int64)
                row = logits[b, seen]
                logits[b, seen] = np.where(row < 0, row * repetition_penalty,
                                           row / repetition_penalty)
            logp = _log_softmax(logits)
            cand = []
            k = beam_size * 2
            for b, (toks, score) in enumerate(beams):
                top = np.argpartition(-logp[b], k)[:k]
                for t in top:
                    cand.append((score + float(logp[b, t]), b, int(t)))
            cand.sort(key=lambda c: -c[0])
            # like CTranslate2: an end among the best `beam_size` candidates finishes that
            # hypothesis; the search stops once the best candidate itself is an end
            nxt = []
            for rank, (score, b, t) in enumerate(cand):
                toks = beams[b][0] + [t]
                if t == self.eos:
                    if rank < beam_size:
                        finished.append((score / (len(toks) - len(prefix)), toks))
                    continue
                nxt.append((toks, score, b))
                if len(nxt) == beam_size:
                    break
            if cand[0][2] == self.eos or not nxt:
                break
            order = np.array([b for _t, _s, b in nxt], dtype=np.int64)
            past = {k2: v.take(order, axis=0) for k2, v in past.items()}
            beams = [(t, s) for t, s, _b in nxt]
            n = len(beams)
            if n not in cross_n:
                cross_n[n] = {k2: np.repeat(v, n, axis=0) for k2, v in cross1.items()}
            last = np.array([[t[-1]] for t, _s in beams], dtype=np.int64)
            logits, past = self._step(last, np.repeat(mask, n, axis=0), past, cross_n[n])
        if finished:
            best = max(finished, key=lambda f: f[0])[1]
        else:
            best = beams[0][0]
        return self._decode(best[len(prefix):], dst)
