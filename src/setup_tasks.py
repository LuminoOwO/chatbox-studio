"""
Steps the setup window runs in a separate process (so freshly installed packages load):

  setup_tasks.py whisper <model>      download a Whisper model
  setup_tasks.py nllb <model>         download the NLLB translator
  setup_tasks.py smart <model>        download the smart translation model
  setup_tasks.py ggml <model>         download a Whisper model for the Vulkan engine
  setup_tasks.py vulkan [--fresh]     install the Vulkan speech engine (whisper.cpp)
  setup_tasks.py rm-whisper|rm-nllb|rm-smart|rm-ggml <model>, rm-vulkan
  setup_tasks.py check <groups...>    check that the libraries load

Prints "PCT <n>" for progress and normal lines for the log. Exit code 0 = done,
3 = blocked by Windows Smart App Control.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402


def pct(n: int):
    print(f"PCT {max(0, min(100, int(n)))}", flush=True)


def say(text: str):
    print(text, flush=True)


def _dir_mb(p: Path) -> float:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total / 1e6


def whisper(name: str):
    import components as K
    from huggingface_hub import snapshot_download
    repo = K.WHISPER_REPO[name]
    size = dict((n, s) for n, _l, s, _v in K.WHISPER)[name]
    target = K.whisper_cache(name)
    done = threading.Event()

    def watch():
        while not done.wait(0.5):
            pct(_dir_mb(target) / size * 100 * 0.99)
    threading.Thread(target=watch, daemon=True).start()
    say(f"Downloading {repo} ...")
    try:
        snapshot_download(repo, allow_patterns=["config.json", "preprocessor_config.json",
                                                "model.bin", "tokenizer.json", "vocabulary.*"])
    finally:
        done.set()
    pct(100)
    say("Speech model ready.")


def nllb(name: str):
    import translator
    translator.download(name, pct)
    say("Translator ready.")


def smart(name: str):
    import smart as S
    S.download(name, pct, say)


def ggml(name: str):
    import whispercpp
    whispercpp.download_model(name, pct, say)
    say("Speech model for Vulkan ready.")


def download(url: str, dest: Path, size_mb: float = 0):
    """Plain HTTPS download with progress (PCT 0-95)."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Chatbox-Studio-Setup"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0) or int(size_mb * 1e6)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                pct(got / total * 95)


def vulkan(fresh: bool) -> int:
    """whisper-server built with Vulkan, from the Chatbox Studio GitHub release."""
    import subprocess
    import tempfile
    import zipfile
    import gpus
    import whispercpp as W
    target = W.engine_dir()
    url = W.engine_url()
    say(f"Downloading the Vulkan speech engine (whisper.cpp {W.WHISPERCPP_VERSION}) ...")
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "engine.zip"
        try:
            download(url, z, 40)
        except Exception as e:
            say(f"ERROR the download failed ({e}). {url}")
            return 1
        if fresh or target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            for m in zf.infolist():
                name = Path(m.filename).name          # flat: no folders from the zip
                if not name or m.is_dir():
                    continue
                with zf.open(m) as src, open(target / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    exe = W.server_exe()
    if not exe.exists():
        say("ERROR the engine package has no whisper-server")
        return 1
    if not paths.IS_WINDOWS:
        for f in target.iterdir():
            f.chmod(0o755)
    pct(97)
    try:
        r = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=20,
                           **paths.hidden_subprocess_kwargs())
        ok = r.returncode == 0 or "usage" in (r.stdout + r.stderr).lower()
    except Exception as e:
        ok, r = False, None
        say(f"✗ whisper-server doesn't start: {e}")
    if not ok:
        err = ((r.stderr if r else "") or "").strip().splitlines()[-1:] or ["?"]
        if r is not None and any(b in (r.stderr or "") for b in BLOCKED):
            say(f"BLOCKED vulkan: {err[0]}")
            return 3
        say(f"⚠ whisper-server doesn't start: {err[0]}")
        if not paths.IS_WINDOWS:
            say("  Your system needs the Vulkan loader (package libvulkan1 / vulkan-loader) "
                "and a Vulkan driver (Mesa: mesa-vulkan-drivers / vulkan-radeon / "
                "vulkan-intel).")
        return 5
    devs = gpus.vulkan()
    if devs:
        say("✓ Vulkan sees: " + ", ".join(f"{g.name}" + (" (integrated)" if g.integrated
                                                       else "") for g in devs))
    else:
        say("⚠ The engine is installed, but Vulkan sees no graphics chip. Install your "
            "graphics driver's Vulkan support" + ("." if paths.IS_WINDOWS else
            " (Mesa: mesa-vulkan-drivers / vulkan-radeon / vulkan-intel)."))
    pct(100)
    return 0


