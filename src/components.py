"""
Optional parts of Chatbox Studio, the hardware check, and the install / remove steps.
Used by the setup window (setup_gui.py) and its command-line mode (--defaults).

What is installed is remembered in <data dir>/components.json, so running the setup
again (update, "Add or remove parts") starts from your current choice.

Graphics cards: NVIDIA runs on CUDA (pip packages), AMD on ROCm: on Windows AMD's
ROCm SDK pip packages, on Linux the system ROCm (apt), plus the ROCm build of
CTranslate2 from its GitHub release in both cases.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import gpus as G
import paths

HERE = Path(__file__).resolve().parent
REQ = HERE / "req"
STATE = paths.data_dir() / "components.json"
CONFIG = paths.config_dir() / "settings.json"
IS_WINDOWS = paths.IS_WINDOWS

GPU = G.GPU


# --------------------------------------------------------------------------- #
# Hardware
# --------------------------------------------------------------------------- #
@dataclass
class Hardware:
    gpus: list = field(default_factory=list)          # cards the AI can use (NVIDIA + AMD)
    other_gpus: list = field(default_factory=list)    # names of cards it can't use
    ram_gb: float = 0.0
    cores: int = 0
    free_gb: float = 0.0
    os_name: str = ""


def _run(cmd, timeout=8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              **paths.hidden_subprocess_kwargs()).stdout
    except Exception:
        return ""


def detect() -> Hardware:
    hw = Hardware()
    try:
        hw.gpus = G.nvidia() + G.amd()
    except Exception:
        pass
    try:            # every other chip (and AMD again, for Vulkan): AMD, Intel, APUs
        hw.gpus += [g for g in G.vulkan() if g.maker != "nvidia"]
    except Exception:
        pass
    try:
        if IS_WINDOWS:
            out = _run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_VideoController | ForEach-Object {$_.Name}"], 15)
            names = [x.strip() for x in out.splitlines() if x.strip()]
        else:
            out = _run(["lspci", "-mm"])
            names = []
            for line in out.splitlines():
                cols = re.findall(r'"([^"]*)"', line)
                if len(cols) >= 3 and re.search(r"VGA|3D|Display", cols[0]):
                    names.append(f"{cols[1].split()[0]} {cols[2]}")
        usable = " ".join(g.name.lower() for g in hw.gpus)
        makers = {g.maker or g.vendor for g in hw.gpus}

        def covered(n: str) -> bool:
            low = n.lower()
            return ("nvidia" in low or n.lower() in usable
                    or (("amd" in low or "radeon" in low or "ati" in low) and "amd" in makers)
                    or ("intel" in low and "intel" in makers))
        hw.other_gpus = [n for n in names if not covered(n)]
    except Exception:
        pass
    try:
        import psutil
        hw.ram_gb = psutil.virtual_memory().total / 2**30
        hw.cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 0
    except Exception:
        hw.cores = os.cpu_count() or 0
    try:
        target = paths.data_dir()
        target.mkdir(parents=True, exist_ok=True)
        hw.free_gb = shutil.disk_usage(target).free / 1e9
    except Exception:
        pass
    import platform
    hw.os_name = f"{platform.system()} {platform.release()}"
    return hw


def gpu_key(g: GPU) -> str:
    return f"{g.vendor}:{g.index}"


def find_gpu(hw: Hardware, key: str) -> Optional[GPU]:
    return next((g for g in hw.gpus if gpu_key(g) == key), None)


def weak_igpu(g: GPU) -> bool:
    """Older Intel laptop graphics (HD / UHD 6xx) are hardly faster than the CPU."""
    return g.integrated and g.maker == "intel" and not re.search(r"arc|xe", g.name, re.I)


def default_gpu(hw: Hardware) -> Optional[GPU]:
    """The device the setup suggests for speech recognition:
    NVIDIA (CUDA) > AMD card (ROCm on Windows, Vulkan on Linux - needs no extra system
    packages there) > other discrete chips via Vulkan (Intel Arc) > AMD APU / Intel Xe
    graphics via Vulkan > CPU. With two cards of one kind, the smaller one keeps the main
    card free for VRChat."""
    def pick(pool):
        if not pool:
            return None
        return min(pool, key=lambda g: g.vram_mb) if len(pool) > 1 else pool[0]
    big = [g for g in hw.gpus if g.vram_mb >= 1500]
    nv = [g for g in big if g.vendor == "nvidia"]
    rocm = [g for g in big if g.vendor == "amd" and not g.integrated]
    vk = [g for g in big if g.vendor == "vulkan" and not g.integrated]
    vk_amd = [g for g in vk if g.maker == "amd"]
    igpu = [g for g in big if g.integrated and not weak_igpu(g)
            and (g.vendor == "vulkan" or IS_WINDOWS)]
    order = [nv] + ([rocm, vk_amd] if IS_WINDOWS else [vk_amd, rocm]) + [vk, igpu]
    for pool in order:
        g = pick(pool)
        if g is not None:
            return g
    return None


# --------------------------------------------------------------------------- #
# What can be installed
# --------------------------------------------------------------------------- #
WHISPER = [   # (name, label, download MB, graphics memory MB with int8)
    ("small", "small - fastest, rough", 480, 700),
    ("medium", "medium - good, faster", 1500, 1300),
    ("large-v2", "large-v2 - very good, sometimes better translations", 3100, 2200),
    ("large-v3", "large-v3 - best quality", 3100, 2200),
]
WHISPER_REPO = {"small": "Systran/faster-whisper-small", "medium": "Systran/faster-whisper-medium",
                "large-v2": "Systran/faster-whisper-large-v2",
                "large-v3": "Systran/faster-whisper-large-v3"}
NLLB = [("nllb-200-distilled-600M", "NLLB 600M - fast", 650, 800),
        ("nllb-200-distilled-1.3B", "NLLB 1.3B - better", 1400, 1600)]
SMART = [("qwen2.5-3b", "Qwen2.5 3B - good", 3100, 3400),
         ("qwen2.5-7b", "Qwen2.5 7B - best", 7650, 7800)]

def ggml_vram(name: str) -> int:
    import whispercpp
    return whispercpp.MODELS.get(name, (None, 0, 1000))[2]


def ggml_size(name: str) -> int:
    import whispercpp
    return whispercpp.MODELS.get(name, (None, 600, 0))[1]


# AMD (ROCm). The CTranslate2 ROCm wheels are built against ROCm 7.2.1.
CT2_ROCM_VERSION = "4.8.4"
CT2_ROCM_URL = ("https://github.com/OpenNMT/CTranslate2/releases/download/v{v}/"
                "rocm-python-wheels-{os}.zip")
ROCM_WIN = "https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/"
ROCM_WIN_WHEELS = ["rocm_sdk_core-7.2.1-py3-none-win_amd64.whl",
                   "rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl"]


@dataclass
class Group:
    key: str
    label: str
    desc: str
    size_mb: int                       # download
    disk_mb: int                       # installed
    req: Optional[str] = None          # requirements file in req/
    dists: tuple = ()                  # pip names to remove when unticked
    required: bool = False


GROUPS = [
    Group("core", "Chatbox Studio and speech recognition",
          "The app, the Whisper speech engine, microphone and VRChat OSC.", 140, 450,
          "core.txt", required=True),
    Group("cuda", "NVIDIA graphics card support (CUDA)",
          "Runs the AI on your NVIDIA card - many times faster than the CPU.", 1200, 2700,
          "cuda.txt", ("nvidia-cublas-cu12", "nvidia-cudnn-cu12")),
    Group("rocm", "AMD graphics card support (ROCm)",
          "Runs the AI on your Radeon card (RX 6800 and newer, RX 7000, RX 9000). "
          + ("Needs the AMD Adrenalin driver 26.2.2 or newer."
             if IS_WINDOWS else "Needs the ROCm 7.2 system libraries - the setup helps "
                                "you install them (one command, asks for your password)."),
          2100 if IS_WINDOWS else 60, 4200 if IS_WINDOWS else 160, None,
          ("rocm-sdk-core", "rocm-sdk-libraries-custom") if IS_WINDOWS else ()),
    Group("vulkan", "Graphics support for any GPU (Vulkan)",
          "Runs speech recognition on AMD and Intel graphics - also the graphics inside your "
          "processor (APU, laptop) and Radeon cards ROCm doesn't support. Uses your normal "
          "graphics driver, nothing else to install. The translator then runs on the CPU.",
          40, 90, None),
    Group("media", "Now playing, program audio and active app",
          "Song and artist from Spotify, YouTube and players, which program plays sound, "
          "audio devices.", 8, 15, "media.txt",
          ("jeepney", "pulsectl", "python-xlib") if not IS_WINDOWS else
          ("winrt-Windows.Media.Control", "pycaw", "comtypes")),
    Group("vr", "VR headset values (SteamVR)",
          "Headset, controller and tracker batteries and more, read from SteamVR.", 3, 5,
          "vr.txt", ("openvr",)),
    Group("earshot", "Earshot: listen to the game",
          "Conversation-aware mode: hears what the people around you say to translate you "
          "better.", 2 if IS_WINDOWS else 0, 3 if IS_WINDOWS else 0, "earshot.txt",
          ("soundcard",) if IS_WINDOWS else ()),
]
GROUP = {g.key: g for g in GROUPS}


@dataclass
class Choice:
    groups: set = field(default_factory=lambda: {"core"})
    whisper: str = "large-v3"
    gpu: str = "cpu"                   # "nvidia:1", "amd:0" or "cpu"
    nllb: Optional[str] = None         # model name or None
    smart: Optional[str] = None
    # 1.24: every model can run on its own card, NVIDIA and AMD mixed ("same" = speech's)
    tr_dev: str = "same"
    smart_dev: str = "same"
    listen_dev: str = "same"           # Earshot's own speech model ("same" = shares yours)
    listen_model: str = "small"

    @property
    def vendor(self) -> str:
        return self.gpu.split(":")[0] if ":" in self.gpu else "cpu"

    def spec(self, role: str) -> str:
        """The device a role really runs on (unticked vendor support -> CPU; the translator
        and the smart AI on a Vulkan device -> CPU, they can't use Vulkan)."""
        raw = {"speech": self.gpu, "translator": self.tr_dev, "smart": self.smart_dev,
               "listen": self.listen_dev}[role] or "same"
        if raw == "same":
            raw = self.gpu
        vendor = raw.split(":")[0] if ":" in raw else "cpu"
        if vendor != "cpu" and _accel_group(vendor) not in self.groups:
            return "cpu"
        if vendor == "vulkan" and role in ("translator", "smart"):
            return "cpu"
        return raw

    def roles(self) -> list[tuple[str, str, str, int]]:
        """[(role, label, device, VRAM MB)] for every AI model this choice runs."""
        sp = self.spec("speech")
        out = [("speech", f"speech ({self.whisper})", sp,
                ggml_vram(self.whisper) if sp.startswith("vulkan:")
                else _vram(WHISPER, self.whisper, 2200))]
        if self.nllb:
            out.append(("translator", "translator", self.spec("translator"),
                        _vram(NLLB, self.nllb, 1600)))
        if self.smart:
            out.append(("smart", "smart AI", self.spec("smart"), _vram(SMART, self.smart, 3400)))
        if "earshot" in self.groups and self.listen_dev not in ("same", ""):
            ls = self.spec("listen")
            out.append(("listen", f"Earshot listening ({self.listen_model})", ls,
                        ggml_vram(self.listen_model) if ls.startswith("vulkan:")
                        else _vram(WHISPER, self.listen_model, 700)))
        return out

    def vendors(self) -> set:
        """Graphics card makers the chosen devices use (before checking ticked support)."""
        out = set()
        for raw, ct2 in ((self.gpu, False), (self.tr_dev if self.nllb else "", True),
                         (self.smart_dev if self.smart else "", True),
                         (self.listen_dev if "earshot" in self.groups else "", False)):
            raw = self.gpu if raw == "same" else raw
            if raw and ":" in raw:
                v = raw.split(":")[0]
                if not (ct2 and v == "vulkan"):
                    out.add(v)
        return out

    def per_gpu(self) -> dict:
        """{device: [(label, MB), ...]} for the cards in use (CPU left out)."""
        out: dict = {}
        for _r, label, dev, mb in self.roles():
            if dev != "cpu":
                out.setdefault(dev, []).append((label, mb))
        return out

    @property
    def gpu_index(self) -> int:
        return int(self.gpu.split(":")[1]) if ":" in self.gpu else -1

    @property
    def backend(self) -> str:
        if self.vendor == "nvidia" and "cuda" in self.groups:
            return "cuda"
        if self.vendor == "amd" and "rocm" in self.groups:
            return "rocm"
        return "cpu"


def _vram(table, name, default) -> int:
    return dict((n, v) for n, _l, _s, v in table).get(name, default)


def vram_parts(c: Choice) -> list[tuple[str, int]]:
    parts = [("speech", dict((n, v) for n, _l, _s, v in WHISPER).get(c.whisper, 2200))]
    if c.nllb:
        parts.append(("translator", dict((n, v) for n, _l, _s, v in NLLB)[c.nllb]))
    if c.smart:
        parts.append(("smart AI", dict((n, v) for n, _l, _s, v in SMART)[c.smart]))
    return parts


def vram_need(c: Choice) -> int:
    return sum(v for _n, v in vram_parts(c))


def _accel_group(vendor: str) -> str:
    return {"nvidia": "cuda", "amd": "rocm", "vulkan": "vulkan"}.get(vendor, "")


def recommend(hw: Hardware, state: Optional[dict] = None) -> Choice:
    c = Choice()
    g = default_gpu(hw)
    if state:                               # setup ran before: start from that
        c.groups = set(state.get("groups", [])) | {"core"}
        c.whisper = state.get("whisper", c.whisper)
        gk = state.get("gpu")
        if gk is None and "gpu_index" in state:            # 1.21 state
            gk = f"nvidia:{state['gpu_index']}" if state["gpu_index"] >= 0 else "cpu"
        c.gpu = gk if gk == "cpu" or find_gpu(hw, gk or "") else (gpu_key(g) if g else "cpu")
        c.nllb = state.get("nllb")
        c.smart = state.get("smart")
        for k in ("tr_dev", "smart_dev", "listen_dev"):
            v = state.get(k) or "same"
            c_ok = v in ("same", "cpu") or find_gpu(hw, v)
            setattr(c, k, v if c_ok else "same")
        c.listen_model = state.get("listen_model") or "small"
        if state.get("backend") == "rocm":            # 1.22/1.23: AMD support was "the" backend
            c.groups.add("rocm")
        return c
    c.groups = {"core", "media", "vr"}      # Earshot (experimental) only when you ask for it
    if g is not None:
        c.groups.add(_accel_group(g.vendor))
        c.gpu = gpu_key(g)
        v = g.vram_mb
        c.whisper = "large-v3" if v >= 4000 else ("medium" if v >= 2600 else "small")
        if g.integrated:              # shared memory, slower: lighter models
            c.whisper = "medium" if g.maker == "amd" and v >= 3000 else "small"
        c.nllb = "nllb-200-distilled-1.3B" if v >= 9000 else "nllb-200-distilled-600M"
    else:
        c.gpu = "cpu"
        c.whisper = "medium" if hw.cores >= 8 else "small"
        c.nllb = "nllb-200-distilled-600M"
    c.smart = None                          # big: only when you ask for it
    return _from_settings(hw, c)


PRESETS = ("standard", "speech", "keep", "custom")


def preset(hw: Hardware, name: str, state: Optional[dict] = None) -> Choice:
    """standard: speech recognition + translator on the best device; speech: without the
    translator; keep: what's installed now (needs a state)."""
    if name == "keep" and state:
        return recommend(hw, state)
    c = recommend(hw)
    g = find_gpu(hw, c.gpu)
    c.groups = {"core", "media", "vr"} | ({_accel_group(g.vendor)} if g else set())
    c.smart = None
    c.tr_dev = c.smart_dev = c.listen_dev = "same"
    c.nllb = "nllb-200-distilled-600M"     # light and fast; 1.3B is in Custom
    if name == "speech":
        c.nllb = None
    return c


def describe(c: Choice) -> str:
    """One line: what a choice installs."""
    w = dict((n, l) for n, l, *_r in WHISPER).get(c.whisper, c.whisper).split(" - ")[0]
    bits = [f"speech model {w}"]
    if c.nllb:
        bits.append("translator " + dict((n, l) for n, l, *_r in NLLB).get(
            c.nllb, c.nllb).split(" - ")[0])
    if "earshot" in c.groups:
        bits.append("Earshot")
    if c.smart:
        bits.append("smart AI")
    return ", ".join(bits)


def _from_settings(hw: Hardware, c: Choice) -> Choice:
    """An install from before the setup window: keep what the app already uses."""
    try:
        data = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return c
    if data.get("model") in dict((n, 1) for n, *_r in WHISPER):
        c.whisper = data["model"]
    gi = data.get("gpu_index")
    if isinstance(gi, int):
        if gi < 0:
            c.gpu = "cpu"
            c.groups -= {"cuda", "rocm"}
        elif find_gpu(hw, f"nvidia:{gi}"):
            c.gpu = f"nvidia:{gi}"
    tm = data.get("translator_model")
    if tm and nllb_installed(tm):
        c.nllb = tm
    sm = (data.get("earshot") or {}).get("smart_model")
    if sm and smart_installed(sm):
        c.smart = sm
    return c


def fit_gpu(hw: Hardware, dev: str, parts: list, usage=None) -> tuple[str, str]:
    """usage: vram.Usage measured live (what other programs use now), or None."""
    gpu = find_gpu(hw, dev)
    if gpu is None:
        return "no", f"The graphics card {dev} wasn't found."
    short = gpu.name.replace("NVIDIA GeForce ", "").replace("AMD Radeon ", "Radeon ")
    if gpu.vendor == "amd" and gpu.arch and not G.rocm_ok(gpu.arch):
        return "no", (f"{short} ({gpu.arch}) isn't supported by ROCm - pick its Vulkan entry, "
                      "another card or the CPU.")
    if weak_igpu(gpu):
        short += " (older laptop graphics - may not be faster than the CPU)"
    need, have = sum(mb for _l, mb in parts), gpu.vram_mb
    what = " + ".join(l for l, _mb in parts)
    if usage is not None and usage.ok:
        left, others = usage.free_mb, usage.others_mb
        vrc = usage.vrchat_mb()
        busy = (f"VRChat {vrc / 1024:.1f} GB + others {(others - vrc) / 1024:.1f} GB" if vrc
                else f"other programs {others / 1024:.1f} GB")
        if need <= left - max(500, have * 0.08):
            return "ok", (f"{short}: about {need / 1024:.1f} GB ({what}), "
                          f"{left / 1024:.1f} GB free next to what runs now ({busy}).")
        if need <= left:
            return "tight", (f"{short} is tight: about {need / 1024:.1f} GB ({what}), only "
                             f"{left / 1024:.1f} GB free next to {busy}. VRChat needs more in "
                             "busy worlds - things may slow down.")
        return "no", (f"{short} is too full: about {need / 1024:.1f} GB ({what}), but only "
                      f"{left / 1024:.1f} GB is free next to {busy}. Move a model to another "
                      "card or the CPU, or pick a smaller one.")
    if need <= have * 0.85:
        return "ok", f"{short}: about {need / 1024:.1f} of {have / 1024:.0f} GB ({what})."
    if need <= have:
        return "tight", (f"{short} is tight: about {need / 1024:.1f} of {have / 1024:.0f} GB "
                         f"({what}) - close other programs that use it.")
    return "no", (f"{short} is too small: about {need / 1024:.1f} GB ({what}), but it has "
                  f"{have / 1024:.0f} GB. Move a model to another card, pick a smaller one or "
                  "leave smart translation off.")


def fit(hw: Hardware, c: Choice, usage: Optional[dict] = None) -> tuple[str, str]:
    """('ok' | 'tight' | 'no', explanation) for the AI parts on the chosen devices."""
    rank = {"ok": 0, "tight": 1, "no": 2}
    worst, msgs = "ok", []
    missing = [v for v in c.vendors() if _accel_group(v) not in c.groups]
    if missing:
        names = " and ".join({"nvidia": "NVIDIA", "amd": "AMD"}[v] for v in sorted(missing))
        return "no", (f"Tick {names} graphics card support above, or those models run on "
                      "the CPU.")
    for dev, parts in c.per_gpu().items():
        st, msg = fit_gpu(hw, dev, parts, (usage or {}).get(dev))
        worst = st if rank[st] > rank[worst] else worst
        msgs.append(msg)
    cpu = [r for r in c.roles() if r[2] == "cpu"]
    for role, label, _d, _mb in cpu:
        if role == "smart":
            if hw.ram_gb < 12:
                return "no", "Smart translation on the CPU needs at least 12 GB of RAM."
            worst = max(worst, "tight", key=rank.get)
            msgs.append("Smart translation on the CPU works, but takes several seconds per "
                        "sentence.")
        elif role in ("speech", "listen") and label.split("(")[-1].startswith("large"):
            worst = max(worst, "tight", key=rank.get)
            msgs.append("large speech models on the CPU are slow - small or medium is better.")
    if not msgs:
        msgs.append("Runs on the CPU.")
    elif cpu and not any("CPU" in m for m in msgs):
        names = [l for _r, l, _d, _m in cpu]
        msgs.append("The " + " and ".join(names) + (" runs" if len(names) == 1 else " run")
                    + " on the CPU.")
    return worst, " ".join(msgs)


# --------------------------------------------------------------------------- #
# What is installed now
# --------------------------------------------------------------------------- #
def load_state() -> Optional[dict]:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def save_state(c: Choice):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"groups": sorted(c.groups), "whisper": c.whisper,
                                 "gpu": c.gpu, "gpu_index": c.gpu_index, "nllb": c.nllb,
                                 "smart": c.smart, "tr_dev": c.tr_dev,
                                 "smart_dev": c.smart_dev, "listen_dev": c.listen_dev,
                                 "listen_model": c.listen_model,
                                 "backend": c.backend}, indent=2))


