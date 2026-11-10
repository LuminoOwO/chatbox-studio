"""
Graphics cards the AI can run on:

    nvidia   CUDA (CTranslate2)
    amd      ROCm / HIP (the ROCm build of CTranslate2) - Radeon cards ROCm supports
    vulkan   Vulkan (whisper.cpp) - any other graphics: AMD cards and APUs without ROCm,
             Intel Arc and Intel integrated graphics, ... Speech recognition only.

CTranslate2 calls both NVIDIA and AMD "cuda" devices: the ROCm build runs HIP under the same
API, so the app only needs the right device numbers and names.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import paths

IS_WINDOWS = paths.IS_WINDOWS


@dataclass
class GPU:
    index: int            # device number for CTranslate2 (per vendor)
    name: str
    vram_mb: int
    vendor: str           # engine prefix: "nvidia" | "amd" | "vulkan"
    cc: float = 0.0       # NVIDIA compute capability
    arch: str = ""        # AMD gfx target, e.g. gfx1201
    bus: str = ""         # PCI address (Linux AMD), to read its live memory use
    maker: str = ""       # who made the chip: "nvidia" | "amd" | "intel" | ... (Vulkan)
    integrated: bool = False   # shares the system memory (APU / laptop graphics)
    device_id: int = 0    # PCI device id (Vulkan), to match it with sysfs / DXGI

    @property
    def engine(self) -> str:
        return {"nvidia": "CUDA", "amd": "ROCm", "vulkan": "Vulkan"}.get(self.vendor, "")


def _run(cmd, timeout=8) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              **paths.hidden_subprocess_kwargs()).stdout
    except Exception:
        return ""


def nvidia() -> list[GPU]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    out = _run([exe, "--query-gpu=index,name,memory.total,compute_cap",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) >= 3:
            try:
                cc = float(p[3]) if len(p) > 3 else 0.0
            except ValueError:
                cc = 0.0
            try:
                gpus.append(GPU(int(p[0]), p[1], int(float(p[2])), "nvidia", cc))
            except ValueError:
                pass
    return gpus


# PCI device ids of current Radeon cards -> (name, gfx target). Others get the lspci /
# Windows name and are checked by ROCm itself.
AMD_IDS = {
    "7550": ("AMD Radeon RX 9070 / 9070 XT", "gfx1201"),
    "7590": ("AMD Radeon RX 9060 XT", "gfx1200"),
    "744c": ("AMD Radeon RX 7900 XT / XTX", "gfx1100"),
    "7448": ("AMD Radeon Pro W7900", "gfx1100"),
    "747e": ("AMD Radeon RX 7800 XT / 7700 XT", "gfx1101"),
    "7480": ("AMD Radeon RX 7600 / 7600 XT", "gfx1102"),
    "73bf": ("AMD Radeon RX 6800 / 6900 XT", "gfx1030"),
    "73df": ("AMD Radeon RX 6700 XT", "gfx1031"),
    "73ff": ("AMD Radeon RX 6600 / 6600 XT", "gfx1032"),
}
ROCM_ARCHS = {"gfx1030", "gfx1100", "gfx1101", "gfx1102", "gfx1200", "gfx1201", "gfx1150",
              "gfx1151", "gfx906", "gfx908", "gfx90a", "gfx942", "gfx950"}
# Linux only: chips ROCm runs on when told to treat them like a close relative
# (HSA_OVERRIDE_GFX_VERSION). Mostly APUs and the smaller RDNA2 / RDNA3 cards.
ROCM_OVERRIDE = {"gfx1031": "10.3.0", "gfx1032": "10.3.0", "gfx1034": "10.3.0",
                 "gfx1035": "10.3.0", "gfx1036": "10.3.0", "gfx1103": "11.0.0",
                 "gfx1152": "11.5.1"}
# APUs (graphics inside the processor) - they use the system memory
AMD_APU_ARCHS = {"gfx90c", "gfx1035", "gfx1036", "gfx1037", "gfx1103", "gfx1150", "gfx1151",
                 "gfx1152", "gfx1153"}


def rocm_ok(arch: str) -> bool:
    """Can the ROCm engine run on this gfx target (on this OS)?"""
    if not arch:
        return True                    # unknown card: let ROCm decide
    return arch in ROCM_ARCHS or (not IS_WINDOWS and arch in ROCM_OVERRIDE)


def _sysfs_arch(dev: Path) -> str:
    """gfx target from the amdgpu driver's IP discovery (newer kernels), e.g. gfx1103."""
    for f in dev.glob("ip_discovery/die/0/GC/0/major"):
        try:
            base = f.parent
            ma, mi, rev = (int((base / n).read_text()) for n in ("major", "minor", "revision"))
            return f"gfx{ma}{mi}{rev:x}" if ma >= 10 else ""   # gfx9 names don't follow it
        except (OSError, ValueError):
            pass
    return ""