def rm_vulkan() -> int:
    import whispercpp as W
    shutil.rmtree(W.engine_dir(), ignore_errors=True)
    say("Removed the Vulkan speech engine.")
    pct(100)
    return 0


def remove(kind: str, name: str):
    import components as K
    if kind == "rm-ggml":
        import whispercpp
        p = whispercpp.model_file(name)
        if p.exists():
            p.unlink()
            say(f"Removed {p}")
        pct(100)
        return
    if kind == "rm-whisper":
        p = K.whisper_cache(name)
    elif kind == "rm-nllb":
        p = paths.models_dir() / name
    else:
        p = paths.models_dir() / f"smart-{name}"
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
        say(f"Removed {p}")
    pct(100)


# --------------------------------------------------------------------------- #
# AMD (ROCm)
# --------------------------------------------------------------------------- #
def _pip(args: list[str]) -> int:
    import subprocess
    import components as K
    cmd = [sys.executable, "-m", "pip", *args, "--disable-pip-version-check",
           "--no-warn-script-location"] + K.pip_progress_flag()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", **paths.hidden_subprocess_kwargs())
    for line in p.stdout:
        print(line.rstrip(), flush=True)
    return p.wait()


def _ct2_rocm_wheel(tmp: str) -> str:
    import components as K
    import httpzip
    v = K.CT2_ROCM_VERSION
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    osname = "Windows" if paths.IS_WINDOWS else "Linux"
    url = K.CT2_ROCM_URL.format(v=v, os=osname)
    say(f"Getting the AMD build of CTranslate2 {v} ({tag}) from github.com/OpenNMT ...")

    def pick(name):
        base = name.rsplit("/", 1)[-1]
        return base.startswith(f"ctranslate2-{v}-{tag}-{tag}-") and base.endswith(".whl") and \
            ("win_amd64" in base if paths.IS_WINDOWS else "x86_64" in base)
    return httpzip.extract(url, pick, tmp, lambda n: pct(60 + n * 0.3))


def _env_with_rocm() -> dict:
    import os
    import gpus
    env = dict(os.environ)
    if not paths.IS_WINDOWS:
        libs = gpus.rocm_libs_linux()
        if libs:
            env["LD_LIBRARY_PATH"] = libs + (":" + env["LD_LIBRARY_PATH"]
                                             if env.get("LD_LIBRARY_PATH") else "")
    return env


def _rocm_probe() -> "subprocess.CompletedProcess":
    """Load the AMD engine the way the helper process does (its folder first on the path)."""
    import subprocess
    import devices
    code = ("import sys; sys.path.insert(0, %r); import ctranslate2; "
            "print(ctranslate2.get_cuda_device_count())" % str(devices.rocm_engine_dir()))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=_env_with_rocm(), **paths.hidden_subprocess_kwargs())


def _migrate_rocm_from_venv():
    """1.22/1.23 replaced the app's own CTranslate2 with the AMD build. Since 1.24 the AMD
    build lives in its own folder (so NVIDIA and AMD cards can be used together) - put the
    standard build back into the app."""
    import components as K
    if K.load_state().get("backend") == "rocm":
        say("Moving the AMD engine into its own folder (new in 1.24: NVIDIA + AMD together) ...")
        _pip(["install", "--force-reinstall", "--no-deps", "ctranslate2>=4.4"])