def dist_installed(name: str) -> bool:
    try:
        from importlib import metadata
        metadata.version(name)
        return True
    except Exception:
        return False


def group_installed(key: str) -> bool:
    g = GROUP[key]
    if key == "core":
        return dist_installed("faster-whisper")
    if key == "earshot" and not IS_WINDOWS:
        return True                         # nothing to install on Linux
    if key == "rocm":
        import devices
        return devices.rocm_installed()
    if key == "vulkan":
        import whispercpp
        return whispercpp.installed()
    return bool(g.dists) and all(dist_installed(d) for d in g.dists)


def whisper_cache(name: str) -> Path:
    from huggingface_hub.constants import HF_HUB_CACHE
    repo = WHISPER_REPO.get(name, f"Systran/faster-whisper-{name}")
    return Path(HF_HUB_CACHE) / ("models--" + repo.replace("/", "--"))


def hf_cache_dir() -> Path:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        return Path(HF_HUB_CACHE)
    except Exception:
        base = os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")
        return Path(base) / "hub"


def whisper_installed(name: str) -> bool:
    try:
        p = whisper_cache(name)
    except Exception:
        return False
    return any(p.glob("snapshots/*/model.bin"))


def nllb_installed(name: str) -> bool:
    p = paths.models_dir() / name
    return (p / "model.bin").exists() and (p / "sentencepiece.bpe.model").exists()