def _amd_linux() -> list[GPU]:
    found = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue
        dev = card / "device"
        try:
            if (dev / "vendor").read_text().strip() != "0x1002":
                continue
            did = (dev / "device").read_text().strip().lower().replace("0x", "")
            vram = int((dev / "mem_info_vram_total").read_text().strip()) // 2**20
            bus = os.path.basename(os.path.realpath(dev))
        except (OSError, ValueError):
            continue
        name, arch = AMD_IDS.get(did, ("", ""))
        arch = arch or _sysfs_arch(dev)
        apu = arch in AMD_APU_ARCHS
        if apu:                               # APU: system memory it may use (GTT) counts too
            try:
                gtt = int((dev / "mem_info_gtt_total").read_text().strip()) // 2**20
                vram = max(vram, min(gtt, vram + gtt) // 2)
            except (OSError, ValueError):
                pass
        if not name:
            out = _run(["lspci", "-mm", "-s", bus])
            cols = re.findall(r'"([^"]*)"', out)
            name = re.sub(r"^.*\[(.+)\]$", r"\1", cols[2]) if len(cols) >= 3 else f"AMD GPU {did}"
            if not name.lower().startswith("amd"):
                name = "AMD " + name
        if vram < 1500 or not rocm_ok(arch):  # tiny or not for ROCm: Vulkan takes it
            continue
        found.append((bus, GPU(0, name, vram, "amd", arch=arch, bus=bus, maker="amd",
                               integrated=apu, device_id=int(did, 16))))
    found.sort(key=lambda x: x[0])           # HIP numbers cards in PCI order
    out = []
    for i, (_bus, g) in enumerate(found):
        g.index = i
        out.append(g)
    return out


def _amd_windows() -> list[GPU]:
    """Name and real VRAM from the display driver's registry entries (WMI's AdapterRAM
    stops at 4 GB)."""
    out = []
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as k:
                        def val(n, default=None):
                            try:
                                return winreg.QueryValueEx(k, n)[0]
                            except OSError:
                                return default
                        desc = str(val("DriverDesc", "") or "")
                        prov = str(val("ProviderName", "") or "")
                        if "amd" not in (desc + prov).lower() and "radeon" not in desc.lower():
                            continue
                        mem = val("HardwareInformation.qwMemorySize") or \
                            val("HardwareInformation.MemorySize") or 0
                        if isinstance(mem, bytes):
                            mem = int.from_bytes(mem[:8], "little")
                        mid = str(val("MatchingDeviceId", "") or "").lower()
                        m = re.search(r"dev_([0-9a-f]{4})", mid)
                        arch = AMD_IDS.get(m.group(1), ("", ""))[1] if m else ""
                        vram = int(mem) // 2**20
                        if vram >= 1500 and rocm_ok(arch):
                            out.append(GPU(len(out), desc.strip(), vram, "amd", arch=arch,
                                           maker="amd", integrated=arch in AMD_APU_ARCHS,
                                           device_id=int(m.group(1), 16) if m else 0))
                except OSError:
                    continue
    except Exception:
        pass
    return out


def amd() -> list[GPU]:
    return _amd_windows() if IS_WINDOWS else _amd_linux()


# --------------------------------------------------------------------------- #
# Vulkan: every graphics chip with a Vulkan driver (Mesa RADV / ANV, AMD, Intel, NVIDIA)
# --------------------------------------------------------------------------- #
MAKERS = {0x1002: "amd", 0x1022: "amd", 0x10DE: "nvidia", 0x8086: "intel", 0x13B5: "arm",
          0x5143: "qualcomm", 0x106B: "apple"}
_vk_cache: Optional[list] = None


def vulkan_probe() -> list[dict]:
    """Asks the Vulkan loader for the graphics chips, in the order whisper.cpp numbers them.
    Runs inside a short helper process (see vulkan()), because loading graphics drivers
    into the app itself can be slow or crash on broken drivers."""
    import ctypes
    lib = ctypes.WinDLL("vulkan-1.dll") if IS_WINDOWS else ctypes.CDLL("libvulkan.so.1")

    class AppInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int32), ("pNext", ctypes.c_void_p),
                    ("pApplicationName", ctypes.c_char_p), ("applicationVersion", ctypes.c_uint32),
                    ("pEngineName", ctypes.c_char_p), ("engineVersion", ctypes.c_uint32),
                    ("apiVersion", ctypes.c_uint32)]

    class CreateInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int32), ("pNext", ctypes.c_void_p),
                    ("flags", ctypes.c_uint32), ("pApplicationInfo", ctypes.POINTER(AppInfo)),
                    ("enabledLayerCount", ctypes.c_uint32),
                    ("ppEnabledLayerNames", ctypes.c_void_p),
                    ("enabledExtensionCount", ctypes.c_uint32),
                    ("ppEnabledExtensionNames", ctypes.c_void_p)]

    app = AppInfo(0, None, b"Chatbox Studio", 1, b"Chatbox Studio", 1, (1 << 22) | (1 << 12))
    ci = CreateInfo(1, None, 0, ctypes.pointer(app), 0, None, 0, None)
    inst = ctypes.c_void_p()
    lib.vkCreateInstance.restype = ctypes.c_int32
    if lib.vkCreateInstance(ctypes.byref(ci), None, ctypes.byref(inst)) != 0:
        return []
    out = []
    try:
        n = ctypes.c_uint32(0)
        lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), None)
        devs = (ctypes.c_void_p * n.value)()
        lib.vkEnumeratePhysicalDevices(inst, ctypes.byref(n), devs)
        for i in range(n.value):
            props = ctypes.create_string_buffer(4096)   # VkPhysicalDeviceProperties
            lib.vkGetPhysicalDeviceProperties(ctypes.c_void_p(devs[i]), props)
            raw = props.raw
            vendor_id = int.from_bytes(raw[8:12], "little")
            device_id = int.from_bytes(raw[12:16], "little")
            dtype = int.from_bytes(raw[16:20], "little", signed=True)
            name = raw[20:276].split(b"\0", 1)[0].decode("utf-8", "replace")
            mem = ctypes.create_string_buffer(1024)     # VkPhysicalDeviceMemoryProperties
            lib.vkGetPhysicalDeviceMemoryProperties(ctypes.c_void_p(devs[i]), mem)
            m = mem.raw
            heaps = []
            count = int.from_bytes(m[260:264], "little")
            for h in range(min(count, 16)):
                off = 264 + h * 16
                size = int.from_bytes(m[off:off + 8], "little")
                flags = int.from_bytes(m[off + 8:off + 12], "little")
                heaps.append((size // 2**20, bool(flags & 1)))
            out.append({"index": i, "name": name, "vendor_id": vendor_id,
                        "device_id": device_id, "type": dtype, "heaps": heaps})
    finally:
        lib.vkDestroyInstance(inst, None)
    return out


def vulkan(include_cpu: bool = False) -> list[GPU]:
    """Graphics chips usable through Vulkan (whisper.cpp). Software renderers (llvmpipe)
    are left out unless include_cpu."""
    global _vk_cache
    if _vk_cache is None:
        import sys
        code = ("import json, sys; sys.path.insert(0, %r); import gpus; "
                "print(json.dumps(gpus.vulkan_probe()))" % str(Path(__file__).resolve().parent))
        txt = _run([sys.executable, "-c", code], timeout=15)
        try:
            _vk_cache = json.loads(txt.strip().splitlines()[-1]) if txt.strip() else []
        except (ValueError, IndexError):
            _vk_cache = []
    out = []
    for d in _vk_cache:
        if d.get("type") == 4 and not include_cpu:          # CPU (lavapipe / llvmpipe)
            continue
        if d.get("type") == 3:                               # virtual GPU
            continue
        heaps = d.get("heaps") or []
        local = max([mb for mb, dl in heaps if dl] or [0])
        integrated = d.get("type") == 1
        if integrated:          # shares system RAM: about half of the biggest heap is usable
            local = max(local, max([mb for mb, _dl in heaps] or [0]) // 2)
        name = re.sub(r"\s*\((RADV|LLVM|ANV|NVK)[^)]*\)", "", d.get("name", "")).strip()
        out.append(GPU(int(d["index"]), name or "Graphics", local, "vulkan",
                       maker=MAKERS.get(int(d.get("vendor_id", 0)), "other"),
                       integrated=integrated, device_id=int(d.get("device_id", 0))))
    return out


# --------------------------------------------------------------------------- #
# Which CTranslate2 build is installed
# --------------------------------------------------------------------------- #
def _state() -> dict:
    try:
        return json.loads((paths.data_dir() / "components.json").read_text())
    except (OSError, ValueError):
        return {}


def backend() -> str:
    b = _state().get("backend")
    if b in ("cuda", "rocm", "cpu"):
        return b
    return "cuda" if shutil.which("nvidia-smi") else "cpu"


def ai_gpus() -> list[GPU]:
    """Cards the installed engine can use, with CTranslate2's device numbers."""
    import devices
    return devices.available_gpus()


def rocm_libs_linux() -> Optional[str]:
    """Folder with the system ROCm libraries the AMD engine needs, or None."""
    need = ("libamdhip64.so.7", "libhipblas.so.3", "libhiprand.so.1")
    for d in [os.environ.get("ROCM_PATH", ""), "/opt/rocm"] + sorted(
            (str(p) for p in Path("/opt").glob("rocm-7*")), reverse=True):
        if d and all((Path(d) / "lib" / n).exists() for n in need):
            return str(Path(d) / "lib")
    out = _run(["ldconfig", "-p"])
    if all(n in out for n in need):
        return ""
    return None
