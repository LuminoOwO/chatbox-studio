"""
Where each AI model runs - per model, mixed vendors allowed.

A device is written as a short string:
    "cpu"        the processor
    "nvidia:1"   NVIDIA card 1 (nvidia-smi numbering), CUDA - runs inside the app
    "amd:0"      AMD card 0, ROCm - runs in a helper process (see workers.py), because
                 one Python process can only load one build of CTranslate2
    "vulkan:2"   Vulkan device 2 (whisper.cpp, see whispercpp.py): any other graphics chip -
                 AMD without ROCm, APUs, Intel. Speech recognition only; the translator
                 and the smart AI then run on the CPU
    "same"       (translator, smart AI, Earshot) the device of the speech model
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import paths


def rocm_engine_dir() -> Path:
    """Where the AMD (ROCm) build of CTranslate2 is installed (pip --target)."""
    return paths.data_dir() / "engines" / "rocm"


def rocm_installed() -> bool:
    return (rocm_engine_dir() / "ctranslate2" / "__init__.py").exists()


def vulkan_installed() -> bool:
    import whispercpp
    return whispercpp.installed()


def supports(spec: str, kind: str) -> bool:
    """Can a model of this kind ("whisper", "nllb", "smart") run on the device?"""
    return parse(spec)[0] != "vulkan" or kind == "whisper"


def parse(spec: str) -> tuple[str, int]:
    spec = (spec or "cpu").strip().lower()
    if ":" in spec:
        vendor, _, idx = spec.partition(":")
        try:
            return vendor, int(idx)
        except ValueError:
            return "cpu", -1
    return "cpu", -1


def speech_spec(s) -> str:
    """The speech model's device, also for settings from before 1.24 (only gpu_index)."""
    spec = str(getattr(s, "speech_device", "") or "")
    if spec:
        return spec
    gi = int(getattr(s, "gpu_index", -1))
    if gi < 0:
        return "cpu"
    try:
        import gpus
        if gpus.backend() == "rocm":
            return f"amd:{gi}"
    except Exception:
        pass
    return f"nvidia:{gi}"


def resolve(spec: Optional[str], s) -> str:
    """'same' / old values -> a real device string."""
    spec = (spec or "same").strip().lower()
    if spec in ("same", "cuda", "gpu"):            # "cuda" = setting from before 1.24
        return speech_spec(s)
    return spec


def ct2(spec: str) -> tuple[str, int]:
    """(CTranslate2 device, index). AMD cards are 'cuda' too inside the ROCm build."""
    vendor, idx = parse(spec)
    if vendor in ("nvidia", "amd") and idx >= 0:
        return "cuda", idx
    return "cpu", 0


def is_remote(spec: str) -> bool:
    return parse(spec)[0] == "amd"


def label(spec: str) -> str:
    vendor, idx = parse(spec)
    if vendor == "cpu":
        return "CPU"
    for g in available_gpus():
        if g.vendor == vendor and g.index == idx:
            return g.name + (" (Vulkan)" if vendor == "vulkan" else "")
    return f"{vendor.upper()} GPU {idx}"


def available_gpus() -> list:
    """Cards the installed engines can use: NVIDIA always (CUDA libraries are optional, the
    model then fails to load with a clear message), AMD when the ROCm engine is installed,
    every other chip (and AMD cards ROCm doesn't take) when the Vulkan engine is."""
    import gpus
    out = []
    try:
        out += gpus.nvidia()
    except Exception:
        pass
    try:
        if rocm_installed() or gpus.backend() == "rocm":
            out += gpus.amd()
    except Exception:
        pass
    try:
        if vulkan_installed():
            out += vulkan_extra(out)
    except Exception:
        pass
    return out


def vulkan_extra(have: list) -> list:
    """Vulkan devices not already usable through CUDA (all NVIDIA cards are)."""
    import gpus
    return [g for g in gpus.vulkan() if g.maker != "nvidia"]


def gpu_label(g) -> str:
    short = g.name.replace("NVIDIA GeForce ", "").replace("AMD Radeon ", "Radeon ")
    mem = f"{round(g.vram_mb / 1024)} GB" + (" shared" if getattr(g, "integrated", False) else "")
    return f"{short} ({mem}" + (", Vulkan)" if g.vendor == "vulkan" else ")")


def choices(include_same: bool = False, same_label: str = "Same as speech",
            kind: str = "whisper") -> list:
    """[(spec, label)] for a device combo box. kind: which model it is for - the translator
    and the smart AI can't run on Vulkan devices, so those aren't offered for them."""
    out = [("same", same_label)] if include_same else []
    for g in available_gpus():
        spec = f"{g.vendor}:{g.index}"
        if supports(spec, kind):
            out.append((spec, gpu_label(g)))
    out.append(("cpu", "CPU (slow)"))
    return out