def smart_installed(name: str) -> bool:
    p = paths.models_dir() / f"smart-{name}"
    return (p / "model.bin").exists() and (p / "tokenizer.json").exists()


def current(hw: Hardware) -> Choice:
    """What's on this PC right now (for updates without a components.json)."""
    st = load_state()
    if st:
        return recommend(hw, st)
    c = recommend(hw)
    c.groups = {g.key for g in GROUPS if group_installed(g.key)} | {"core"}
    return c


# --------------------------------------------------------------------------- #
# Disk space
# --------------------------------------------------------------------------- #
def dir_mb(p: Path) -> float:
    total = 0
    try:
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total / 1e6


def whisper_dirs() -> list[Path]:
    hub = hf_cache_dir()
    return [hub / ("models--" + r.replace("/", "--")) for r in WHISPER_REPO.values()]


def used_space() -> dict:
    """MB used by Chatbox Studio now: {'app': .., 'models': .., 'speech': .., 'total': ..}."""
    root = paths.data_dir()
    models = dir_mb(paths.models_dir())
    whole = dir_mb(root)
    speech = sum(dir_mb(p) for p in whisper_dirs() if p.exists())
    return {"app": whole - models, "models": models, "speech": speech,
            "total": whole + speech}


def space_after(used: dict, steps: list) -> float:
    """MB used after the planned steps (estimate)."""
    total = used.get("total", 0.0)
    for s in steps:
        total += s.disk_delta
    return max(0.0, total)


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
@dataclass
class Step:
    label: str
    cmd: list                     # argv, run with this Python
    weight: float                 # share of the progress bar (by size)
    kind: str = "pip"             # pip | task | remove
    disk_delta: float = 0.0       # MB of disk space this step adds (negative: frees)
    download: float = 0.0         # MB downloaded