def rocm(fresh: bool) -> int:
    import shutil
    import tempfile
    import components as K
    import devices
    import gpus
    target = devices.rocm_engine_dir()
    if not paths.IS_WINDOWS:
        libs = gpus.rocm_libs_linux()
        if libs is None:
            say("NEED_ROCM The ROCm 7.2 system libraries (HIP, hipBLAS, hipRAND) are missing.")
            return 4
        say(f"Found ROCm in {libs or 'the system library path'}.")
    _migrate_rocm_from_venv()
    if fresh and target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    tgt = ["--target", str(target), "--upgrade"]
    if paths.IS_WINDOWS:
        say("Installing AMD's ROCm 7.2.1 runtime (repo.radeon.com) ...")
        urls = [K.ROCM_WIN + w for w in K.ROCM_WIN_WHEELS]
        if _pip(["install", "--no-deps"] + tgt + urls) != 0:
            return 1
    pct(60)
    with tempfile.TemporaryDirectory() as tmp:
        whl = _ct2_rocm_wheel(tmp)
        pct(90)
        if _pip(["install", "--no-deps"] + tgt + [whl]) != 0:
            return 1
    r = _rocm_probe()
    n = (r.stdout.strip().splitlines() or ["0"])[-1]
    if r.returncode != 0:
        err = (r.stderr.strip().splitlines() or [""])[-1]
        if any(b in r.stderr for b in BLOCKED):
            say(f"BLOCKED rocm: {err}")
            return 3
        say(f"⚠ The AMD engine doesn't load ({err}). Models set to an AMD card will fall back "
            "to the CPU until this is fixed.")
        return 5
    if not n.isdigit() or int(n) == 0:
        err = (r.stderr.strip().splitlines() or [""])[-1]
        say(f"⚠ The AMD engine is installed, but sees no graphics card {err}")
        say("  Windows: update the AMD Adrenalin driver (26.2.2 or newer) and restart."
            if paths.IS_WINDOWS else
            "  Linux: add yourself to the render and video groups and log in again "
            "(sudo usermod -aG render,video $USER).")
        if any(b in r.stderr for b in BLOCKED):
            return 3
        return 5
    say(f"✓ The AMD engine sees {n} graphics card(s). NVIDIA cards keep working next to it.")
    pct(100)
    return 0


def rm_rocm() -> int:
    import shutil
    import devices
    _migrate_rocm_from_venv()
    if paths.IS_WINDOWS:          # 1.22/1.23 put these into the app itself
        _pip(["uninstall", "-y", "rocm-sdk-core", "rocm-sdk-libraries-custom"])
    shutil.rmtree(devices.rocm_engine_dir(), ignore_errors=True)
    say("Removed the AMD engine.")
    pct(100)
    return 0


CHECKS = {
    "core": "import numpy, ctranslate2, onnxruntime, sounddevice, sentencepiece, faster_whisper",
    "media": ("import pycaw.pycaw; from winrt.windows.media.control import "
              "GlobalSystemMediaTransportControlsSessionManager") if paths.IS_WINDOWS else
             "import jeepney, pulsectl",
    "vr": "import openvr",
    "earshot": "import soundcard" if paths.IS_WINDOWS else "",
}
BLOCKED = ("Application Control", "4551", "0x800711C7")


def check(groups: list[str]) -> int:
    import subprocess
    bad = 0
    for g in groups:
        code = CHECKS.get(g)
        if not code:
            continue
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=_env_with_rocm(), **paths.hidden_subprocess_kwargs())
        if r.returncode == 0:
            say(f"✓ {g}")
            continue
        err = (r.stderr.strip().splitlines() or ["?"])[-1]
        if any(b in r.stderr for b in BLOCKED):
            say(f"BLOCKED {g}: {err}")
            return 3
        say(f"✗ {g}: {err}")
        if g == "core":
            bad = 1
    if "rocm" in groups:
        r = _rocm_probe()
        n = (r.stdout.strip().splitlines() or ["0"])[-1]
        if r.returncode == 0 and n.isdigit() and int(n) > 0:
            say(f"✓ rocm ({n} card(s))")
        else:
            say(f"✗ rocm: {(r.stderr.strip().splitlines() or ['sees no card'])[-1]}")
    if "vulkan" in groups:
        import whispercpp as W
        say("✓ vulkan" if W.installed() else "✗ vulkan: the engine is missing")
    if "cuda" in groups:
        import components as K
        if not K.dist_installed("nvidia-cublas-cu12"):
            say("✗ cuda: the CUDA libraries are missing")
    pct(100)
    return bad


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    t0 = time.time()
    try:
        if cmd == "whisper":
            whisper(args[0])
        elif cmd == "nllb":
            nllb(args[0])
        elif cmd == "smart":
            smart(args[0])
        elif cmd == "ggml":
            ggml(args[0])
        elif cmd in ("rm-whisper", "rm-nllb", "rm-smart", "rm-ggml"):
            remove(cmd, args[0])
        elif cmd == "vulkan":
            return vulkan("--fresh" in args)
        elif cmd == "rm-vulkan":
            return rm_vulkan()
        elif cmd == "check":
            return check(args)
        elif cmd == "rocm":
            return rocm("--fresh" in args)
        elif cmd == "rm-rocm":
            return rm_rocm()
        else:
            say(f"unknown step {cmd}")
            return 2
    except Exception as e:
        say(f"ERROR {type(e).__name__}: {e}")
        return 1
    say(f"({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