def python_exe() -> str:
    """The console python next to the running one (pythonw on Windows has no output)."""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


def pip_progress_flag() -> list:
    try:
        from importlib import metadata
        major, minor = (int(x) for x in metadata.version("pip").split(".")[:2])
        if (major, minor) >= (24, 1):
            return ["--progress-bar", "raw"]
    except Exception:
        pass
    return ["--progress-bar", "off"]


def _wanted_lines(path: Path) -> list[str]:
    """Requirement lines that apply to this OS."""
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line or not line[0].isalpha():
            continue
        if 'sys_platform == "win32"' in line and not IS_WINDOWS:
            continue
        if 'sys_platform == "linux"' in line and IS_WINDOWS:
            continue
        out.append(line)
    return out


def _model_size(table, name) -> int:
    return dict((n, s) for n, _l, s, _v in table).get(name, 1000)


def plan(hw: Hardware, want: Choice, have: Choice, update: bool = False,
         fresh: bool = False) -> list[Step]:
    """Steps from `have` to `want`. update: re-run pip for every ticked part (cheap when
    nothing changed). fresh: reinstall every ticked part and model from scratch."""
    py = python_exe()
    pip = [py, "-m", "pip", "install", "--disable-pip-version-check",
           "--no-warn-script-location"] + pip_progress_flag()
    if fresh:
        pip = pip + ["--force-reinstall"]
    tasks = [py, str(HERE / "setup_tasks.py")]
    steps: list[Step] = []
    for g in GROUPS:
        on = g.key in want.groups
        if g.key == "cuda" and "nvidia" not in want.vendors():
            on = False
        if g.key == "rocm" and "amd" not in want.vendors():
            on = False
        was = g.key in have.groups and group_installed(g.key)
        if g.key == "vulkan":
            on = on and "vulkan" in want.vendors()
            if on and (fresh or not was):
                steps.append(Step("Installing the Vulkan speech engine (whisper.cpp)",
                                  tasks + ["vulkan"] + (["--fresh"] if fresh else []),
                                  g.size_mb, "task", 0 if was and not fresh else g.disk_mb,
                                  g.size_mb))
            elif not on and was:
                steps.append(Step("Removing the Vulkan speech engine",
                                  tasks + ["rm-vulkan"], 5, "task", -g.disk_mb))
            continue
        if g.key == "rocm":
            if on and (fresh or update or not was):
                steps.append(Step("Installing AMD graphics card support (ROCm)",
                                  tasks + ["rocm"] + (["--fresh"] if fresh else []),
                                  g.size_mb, "task", 0 if was and not fresh else g.disk_mb,
                                  g.size_mb if not was or fresh else 30))
            elif not on and was:
                steps.append(Step("Removing AMD graphics card support (ROCm)",
                                  tasks + ["rm-rocm"], 30, "task", -g.disk_mb))
            continue
        if not g.req or not (REQ / g.req).exists():
            continue
        if on and (update or fresh or not was or g.required):
            if not _wanted_lines(REQ / g.req):
                continue                  # nothing to install on this OS
            new = not was or fresh
            steps.append(Step(f"{'Reinstalling' if fresh else 'Installing'} {g.label}",
                              pip + ["-r", str(REQ / g.req)],
                              max(5, g.size_mb if new else 20), "pip",
                              g.disk_mb if not was else 0, g.size_mb if new else 0))
        elif not on and was and g.dists:
            steps.append(Step(f"Removing {g.label}",
                              [py, "-m", "pip", "uninstall", "-y", *g.dists], 3, "remove",
                              -g.disk_mb))
    # models
    def add_model(kind, label, name, table, installed, folder):
        size = _model_size(table, name)
        if fresh and installed:
            steps.append(Step(f"Removing the old {label}", tasks + [f"rm-{kind}", name], 1,
                              "task", -dir_mb(folder)))
            installed = False
        if not installed:
            steps.append(Step(f"Downloading the {label} ({name}, ~{size} MB)",
                              tasks + [kind, name], size, "task", size, size))
    def add_ggml(label, name):
        import whispercpp
        f = whispercpp.model_file(name)
        if fresh and whispercpp.model_installed(name):
            steps.append(Step(f"Removing the old {label}", tasks + ["rm-ggml", name], 1,
                              "task", -ggml_size(name)))
        elif whispercpp.model_installed(name):
            return
        size = ggml_size(name)
        steps.append(Step(f"Downloading the {label} for Vulkan ({name}, ~{size} MB)",
                          tasks + ["ggml", name], size, "task", size, size))
        _ = f
    if want.spec("speech").startswith("vulkan:"):
        add_ggml("speech model", want.whisper)
    else:
        add_model("whisper", "speech model", want.whisper, WHISPER,
                  whisper_installed(want.whisper), _safe(lambda: whisper_cache(want.whisper)))
    if want.nllb:
        add_model("nllb", "translator", want.nllb, NLLB, nllb_installed(want.nllb),
                  paths.models_dir() / want.nllb)
    if want.smart:
        add_model("smart", "smart translation model", want.smart, SMART,
                  smart_installed(want.smart), paths.models_dir() / f"smart-{want.smart}")
    # models you unticked
    for name, *_r in NLLB:
        if name != want.nllb and nllb_installed(name):
            steps.append(Step(f"Removing translator {name}", tasks + ["rm-nllb", name], 1,
                              "task", -dir_mb(paths.models_dir() / name)))
    for name, *_r in SMART:
        if name != want.smart and smart_installed(name):
            steps.append(Step(f"Removing smart model {name}", tasks + ["rm-smart", name], 1,
                              "task", -dir_mb(paths.models_dir() / f"smart-{name}")))
    for name, *_r in WHISPER:
        if name != want.whisper and name == have.whisper and whisper_installed(name) and \
                not (want.listen_dev not in ("same", "") and name == want.listen_model):
            steps.append(Step(f"Removing the old speech model ({name})",
                              tasks + ["rm-whisper", name], 1, "task",
                              -dir_mb(_safe(lambda n=name: whisper_cache(n)))))
    if "earshot" in want.groups and want.listen_dev not in ("same", ""):
        if want.spec("listen").startswith("vulkan:"):
            if not (want.listen_model == want.whisper and
                    want.spec("speech").startswith("vulkan:")):
                add_ggml("Earshot listening model", want.listen_model)
        else:
            add_model("whisper", "Earshot listening model", want.listen_model, WHISPER,
                      whisper_installed(want.listen_model) or (
                          want.listen_model == want.whisper and
                          not want.spec("speech").startswith("vulkan:")),
                      _safe(lambda: whisper_cache(want.listen_model)))
    v = want.vendors()
    checks = sorted(want.groups - ({"cuda"} if "nvidia" not in v else set())
                    - ({"rocm"} if "amd" not in v else set())
                    - ({"vulkan"} if "vulkan" not in v else set()))
    steps.append(Step("Checking that everything loads", tasks + ["check", *checks], 2, "task"))
    return steps


def _safe(fn) -> Path:
    try:
        return fn()
    except Exception:
        return Path("/nonexistent")


def download_mb(steps: list[Step]) -> int:
    return int(sum(s.download for s in steps))


def apply_settings(c: Choice, reset: bool = False):
    """Writes the choice into the app's settings (merging, nothing else changes).
    reset: start from default settings."""
    data = {}
    if not reset:
        try:
            data = json.loads(CONFIG.read_text())
        except (OSError, ValueError):
            data = {}
    data["model"] = c.whisper
    speech = c.spec("speech")
    data["speech_device"] = speech
    data["gpu_index"] = int(speech.split(":")[1]) if ":" in speech else -1

    def dev(role, raw):
        return "same" if (raw or "same") == "same" and c.spec("speech") != "cpu" \
            else c.spec(role)
    if c.nllb:
        data["translator_model"] = c.nllb
        data["translator_device"] = dev("translator", c.tr_dev)
    es = data.get("earshot") or {}
    es["listen_device"] = "same" if c.listen_dev in ("same", "") else c.spec("listen")
    es["listen_model"] = c.listen_model
    if c.smart:
        es["smart_model"] = c.smart
        es["smart_device"] = dev("smart", c.smart_dev)
        es.setdefault("smart", True)
    else:
        es["smart"] = False
    data["earshot"] = es
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(data, indent=2))


# --------------------------------------------------------------------------- #
# Removing everything
# --------------------------------------------------------------------------- #
def uninstall_everything(keep_settings: bool, keep_models: bool) -> None:
    """Writes a small script that waits for this window to close, then deletes Chatbox
    Studio (and, if wanted, its settings and models), and starts it."""
    root = paths.data_dir()
    conf = paths.config_dir()
    models = paths.models_dir()
    speech = [p for p in whisper_dirs() if p.exists()]
    pid = os.getpid()
    if IS_WINDOWS:
        lines = ["$ErrorActionPreference = 'SilentlyContinue'",
                 f"while (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
                 "{ Start-Sleep -Milliseconds 300 }",
                 f"$root = '{root}'",
                 "Get-Process -Name pythonw, python -ErrorAction SilentlyContinue | "
                 "Where-Object { $_.Path -and $_.Path.StartsWith($root, "
                 "[StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force",
                 "Start-Sleep -Seconds 1"]
        for lnk in ("Desktop", "Programs"):
            lines.append(f"Remove-Item (Join-Path ([Environment]::GetFolderPath('{lnk}')) "
                         "'Chatbox Studio.lnk') -Force")
        lines.append("Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) "
                     "'Chatbox Studio Setup.lnk') -Force")
        lines.append("Remove-Item 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\"
                     "Uninstall\\ChatboxStudio' -Recurse -Force")
        if keep_models:
            lines.append(f"Get-ChildItem '{root}' | Where-Object {{ $_.Name -ne 'models' }} | "
                         "Remove-Item -Recurse -Force")
        else:
            lines.append(f"Remove-Item '{root}' -Recurse -Force")
            for p in speech:
                lines.append(f"Remove-Item '{p}' -Recurse -Force")
        if not keep_settings:
            lines.append(f"Remove-Item '{conf}' -Recurse -Force")
        lines.append("Remove-Item $MyInvocation.MyCommand.Path -Force")
        fd, path = tempfile.mkstemp(suffix=".ps1", prefix="chatbox-studio-uninstall-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\r\n".join(lines) + "\r\n")
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", path],
                         creationflags=subprocess.CREATE_NO_WINDOW | 0x00000008,  # DETACHED
                         close_fds=True)
    else:
        apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / \
            "applications"
        binp = Path.home() / ".local" / "bin"
        q = lambda p: "'" + str(p).replace("'", "'\\''") + "'"       # noqa: E731
        lines = ["#!/bin/sh",
                 f"while kill -0 {pid} 2>/dev/null; do sleep 0.3; done",
                 f"pkill -f {q(str(root) + '/app/app.py')} 2>/dev/null",
                 "sleep 1",
                 f"rm -f {q(apps / 'chatbox-studio.desktop')} "
                 f"{q(apps / 'chatbox-studio-setup.desktop')} {q(binp / 'chatbox-studio')} "
                 f"{q(binp / 'chatbox-studio-setup')}"]
        if keep_models:
            lines.append(f"find {q(root)} -mindepth 1 -maxdepth 1 ! -name models "
                         "-exec rm -rf {} +")
        else:
            lines.append(f"rm -rf {q(root)}")
            for p in speech:
                lines.append(f"rm -rf {q(p)}")
        if not keep_settings:
            lines.append(f"rm -rf {q(conf)}")
        lines.append('rm -f "$0"')
        fd, path = tempfile.mkstemp(suffix=".sh", prefix="chatbox-studio-uninstall-")
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.chmod(path, 0o700)
        subprocess.Popen(["sh", path], start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    del models
