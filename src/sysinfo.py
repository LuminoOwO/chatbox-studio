"""
PC specs and live system values for the chatbox, on Linux and Windows.

Static values (CPU model, GPU, OS, ...) are read once in the background.
Live values (usage, temperatures, network, ...) are read every couple of seconds.
Anything a system can't tell us is left empty, so "[ ... ]" parts in formats disappear.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import paths

IS_WINDOWS = paths.IS_WINDOWS

# (placeholder, description, group) - shown in the GUI with the current value, by group
PLACEHOLDERS = [
    ("os", "Operating system", "System"),
    ("os_name", "OS name only", "System"),
    ("os_version", "OS version", "System"),
    ("os_icon", "🐧 on Linux, 🪟 on Windows", "System"),
    ("kernel", "Kernel / build", "System"),
    ("desktop", "Desktop environment", "System"),
    ("session", "Display server (X11 / Wayland)", "System"),
    ("pc_model", "PC or laptop model", "System"),
    ("board", "Motherboard", "System"),
    ("hostname", "Computer name (private!)", "System"),
    ("uptime", "Time since the PC started", "System"),
    ("bios", "BIOS / UEFI version", "System"),
    ("arch", "Architecture (x86_64, ARM64)", "System"),
    ("processes", "Running programs (processes)", "System"),
    ("cpu_name", "CPU model", "CPU"),
    ("cpu_short", "CPU model, short", "CPU"),
    ("cores", "Cores", "CPU"),
    ("threads", "Threads", "CPU"),
    ("cpu_max", "Max clock (GHz)", "CPU"),
    ("cpu_vendor", "Maker (Intel / AMD)", "CPU"),
    ("cpu_cache", "L3 cache (MB)", "CPU"),
    ("cpu", "Usage (%)", "CPU"),
    ("cpu_freq", "Clock now (GHz)", "CPU"),
    ("cpu_temp", "Temperature (°C)", "CPU"),
    ("cpu_fan", "CPU fan (RPM, Linux)", "CPU"),
    ("gpu_name", "Graphics card", "Graphics"),
    ("gpu_short", "Graphics card, short", "Graphics"),
    ("gpus", "All graphics cards", "Graphics"),
    ("gpu_driver", "Driver version", "Graphics"),
    ("gpu", "Usage (%)", "Graphics"),
    ("gpu_temp", "Temperature (°C)", "Graphics"),
    ("gpu_power", "Power (W)", "Graphics"),
    ("gpu_fan", "Fan (%)", "Graphics"),
    ("gpu_clock", "Clock (MHz)", "Graphics"),
    ("gpu_mem_clock", "Memory clock (MHz)", "Graphics"),
    ("gpu_power_limit", "Power limit (W)", "Graphics"),
    ("gpu_pcie", "PCIe link (Gen x width)", "Graphics"),
    ("vram", "VRAM usage (%)", "Graphics"),
    ("vram_used", "VRAM used (GB)", "Graphics"),
    ("vram_total", "VRAM total (GB)", "Graphics"),
    ("ram", "RAM usage (%)", "Memory"),
    ("ram_used", "RAM used (GB)", "Memory"),
    ("ram_total", "RAM total (GB)", "Memory"),
    ("ram_speed", "RAM speed (MT/s, Windows)", "Memory"),
    ("ram_type", "RAM type (DDR4, DDR5, Windows)", "Memory"),
    ("ram_sticks", "RAM sticks (4x8 GB, Windows)", "Memory"),
    ("ram_maker", "RAM maker (Windows)", "Memory"),
    ("swap", "Swap / page file usage (%)", "Memory"),
    ("disk", "System drive used (%)", "Storage"),
    ("disk_free", "System drive free (GB)", "Storage"),
    ("disk_total", "System drive size (GB)", "Storage"),
    ("disk_model", "System drive model", "Storage"),
    ("disk_fs", "System drive file system", "Storage"),
    ("disks", "All drives", "Storage"),
    ("disk_read", "Drive read speed", "Storage"),
    ("disk_write", "Drive write speed", "Storage"),
    ("disk_temp", "SSD temperature (°C)", "Storage"),
    ("net_down", "Download speed", "Network"),
    ("net_up", "Upload speed", "Network"),
    ("net_type", "Connection (Wi-Fi / Ethernet)", "Network"),
    ("net_link", "Link speed (Mbit/s)", "Network"),
    ("wifi_signal", "Wi-Fi signal (%, Linux)", "Network"),
    ("screen", "Main screen resolution", "Display"),
    ("refresh", "Main screen refresh rate (Hz)", "Display"),
    ("screens", "All screens", "Display"),
    ("screen_count", "Number of screens", "Display"),
    ("battery", "Laptop battery (%)", "Power"),
    ("battery_state", "Charging / on battery", "Power"),
    ("hmd", "Headset (maker + model)", "VR headset"),
    ("hmd_model", "Headset model", "VR headset"),
    ("hmd_maker", "Headset maker", "VR headset"),
    ("hmd_refresh", "Refresh rate (Hz)", "VR headset"),
    ("hmd_battery", "Headset battery (%, wireless)", "VR headset"),
    ("hmd_charging", "Headset charging", "VR headset"),
    ("ipd", "IPD (mm)", "VR headset"),
    ("vr_runtime", "VR software (SteamVR, ...)", "VR headset"),
    ("vr_link", "Streaming (Virtual Desktop, ALVR, ...)", "VR headset"),
    ("vr_tracking", "Tracking system", "VR headset"),
    ("vr_connected", "“connected” when the headset is connected", "VR headset"),
    ("vr_session", "“in VR” while a VR game runs", "VR headset"),
    ("hmd_device", "Headset device name (Monado/WiVRn)", "VR headset"),
    ("hmd_serial", "Headset serial (private!)", "VR headset"),
    ("hmd_rates", "Refresh rates the headset offers", "WiVRn"),
    ("vr_bitrate", "Stream bitrate (Mbit/s)", "WiVRn"),
    ("vr_codec", "Video codecs", "WiVRn"),
    ("vr_features", "Hand / eye / face tracking", "WiVRn"),
    ("hand_tracking", "“hand tracking” when supported", "WiVRn"),
    ("eye_tracking", "“eye tracking” when supported", "WiVRn"),
    ("face_tracking", "“face tracking” when supported", "WiVRn"),
    ("vr_mic", "Headset mic (kHz, channels)", "WiVRn"),
    ("vr_speakers", "Headset speakers (kHz, channels)", "WiVRn"),
    ("ctrl_model", "Controllers", "Controllers & trackers"),
    ("ctrl_battery", "Controller batteries (L / R)", "Controllers & trackers"),
    ("ctrl_left_battery", "Left controller battery (%)", "Controllers & trackers"),
    ("ctrl_right_battery", "Right controller battery (%)", "Controllers & trackers"),
    ("trackers", "Number of trackers", "Controllers & trackers"),
    ("tracker_battery", "Lowest tracker battery (%)", "Controllers & trackers"),
    ("tracker_model", "Tracker model", "Controllers & trackers"),
    ("base_stations", "Base stations", "Controllers & trackers"),
    ("fbt", "“Full body” with 3+ trackers", "Controllers & trackers"),
    ("audio_out", "Sound output (headphones/speakers)", "Audio"),
    ("audio_in", "Microphone", "Audio"),
    ("volume", "Output volume (%)", "Audio"),
    ("muted", "“muted” when output is muted", "Audio"),
    ("devices", "Wireless devices with battery", "Devices"),
    ("headphones_battery", "Headphones / headset battery (%)", "Devices"),
    ("mouse_battery", "Mouse battery (%)", "Devices"),
    ("keyboard_battery", "Keyboard battery (%)", "Devices"),
    ("gamepad_battery", "Gamepad battery (%)", "Devices"),
    ("phone_battery", "Phone battery (%)", "Devices"),
    ("device_count", "Number of devices with battery", "Devices"),
    ("vrc_mode", "VR or Desktop (from VRChat)", "VRChat"),
    ("vrc_tracking", "Tracking (3-point, full body, ...)", "VRChat"),
    ("vrc_height", "Avatar eye height", "VRChat"),
    ("vrc_scale", "Avatar scale", "VRChat"),
    ("vrc_seated", "“seated” when sitting", "VRChat"),
    ("vrc_earmuffs", "“earmuffs” when on", "VRChat"),
]
# Values that only exist on Linux (WiVRn, Monado and the battery list of upower) are left
# out on Windows, so the chatbox editor doesn't offer blocks that would stay empty there.
LINUX_ONLY_GROUPS = {"WiVRn", "Devices"}
LINUX_ONLY_KEYS = {"hmd_device"}
if IS_WINDOWS:
    PLACEHOLDERS = [p for p in PLACEHOLDERS
                    if p[2] not in LINUX_ONLY_GROUPS and p[0] not in LINUX_ONLY_KEYS]
GROUPS = list(dict.fromkeys(g for _k, _d, g in PLACEHOLDERS))
VR_KEYS = {k for k, _d, g in PLACEHOLDERS if g in ("VR headset", "Controllers & trackers", "WiVRn")}
AUDIO_KEYS = {k for k, _d, g in PLACEHOLDERS if g == "Audio"}
DEVICE_KEYS = {k for k, _d, g in PLACEHOLDERS if g == "Devices"}


def _run(cmd: list[str], timeout: float = 6) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              **paths.hidden_subprocess_kwargs()).stdout
    except Exception:
        return ""


def _read(p: str) -> str:
    try:
        return Path(p).read_text(errors="replace").strip()
    except OSError:
        return ""


JUNK = re.compile(r"^(to be filled by o\.?e\.?m\.?|system product name|default string|"
                  r"system manufacturer|not applicable|none|o\.e\.m\.|x\.x|0+|base board)$", re.I)


def _clean(s: str) -> str:
    s = (s or "").strip()
    if not s or JUNK.match(s):
        return ""
    if s.isupper() and len(s) > 4 and s.isalpha():     # "LENOVO" -> "Lenovo"
        s = s.title()
    return s


def clean_cpu(name: str) -> tuple[str, str]:
    """'12th Gen Intel(R) Core(TM) i5-12400 @ 2.50GHz' -> ('Intel Core i5-12400', 'i5-12400')"""
    n = re.sub(r"\((R|TM|tm|r)\)|®|™", "", name or "")
    n = re.sub(r"\s*@\s*[\d.]+\s*GHz", "", n)
    n = re.sub(r"\b\d+(st|nd|rd|th) Gen\b", "", n)
    n = re.sub(r"\b(CPU|Processor|\d+-Core Processor|with Radeon Graphics|w/ Radeon .*)\b", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    m = re.search(r"\b(i[3579]-\w+|Core Ultra \d+ \w+|Ryzen \w+ \w+|Threadripper \w+ ?\w*|"
                  r"Xeon \w+[- ]?\w*|Pentium \w+|Celeron \w+|Apple M\d \w*|Snapdragon \w+ ?\w*)",
                  n)
    return n, (m.group(1).strip() if m else n)


def clean_gpu(name: str) -> str:
    """'NVIDIA GeForce RTX 4070' -> 'RTX 4070', 'AMD Radeon RX 6800 XT' -> 'RX 6800 XT'"""
    n = re.sub(r"\((R|TM)\)|®|™", "", name or "").strip()
    n = re.sub(r"^(NVIDIA\s+)?(GeForce\s+)?", "", n)
    n = re.sub(r"^(AMD\s+)?(Radeon\s+)(?=RX|Pro|R\d)", "", n)
    n = re.sub(r"^Intel\s+", "", n)
    n = re.sub(r"\s+(Laptop GPU|Graphics Adapter)$", "", n)
    return re.sub(r"\s+", " ", n).strip()


# --------------------------------------------------------------------------- #
# Static info
# --------------------------------------------------------------------------- #
def _linux_os() -> dict:
    rel = {}
    for line in _read("/etc/os-release").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            rel[k] = v.strip().strip('"')
    name = rel.get("NAME", "Linux")
    ver = rel.get("VERSION_ID", "")
    pretty = rel.get("PRETTY_NAME") or f"{name} {ver}".strip()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "")
    desktop = desktop.split(":")[-1].replace("X-", "")
    sess = os.environ.get("XDG_SESSION_TYPE", "")
    kernel = platform.release()
    return {"os": pretty, "os_name": name, "os_version": ver, "os_icon": "🐧",
            "kernel": kernel, "desktop": desktop.title() if desktop.islower() else desktop,
            "session": {"x11": "X11", "wayland": "Wayland"}.get(sess.lower(), sess)}


def _windows_os() -> dict:
    ver = platform.version()                       # '10.0.26100'
    build = int(ver.split(".")[-1]) if ver.split(".")[-1].isdigit() else 0
    major = "11" if build >= 22000 else platform.release()
    edition = ""
    try:
        edition = {"Professional": "Pro", "Core": "Home", "Enterprise": "Enterprise",
                   "Education": "Education", "CoreSingleLanguage": "Home"}.get(
            platform.win32_edition() or "", platform.win32_edition() or "")
    except Exception:
        pass
    disp = ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            disp = winreg.QueryValueEx(k, "DisplayVersion")[0]
    except Exception:
        pass
    os_ = " ".join(x for x in (f"Windows {major}", edition, disp) if x)
    return {"os": os_, "os_name": "Windows", "os_version": f"{major} {disp}".strip(),
            "os_icon": "🪟", "kernel": f"build {build}" if build else ver,
            "desktop": "Windows", "session": ""}


def _windows_cim() -> dict:
    """One hidden PowerShell call for the things only WMI/CIM knows."""
    ps = ("$o=@{};"
          "$o.gpus=@(Get-CimInstance Win32_VideoController | ForEach-Object {$_.Name});"
          "$b=Get-CimInstance Win32_BaseBoard; $o.board=\"$($b.Manufacturer) $($b.Product)\";"
          "$c=Get-CimInstance Win32_ComputerSystem; $o.model=\"$($c.Manufacturer) $($c.Model)\";"
          "$m=@(Get-CimInstance Win32_PhysicalMemory);"
          "$o.ram_speed=@($m | ForEach-Object {$_.ConfiguredClockSpeed})[0];"
          "$o.ram_sticks=@($m | ForEach-Object {[string]$_.Capacity});"
          "$o.ram_type=@($m | ForEach-Object {$_.SMBIOSMemoryType})[0];"
          "$o.ram_maker=@($m | ForEach-Object {$_.Manufacturer})[0];"
          "$o.bios=(Get-CimInstance Win32_BIOS).SMBIOSBIOSVersion;"
          "$o.l3=@(Get-CimInstance Win32_Processor)[0].L3CacheSize;"
          "$o.disks=@(Get-CimInstance Win32_DiskDrive | ForEach-Object "
          "{\"$($_.Model)|$($_.Size)|$($_.MediaType)\"});"
          "try{$o.sysdisk=(Get-Partition -DriveLetter $env:SystemDrive[0] -ErrorAction Stop"
          " | Get-Disk).FriendlyName}catch{};"
          "$o | ConvertTo-Json -Compress")
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=15)
    try:
        return json.loads(out)
    except ValueError:
        return {}


def _cpu_name() -> str:
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0]
        except Exception:
            return platform.processor()
    for line in _read("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor()


def _nvidia() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 4:
            gpus.append({"index": int(parts[0]), "name": parts[1],
                         "vram": f"{int(float(parts[2])) / 1024:.0f}", "driver": parts[3]})
    return gpus


def _linux_gpus() -> list[str]:
    out = _run(["lspci", "-mm"])
    names = []
    for line in out.splitlines():
        cols = re.findall(r'"([^"]*)"', line)
        if len(cols) >= 3 and re.search(r"VGA|3D|Display", cols[0]):
            vendor = cols[1].replace(" Corporation", "").replace("Advanced Micro Devices, Inc. ",
                                                                  "")
            dev = re.sub(r"^.*\[(.+)\]$", r"\1", cols[2])     # "TU104 [GeForce RTX 2080]"
            names.append(f"{vendor.split()[0]} {dev}".strip())
    return names


def fmt_size(b: float) -> str:
    if b >= 999.5e9:
        t = b / 1e12
        return f"{t:.1f} TB".replace(".0 ", " ") if t < 10 else f"{t:.0f} TB"
    return f"{b / 1e9:.0f} GB"


SMBIOS_RAM = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR", 28: "LPDDR2",
              29: "LPDDR3", 30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}

_VIRTUAL_DISK = re.compile(r"^(loop|ram|zram|dm-|sr|md|fd|nbd)")


def _linux_disks() -> list[tuple[str, str]]:
    """[(block name, 'Samsung SSD 980 1TB · 1 TB'), ...] for real, non-removable drives."""
    out = []
    for dev in sorted(Path("/sys/block").glob("*")):
        if _VIRTUAL_DISK.match(dev.name) or _read(str(dev / "removable")) == "1":
            continue
        try:
            size = int(_read(str(dev / "size")) or 0) * 512
        except ValueError:
            size = 0
        if size < 1e9:
            continue
        model = re.sub(r"\s+", " ", _read(str(dev / "device" / "model"))).strip()
        out.append((dev.name, f"{model} · {fmt_size(size)}" if model else fmt_size(size)))
    return out


def _linux_root_disk() -> str:
    """Block device name of the drive '/' lives on (follows partitions, LUKS, LVM)."""
    try:
        import psutil
        part = next((p for p in psutil.disk_partitions(all=False) if p.mountpoint == "/"), None)
    except Exception:
        return ""
    if not part or not part.device.startswith("/dev/"):
        return ""
    name = Path(os.path.realpath(part.device)).name
    for _ in range(6):
        node = Path("/sys/class/block") / name
        if not node.exists():
            return ""
        if (node / "partition").exists():
            name = Path(os.path.realpath(node)).parent.name
            continue
        slaves = list((node / "slaves").glob("*")) if (node / "slaves").exists() else []
        if slaves:
            name = slaves[0].name
            continue
        return name
    return name


def _cpu_extra(name: str, cim: dict) -> dict:
    v = {}
    low = name.lower()
    for key, maker in (("intel", "Intel"), ("amd", "AMD"), ("ryzen", "AMD"), ("apple", "Apple"),
                       ("snapdragon", "Qualcomm"), ("qualcomm", "Qualcomm")):
        if key in low:
            v["cpu_vendor"] = maker
            break
    kb = 0
    if IS_WINDOWS:
        try:
            kb = int(cim.get("l3") or 0)
        except (TypeError, ValueError):
            pass
    else:
        m = re.match(r"(\d+)\s*([KM])", _read("/sys/devices/system/cpu/cpu0/cache/index3/size"))
        if m:
            kb = int(m.group(1)) * (1024 if m.group(2) == "M" else 1)
    if kb:
        v["cpu_cache"] = f"{kb / 1024:g}"
    arch = platform.machine().lower()
    v["arch"] = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "ARM64",
                 "aarch64": "ARM64"}.get(arch, arch)
    return v


def _storage(cim: dict) -> dict:
    v = {}
    try:
        import psutil
        root = os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/"
        part = next((p for p in psutil.disk_partitions(all=False)
                     if p.mountpoint.rstrip("\\/").lower() == root.rstrip("\\/").lower()
                     or p.mountpoint == root), None)
        if part and part.fstype:
            v["disk_fs"] = part.fstype.upper() if IS_WINDOWS or part.fstype in ("ntfs", "xfs") \
                else part.fstype
    except Exception:
        pass
    if IS_WINDOWS:
        disks = cim.get("disks") or []
        if isinstance(disks, str):
            disks = [disks]
        names = []
        for d in disks:
            model, size, *_rest = (str(d).split("|") + ["", ""])[:3]
            try:
                names.append(f"{model.strip()} · {fmt_size(float(size))}" if size else model)
            except ValueError:
                names.append(model.strip())
        v["disks"] = " + ".join(n for n in names if n)
        v["disk_model"] = _clean(str(cim.get("sysdisk") or ""))
    else:
        disks = _linux_disks()
        v["disks"] = " + ".join(d for _n, d in disks)
        rd = _linux_root_disk()
        model = re.sub(r"\s+", " ", _read(f"/sys/block/{rd}/device/model")).strip() if rd else ""
        v["disk_model"] = model
    return v


def _ram_windows(cim: dict) -> dict:
    v = {}
    sticks = cim.get("ram_sticks") or []
    if isinstance(sticks, (str, int)):
        sticks = [sticks]
    try:
        sizes = [round(int(x) / 2**30) for x in sticks if str(x).isdigit()]
    except ValueError:
        sizes = []
    if sizes:
        if len(set(sizes)) == 1:
            v["ram_sticks"] = f"{len(sizes)}x{sizes[0]} GB"
        else:
            v["ram_sticks"] = " + ".join(f"{x} GB" for x in sizes)
    try:
        v["ram_type"] = SMBIOS_RAM.get(int(cim.get("ram_type") or 0), "")
    except (TypeError, ValueError):
        pass
    maker = _clean(str(cim.get("ram_maker") or ""))
    if maker and not re.fullmatch(r"[0-9A-Fa-f]{4,}", maker):    # some boards only give an ID
        v["ram_maker"] = maker
    return v


def static_info(gpu_index: int = 0, log: Callable[[str], None] = lambda m: None) -> dict:
    v: dict = {}
    try:
        v.update(_windows_os() if IS_WINDOWS else _linux_os())
    except Exception as e:
        log(f"PC specs: OS not readable ({e})")
    v["hostname"] = platform.node()
    cpu_full, cpu_short = clean_cpu(_cpu_name())
    v["cpu_name"], v["cpu_short"] = cpu_full, cpu_short
    try:
        import psutil
        v["cores"] = str(psutil.cpu_count(logical=False) or "")
        v["threads"] = str(psutil.cpu_count(logical=True) or "")
        f = psutil.cpu_freq()
        if f and f.max:
            v["cpu_max"] = f"{f.max / 1000:.1f}"
        v["ram_total"] = f"{psutil.virtual_memory().total / 2**30:.0f}"
        root = os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/"
        v["disk_total"] = f"{psutil.disk_usage(root).total / 1e9:.0f}"
    except Exception:
        pass

    nv = _nvidia()
    cim = _windows_cim() if IS_WINDOWS else {}
    names = [g["name"] for g in nv]
    others = (cim.get("gpus") or []) if IS_WINDOWS else _linux_gpus()
    if isinstance(others, str):
        others = [others]
    for o in others:                     # add non-NVIDIA cards (integrated, AMD, Intel)
        if not any(clean_gpu(o).lower() in clean_gpu(n).lower() or
                   clean_gpu(n).lower() in clean_gpu(o).lower() for n in names):
            names.append(o)
    main = next((g for g in nv if g["index"] == gpu_index), nv[0] if nv else None)
    v["gpu_name"] = re.sub(r"\((R|TM)\)", "", main["name"] if main else (names[0] if names else ""))
    v["gpu_short"] = clean_gpu(v["gpu_name"])
    v["gpus"] = " + ".join(clean_gpu(n) for n in names)
    if main:
        v["gpu_driver"] = main["driver"]
        v["vram_total"] = main["vram"]
    else:
        try:                                    # AMD: name and VRAM of the biggest card
            import gpus as G
            amd = sorted(G.amd(), key=lambda g: -g.vram_mb)
            if amd:
                v["gpu_name"] = amd[0].name
                v["gpu_short"] = clean_gpu(amd[0].name)
                v["vram_total"] = f"{amd[0].vram_mb / 1024:.0f}"
        except Exception:
            pass

    for part, fn in (("CPU", lambda: _cpu_extra(_cpu_name(), cim)),
                     ("drives", lambda: _storage(cim)),
                     ("RAM", lambda: _ram_windows(cim) if IS_WINDOWS else {})):
        try:
            v.update({k: val for k, val in fn().items() if val})
        except Exception as e:
            log(f"PC specs: {part} details not readable ({e})")

    if IS_WINDOWS:
        v["bios"] = _clean(str(cim.get("bios") or ""))
        v["board"] = _clean(str(cim.get("board", "")))
        v["pc_model"] = _clean(str(cim.get("model", "")))
        if cim.get("ram_speed"):
            v["ram_speed"] = str(cim["ram_speed"])
    else:
        d = "/sys/class/dmi/id/"
        bv, bn = _clean(_read(d + "board_vendor")), _clean(_read(d + "board_name"))
        v["board"] = f"{bv} {bn}".strip()
        bios, bdate = _clean(_read(d + "bios_version")), _read(d + "bios_date")
        v["bios"] = f"{bios} ({bdate})" if bios and bdate else bios
        sv, pn, pv = (_clean(_read(d + "sys_vendor")), _clean(_read(d + "product_name")),
                      _clean(_read(d + "product_version")))
        # Lenovo puts the readable model name in product_version
        model = pv if pv and ("lenovo" in sv.lower() or len(pn) <= 10 and pv) else pn
        v["pc_model"] = f"{sv} {model}".strip() if model and sv.lower() not in model.lower() \
            else (model or "")
    return {k: ("" if val is None else str(val)) for k, val in v.items()}


# --------------------------------------------------------------------------- #
# Live values
# --------------------------------------------------------------------------- #
def fmt_rate(bytes_per_s: float) -> str:
    if bytes_per_s >= 1e6:
        return f"{bytes_per_s / 1e6:.1f} MB/s"
    if bytes_per_s >= 1e3:
        return f"{bytes_per_s / 1e3:.0f} KB/s"
    return f"{bytes_per_s:.0f} B/s"


def _amd_live_linux() -> dict:
    """Usage, VRAM, temperature, power, fan and clock of the biggest AMD card (amdgpu)."""
    best = None
    for card in Path("/sys/class/drm").glob("card[0-9]*"):
        dev = card / "device"
        if "-" in card.name or _read(str(dev / "vendor")) != "0x1002":
            continue
        try:
            total = int(_read(str(dev / "mem_info_vram_total")) or 0)
        except ValueError:
            continue
        if best is None or total > best[1]:
            best = (dev, total)
    if best is None or best[1] < 1.5 * 2**30:
        return {}
    dev, total = best
    v = {}

    def num(path):
        try:
            return float(_read(str(path)))
        except ValueError:
            return None
    busy = num(dev / "gpu_busy_percent")
    if busy is not None:
        v["gpu"] = f"{busy:.0f}"
    used = num(dev / "mem_info_vram_used")
    if used is not None:
        v["vram_used"] = f"{used / 2**30:.1f}"
        v["vram_total"] = f"{total / 2**30:.0f}"
        v["vram"] = f"{used / total * 100:.0f}"
    hw = next(iter(sorted((dev / "hwmon").glob("hwmon*"))), None)
    if hw is not None:
        t = num(hw / "temp1_input")
        if t is not None:
            v["gpu_temp"] = f"{t / 1000:.0f}"
        p = num(hw / "power1_average")
        if p is None:
            p = num(hw / "power1_input")
        if p is not None:
            v["gpu_power"] = f"{p / 1e6:.0f}"
        cap = num(hw / "power1_cap")
        if cap is not None:
            v["gpu_power_limit"] = f"{cap / 1e6:.0f}"
        pwm, pmax = num(hw / "pwm1"), num(hw / "pwm1_max")
        if pwm is not None and pmax:
            v["gpu_fan"] = f"{pwm / pmax * 100:.0f}"
        f = num(hw / "freq1_input")
        if f is not None:
            v["gpu_clock"] = f"{f / 1e6:.0f}"
    return v


class Live:
    def __init__(self):
        import psutil
        self.ps = psutil
        psutil.cpu_percent(None)
        self._net = None
        self._last_gpu = 0.0
        self.gpu_values: dict = {}
        self._disk = None
        self._nic = ("", 0.0)
        self._wifi = ("", 0.0)

    def sample(self, gpu_index: int = 0) -> dict:
        ps = self.ps
        v = {"cpu": f"{ps.cpu_percent(None):.0f}"}
        vm = ps.virtual_memory()
        v.update(ram=f"{vm.percent:.0f}", ram_used=f"{vm.used / 2**30:.1f}",
                 ram_total=f"{vm.total / 2**30:.0f}")
        try:
            v["swap"] = f"{ps.swap_memory().percent:.0f}"
        except Exception:
            pass
        try:
            f = ps.cpu_freq()
            if f and f.current:
                v["cpu_freq"] = f"{f.current / 1000:.1f}"
        except Exception:
            pass
        try:
            temps = ps.sensors_temperatures() if hasattr(ps, "sensors_temperatures") else {}
            for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
                if temps.get(key):
                    pkg = [t for t in temps[key] if "package" in (t.label or "").lower()
                           or (t.label or "").lower() in ("tctl", "tdie")]
                    v["cpu_temp"] = f"{(pkg or temps[key])[0].current:.0f}"
                    break
        except Exception:
            pass
        try:
            root = os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/"
            du = ps.disk_usage(root)
            v.update(disk=f"{du.percent:.0f}", disk_free=f"{du.free / 1e9:.0f}",
                     disk_total=f"{du.total / 1e9:.0f}")
        except Exception:
            pass
        try:
            n = ps.net_io_counters()
            now = time.monotonic()
            if self._net:
                t0, r0, s0 = self._net
                dt = max(0.1, now - t0)
                v["net_down"] = fmt_rate((n.bytes_recv - r0) / dt)
                v["net_up"] = fmt_rate((n.bytes_sent - s0) / dt)
            self._net = (now, n.bytes_recv, n.bytes_sent)
        except Exception:
            pass
        try:
            d = ps.disk_io_counters()
            now = time.monotonic()
            if d is not None:
                if self._disk:
                    t0, r0, w0 = self._disk
                    dt = max(0.1, now - t0)
                    v["disk_read"] = fmt_rate((d.read_bytes - r0) / dt)
                    v["disk_write"] = fmt_rate((d.write_bytes - w0) / dt)
                self._disk = (now, d.read_bytes, d.write_bytes)
        except Exception:
            pass
        try:
            v["processes"] = str(len(ps.pids()))
        except Exception:
            pass
        try:
            temps = ps.sensors_temperatures() if hasattr(ps, "sensors_temperatures") else {}
            for key in ("nvme", "drivetemp"):
                if temps.get(key):
                    comp = [t for t in temps[key] if (t.label or "").lower() == "composite"]
                    v["disk_temp"] = f"{(comp or temps[key])[0].current:.0f}"
                    break
            fans = ps.sensors_fans() if hasattr(ps, "sensors_fans") else {}
            allf = [f for lst in fans.values() for f in lst if f.current > 0]
            cpuf = [f for f in allf if "cpu" in (f.label or "").lower()]
            if cpuf or allf:
                v["cpu_fan"] = str((cpuf or allf)[0].current)
        except Exception:
            pass
        try:
            v.update(self._network())
        except Exception:
            pass
        try:
            b = ps.sensors_battery()
            if b is not None:
                v["battery"] = f"{b.percent:.0f}"
                v["battery_state"] = "charging" if b.power_plugged else "on battery"
        except Exception:
            pass
        try:
            v["uptime_s"] = str(int(time.time() - ps.boot_time()))
        except Exception:
            pass
        if time.monotonic() - self._last_gpu > 5 and shutil.which("nvidia-smi"):
            self._last_gpu = time.monotonic()
            out = _run(["nvidia-smi", "-i", str(gpu_index),
                        "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total,"
                        "power.draw,fan.speed,clocks.gr,clocks.mem,power.limit,"
                        "pcie.link.gen.current,pcie.link.width.current",
                        "--format=csv,noheader,nounits"], 4)
            parts = [p.strip() for p in out.strip().split(",")]
            if len(parts) == 11:
                def num(x, fmt="{:.0f}"):
                    try:
                        return fmt.format(float(x))
                    except ValueError:
                        return ""               # "[N/A]" on some cards
                g, t, mu, mt, pw, fan, clk, mclk, plim, gen, width = parts
                self.gpu_values = {"gpu": num(g), "gpu_temp": num(t),
                                   "vram_used": num(float(mu) / 1024 if num(mu) else "x", "{:.1f}"),
                                   "vram_total": num(float(mt) / 1024 if num(mt) else "x"),
                                   "gpu_power": num(pw), "gpu_fan": num(fan), "gpu_clock": num(clk),
                                   "gpu_mem_clock": num(mclk), "gpu_power_limit": num(plim),
                                   "gpu_pcie": (f"Gen{num(gen)} x{num(width)}"
                                                if num(gen) and num(width) else "")}
                if num(mu) and num(mt) and float(mt) > 0:
                    self.gpu_values["vram"] = f"{float(mu) / float(mt) * 100:.0f}"
        elif time.monotonic() - self._last_gpu > 3 and not IS_WINDOWS and \
                not shutil.which("nvidia-smi"):
            self._last_gpu = time.monotonic()
            self.gpu_values = _amd_live_linux()
        v.update(self.gpu_values)
        return v

    _SKIP_NIC = re.compile(r"^(lo|docker|veth|virbr|br-|tun|tap|tailscale|wg|zt|vmnet|vboxnet)"
                           r"|loopback|vethernet|virtual|bluetooth|isatap|teredo", re.I)

    def _network(self) -> dict:
        ps = self.ps
        now = time.monotonic()
        nic, when = self._nic
        if not nic or now - when > 15:
            stats = ps.net_if_stats()
            counters = ps.net_io_counters(pernic=True)
            best = [(c.bytes_recv + c.bytes_sent, n) for n, c in counters.items()
                    if n in stats and stats[n].isup and not self._SKIP_NIC.search(n)]
            nic = max(best)[1] if best else ""
            self._nic = (nic, now)
        if not nic:
            return {}
        v = {}
        st = ps.net_if_stats().get(nic)
        if IS_WINDOWS:
            wifi = bool(re.search(r"wi-?fi|wlan|wireless", nic, re.I))
        else:
            wifi = (Path("/sys/class/net") / nic / "wireless").exists() or \
                (Path("/sys/class/net") / nic / "phy80211").exists()
        v["net_type"] = "Wi-Fi" if wifi else "Ethernet"
        if st and st.speed > 0:
            v["net_link"] = str(st.speed)
        if wifi:
            sig, swhen = self._wifi
            if now - swhen > 10:
                sig = ""
                if IS_WINDOWS:
                    m = re.search(r"^\s*Signal\s*:\s*(\d+)\s*%",
                                  _run(["netsh", "wlan", "show", "interfaces"], 4), re.M)
                    sig = m.group(1) if m else ""
                else:
                    for line in _read("/proc/net/wireless").splitlines():
                        if line.strip().startswith(nic + ":"):
                            q = line.split()[2].rstrip(".")
                            try:
                                sig = f"{min(100, float(q) / 70 * 100):.0f}"
                            except ValueError:
                                pass
                self._wifi = (sig, now)
            if sig:
                v["wifi_signal"] = sig
        return v


# --------------------------------------------------------------------------- #
# VR: running software (any system) and SteamVR devices (OpenVR)
# --------------------------------------------------------------------------- #
VR_PROCESSES = [   # (process name without .exe, lowercase) -> (kind, name)
    ("vrserver", "runtime", "SteamVR"), ("vrcompositor", "runtime", "SteamVR"),
    ("ovrserver_x64", "runtime", "Meta Quest Link"), ("ovrserver", "runtime", "Meta Quest Link"),
    ("virtualdesktop.streamer", "link", "Virtual Desktop"),
    ("alvr_dashboard", "link", "ALVR"), ("alvr_launcher", "link", "ALVR"),
    ("wivrn-server", "link", "WiVRn"), ("monado-service", "runtime", "Monado"),
    ("streaming assistant", "link", "PICO Connect"), ("pico connect", "link", "PICO Connect"),
    ("vrlink", "link", "Steam Link"), ("envision", "runtime", "Envision"),
]
TRACKING_NAMES = {"lighthouse": "Lighthouse", "oculus": "Meta / Oculus", "vrlink": "Steam Link",
                  "alvr": "ALVR", "alvr_server": "ALVR", "virtual_desktop": "Virtual Desktop",
                  "pico": "PICO", "htc": "HTC", "holographic": "Windows Mixed Reality"}


def vr_software() -> dict:
    try:
        import psutil
        names = {(p.info.get("name") or "").lower().removesuffix(".exe")
                 for p in psutil.process_iter(["name"])}
    except Exception:
        return {}
    v = {}
    for proc, kind, name in VR_PROCESSES:
        if proc in names:
            key = "vr_runtime" if kind == "runtime" else "vr_link"
            v.setdefault(key, name)
    if "vr_link" in v and "vr_runtime" not in v:
        v["vr_runtime"] = v["vr_link"]
    return v


def _pct(x: float) -> str:
    return f"{max(0, min(100, round(x * 100)))}"


def clean_controller(name: str) -> str:
    """'Knuckles EV3.0 Left' -> 'Index Controller', 'Miramar (Left Controller)' -> 'Quest Touch Pro'"""
    n = re.sub(r"\s*\((Left|Right)( Controller)?\)|\b(Left|Right)\b", "", name or "").strip()
    known = {"knuckles": "Index Controller", "miramar": "Quest Touch Pro",
             "oculus quest2": "Quest 2 Touch", "oculus quest 3": "Quest 3 Touch",
             "meta quest 3": "Quest 3 Touch", "vive. controller": "VIVE Wand",
             "vive controller": "VIVE Wand", "vive cosmos controller": "VIVE Cosmos Controller",
             "windowsmr": "WMR Controller", "meta quest pro": "Quest Touch Pro",
             "meta quest 2": "Quest 2 Touch", "oculus quest 2": "Quest 2 Touch",
             "oculus quest": "Quest Touch", "pico 4 ultra": "PICO 4 Ultra Controller",
             "pico 4": "PICO 4 Controller", "pico neo3": "PICO Neo3 Controller"}
    low = n.lower()
    for k, v in known.items():
        if low.startswith(k):
            return v
    return re.sub(r"\s+", " ", n)


def openvr_devices(log: Callable[[str], None] = lambda m: None) -> dict:
    """Reads headset, controllers, trackers and base stations from SteamVR - only if it
    is already running (a 'background' connection never starts SteamVR by itself)."""
    import openvr
    try:
        vr = openvr.init(openvr.VRApplication_Background)
    except Exception:
        return {}
    v: dict = {}
    try:
        S, F, B = (vr.getStringTrackedDeviceProperty, vr.getFloatTrackedDeviceProperty,
                   vr.getBoolTrackedDeviceProperty)

        def s(i, p):
            try:
                return (S(i, p) or "").strip()
            except Exception:
                return ""

        def batt(i):
            try:
                if B(i, openvr.Prop_DeviceProvidesBatteryStatus_Bool):
                    return _pct(F(i, openvr.Prop_DeviceBatteryPercentage_Float))
            except Exception:
                pass
            return ""

        trackers, tracker_batt, bases, ctrl_models = [], [], 0, []
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            cls = vr.getTrackedDeviceClass(i)
            if cls == openvr.TrackedDeviceClass_Invalid or not vr.isTrackedDeviceConnected(i):
                continue
            if cls == openvr.TrackedDeviceClass_HMD:
                model, maker = s(i, openvr.Prop_ModelNumber_String), s(i, openvr.Prop_ManufacturerName_String)
                v["hmd_model"], v["hmd_maker"] = model, maker
                v["hmd"] = model if maker.lower() in model.lower() or not maker else f"{maker} {model}"
                try:
                    hz = F(i, openvr.Prop_DisplayFrequency_Float)
                    if hz > 0:
                        v["hmd_refresh"] = f"{hz:.0f}"
                except Exception:
                    pass
                try:
                    ipd = F(i, openvr.Prop_UserIpdMeters_Float)
                    if ipd > 0:
                        v["ipd"] = f"{ipd * 1000:.1f}"
                except Exception:
                    pass
                v["hmd_battery"] = batt(i)
                try:
                    if v["hmd_battery"] and B(i, openvr.Prop_DeviceIsCharging_Bool):
                        v["hmd_charging"] = "charging"
                except Exception:
                    pass
                ts = s(i, openvr.Prop_TrackingSystemName_String).lower()
                v["vr_tracking"] = TRACKING_NAMES.get(ts, ts.title() if ts else "")
            elif cls == openvr.TrackedDeviceClass_Controller:
                role = vr.getControllerRoleForTrackedDeviceIndex(i)
                b = batt(i)
                if role == openvr.TrackedControllerRole_LeftHand:
                    v["ctrl_left_battery"] = b
                elif role == openvr.TrackedControllerRole_RightHand:
                    v["ctrl_right_battery"] = b
                m = clean_controller(s(i, openvr.Prop_ModelNumber_String))
                if m and m not in ctrl_models:
                    ctrl_models.append(m)
            elif cls == openvr.TrackedDeviceClass_GenericTracker:
                trackers.append(s(i, openvr.Prop_ModelNumber_String))
                b = batt(i)
                if b:
                    tracker_batt.append(int(b))
            elif cls == openvr.TrackedDeviceClass_TrackingReference:
                bases += 1
        l, r = v.get("ctrl_left_battery", ""), v.get("ctrl_right_battery", "")
        v["ctrl_battery"] = " · ".join(x for x in (f"L {l}%" if l else "", f"R {r}%" if r else "") if x)
        v["ctrl_model"] = " / ".join(dict.fromkeys(ctrl_models))
        if trackers:
            v["trackers"] = str(len(trackers))
            v["tracker_model"] = next((t for t in trackers if t), "")
            if len(trackers) >= 3:
                v["fbt"] = "Full body"
        if tracker_batt:
            v["tracker_battery"] = str(min(tracker_batt))
        if bases:
            v["base_stations"] = str(bases)
    finally:
        try:
            openvr.shutdown()
        except Exception:
            pass
    return v


# --------------------------------------------------------------------------- #
# Audio devices
# --------------------------------------------------------------------------- #
class AudioInfo:
    def __init__(self):
        self._pulse = None
        self._com = False

    def read(self) -> dict:
        return self._windows() if IS_WINDOWS else self._linux()

    def _linux(self) -> dict:
        import pulsectl
        if self._pulse is None:
            self._pulse = pulsectl.Pulse("chatbox-studio-info")
        p = self._pulse
        try:
            info = p.server_info()
            v = {}
            sink = next((x for x in p.sink_list() if x.name == info.default_sink_name), None)
            if sink is not None:
                v["audio_out"] = sink.description
                v["volume"] = f"{round(sink.volume.value_flat * 100)}"
                v["muted"] = "muted" if sink.mute else ""
            src = next((x for x in p.source_list() if x.name == info.default_source_name), None)
            if src is not None:
                v["audio_in"] = src.description
            return v
        except Exception:
            self._pulse = None
            raise

    def _windows(self) -> dict:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        if not self._com:
            comtypes.CoInitialize()
            self._com = True
        v = {}
        spk = AudioUtilities.GetSpeakers()
        if spk is not None:
            v["audio_out"] = spk.FriendlyName or ""
            try:
                v["volume"] = f"{round(spk.volume_percent)}"
                v["muted"] = "muted" if spk.EndpointVolume.GetMute() else ""
            except Exception:
                pass
        try:
            mic = AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone())
            v["audio_in"] = mic.FriendlyName or ""
        except Exception:
            pass
        return v


# --------------------------------------------------------------------------- #
# Wireless device batteries (Linux: UPower knows Bluetooth headsets, mice, pads, ...)
# --------------------------------------------------------------------------- #
UPOWER_TYPES = {5: "mouse", 6: "keyboard", 8: "phone", 12: "gamepad", 17: "headphones",
                19: "headphones", 18: "speakers", 26: "wearable", 21: "audio", 28: "device"}


def _upower() -> list[tuple[str, str, int]]:
    from jeepney import DBusAddress, Properties, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    conn = open_dbus_connection(bus="SYSTEM")
    found = []
    try:
        up = DBusAddress("/org/freedesktop/UPower", bus_name="org.freedesktop.UPower",
                         interface="org.freedesktop.UPower")
        paths_ = conn.send_and_get_reply(new_method_call(up, "EnumerateDevices"),
                                         timeout=2).body[0]
        for pth in paths_:
            dev = DBusAddress(pth, bus_name="org.freedesktop.UPower",
                              interface="org.freedesktop.UPower.Device")
            props = conn.send_and_get_reply(Properties(dev).get_all(), timeout=2).body[0]
            props = {k: val[1] for k, val in props.items()}
            kind = UPOWER_TYPES.get(int(props.get("Type", 0)))
            pct = float(props.get("Percentage", 0) or 0)
            if kind and pct > 0 and props.get("IsPresent", True):
                found.append((kind, (props.get("Model") or kind.title()).strip(), round(pct)))
    finally:
        conn.close()
    return found


def _bluez() -> list[tuple[str, str, int]]:
    """Bluetooth devices that report a battery (org.bluez.Battery1)."""
    from jeepney import DBusAddress, new_method_call
    from jeepney.io.blocking import open_dbus_connection
    conn = open_dbus_connection(bus="SYSTEM")
    try:
        om = DBusAddress("/", bus_name="org.bluez", interface="org.freedesktop.DBus.ObjectManager")
        reply = conn.send_and_get_reply(new_method_call(om, "GetManagedObjects"), timeout=2)
        objs = reply.body[0] if reply.body else {}
    finally:
        conn.close()
    found = []
    for _path, ifaces in objs.items():
        batt = ifaces.get("org.bluez.Battery1")
        dev = ifaces.get("org.bluez.Device1")
        if not batt or not dev or not dev.get("Connected", ("b", False))[1]:
            continue
        pct = int(batt.get("Percentage", ("y", 0))[1])
        icon = str(dev.get("Icon", ("s", ""))[1])
        name = str((dev.get("Alias") or dev.get("Name") or ("s", "Bluetooth"))[1])
        kind = {"input-mouse": "mouse", "input-keyboard": "keyboard", "input-gaming": "gamepad",
                "audio-headset": "headphones", "audio-headphones": "headphones",
                "audio-card": "speakers", "phone": "phone"}.get(icon, "device")
        if pct > 0:
            found.append((kind, name, pct))
    return found


def _sysfs_batteries() -> list[tuple[str, str, int]]:
    """Kernel driver batteries: Logitech (hidpp), DualSense/DualShock, Xbox (xpadneo), Steam..."""
    found = []
    for ps_ in Path("/sys/class/power_supply").glob("*"):
        if _read(str(ps_ / "scope")).lower() != "device":
            continue
        if _read(str(ps_ / "present")) == "0":
            continue
        cap = _read(str(ps_ / "capacity"))
        if not cap.isdigit():
            cap = {"full": "100", "high": "75", "normal": "50", "low": "20",
                   "critical": "5"}.get(_read(str(ps_ / "capacity_level")).lower(), "")
        if not cap or int(cap) <= 0:
            continue
        n = ps_.name.lower()
        model = _read(str(ps_ / "model_name"))
        kind = ("gamepad" if re.search(r"sony|ps-controller|xpadneo|xbox|nintendo|steam|"
                                       r"wiimote|gamepad|joycon", n) else
                "mouse" if "mouse" in (n + model).lower() else
                "keyboard" if "keyboard" in (n + model).lower() else
                "headphones" if "headset" in (n + model).lower() else
                "mouse" if n.startswith("hidpp") else "device")
        found.append((kind, model or kind.title(), int(cap)))
    return found


def _headsetcontrol() -> list[tuple[str, str, int]]:
    """Wireless gaming headsets (SteelSeries, HyperX, Logitech G, Corsair...) via HeadsetControl."""
    exe = shutil.which("headsetcontrol")
    if not exe:
        return []
    out = _run([exe, "-b", "-o", "json"], 4)
    try:
        data = json.loads(out)
    except ValueError:
        return []
    found = []
    for d in data.get("devices", []):
        b = d.get("battery") or {}
        lvl = b.get("level", -1)
        if isinstance(lvl, (int, float)) and lvl >= 0 and \
                str(b.get("status", "")).upper() != "BATTERY_UNAVAILABLE":
            found.append(("headphones", d.get("product") or d.get("device") or "Headset", int(lvl)))
    return found


def upower_devices() -> dict:
    """Wireless devices with a battery: UPower, BlueZ, kernel drivers and HeadsetControl."""
    if IS_WINDOWS:
        return {}
    found, errors = [], []
    for fn in (_upower, _bluez, _sysfs_batteries, _headsetcontrol):
        try:
            for kind, name, pct in fn():
                key = re.sub(r"\W+", "", name.lower())
                if not any(re.sub(r"\W+", "", n.lower()) == key for _k, n, _p in found):
                    found.append((kind, name, pct))
        except Exception as e:
            errors.append(f"{fn.__name__.strip('_')}: {type(e).__name__}")
    if not found and len(errors) == 4:
        raise RuntimeError(", ".join(errors))
    v = {"devices": " · ".join(f"{name} {pct}%" for _k, name, pct in found),
         "device_count": str(len(found)) if found else ""}
    for kind, _name, pct in found:
        v.setdefault(f"{kind}_battery", str(pct))
    return v


def vr_info(log: Callable[[str], None] = lambda m: None) -> dict:
    """Running VR software, plus device details from SteamVR, WiVRn or Monado."""
    v = vr_software()
    rt = v.get("vr_runtime", "")
    sources = []
    if rt == "SteamVR":
        sources.append(("SteamVR", lambda: openvr_devices(log)))
    if not IS_WINDOWS and ("WiVRn" in (rt, v.get("vr_link")) or rt in ("Monado", "Envision")):
        if "WiVRn" in (rt, v.get("vr_link")):
            sources.append(("WiVRn", wivrn_dbus))
        sources.append(("libmonado", monado_devices))
    for name, fn in sources:
        try:
            got = fn()
        except Exception as e:
            _log_once(log, f"vr:{name}", f"VR info from {name} not available "
                                         f"({type(e).__name__}: {e})")
            continue
        for k, val in got.items():
            if val and not v.get(k):          # first source wins (SteamVR, then WiVRn)
                v[k] = val
    if v.get("hmd"):
        v.setdefault("vr_connected", "connected")
    # Monado only says "Left Controller": name them after the headset instead
    if v.get("hmd") and re.fullmatch(r"(?i)\s*(left|right)?\s*(touch\s*)?controllers?\s*",
                                     v.get("ctrl_model", "x")):
        guess = clean_controller(v["hmd"])
        v["ctrl_model"] = guess if guess != v["hmd"] else f"{v['hmd']} controllers"
    return v


_logged_keys: set = set()


def _log_once(log, key, msg):
    if key not in _logged_keys:
        _logged_keys.add(key)
        log(msg)


# --------------------------------------------------------------------------- #
# WiVRn (D-Bus) and Monado / WiVRn devices (libmonado) - Linux OpenXR without SteamVR
# --------------------------------------------------------------------------- #
def wivrn_dbus() -> dict:
    """Headset info WiVRn publishes on the session bus (io.github.wivrn.Server)."""
    if IS_WINDOWS:
        return {}
    from jeepney import DBusAddress, Properties
    from jeepney.io.blocking import open_dbus_connection
    conn = open_dbus_connection(bus="SESSION")
    try:
        addr = DBusAddress("/io/github/wivrn/Server", bus_name="io.github.wivrn.Server",
                           interface="io.github.wivrn.Server")
        reply = conn.send_and_get_reply(Properties(addr).get_all(), timeout=2)
        if reply.header.message_type.name == "error":
            return {}
        p = {k: val[1] for k, val in reply.body[0].items()}
    finally:
        conn.close()
    v = {"vr_link": "WiVRn"}
    if p.get("HeadsetConnected"):
        v["vr_connected"] = "connected"
        name = str(p.get("SystemName") or "").strip()
        if name:
            v["hmd"] = name
            v["hmd_model"] = name
            m = re.match(r"(Meta|Oculus|Pico|PICO|HTC|Vive|Lynx|Pimax|Samsung|Apple|Play For Dream)\s+(.*)",
                         name, re.I)
            if m:
                v["hmd_maker"] = m.group(1)
                v["hmd_model"] = m.group(2).strip() or name
        hz = p.get("PreferredRefreshRate") or 0
        if hz:
            v["hmd_refresh"] = f"{float(hz):.0f}"
        rates = p.get("AvailableRefreshRates") or []
        if rates:
            v["hmd_rates"] = " / ".join(f"{float(r):.0f}" for r in sorted(set(rates)))
        br = p.get("Bitrate") or 0
        if br:
            v["vr_bitrate"] = f"{int(br) / 1e6:.0f}"
        feats = [n for k, n in (("HandTracking", "hand tracking"), ("EyeGaze", "eye tracking"),
                                ("FaceTracking", "face tracking")) if p.get(k)]
        v["vr_features"] = " · ".join(feats)
        v["hand_tracking"] = "hand tracking" if p.get("HandTracking") else ""
        v["eye_tracking"] = "eye tracking" if p.get("EyeGaze") else ""
        v["face_tracking"] = "face tracking" if p.get("FaceTracking") else ""
        codecs = [str(c).upper().replace("H265", "H.265").replace("H264", "H.264")
                  for c in (p.get("SupportedCodecs") or [])]
        if codecs:
            v["vr_codec"] = " / ".join(codecs)
        for key, rate, ch in (("vr_mic", "MicSampleRate", "MicChannels"),
                              ("vr_speakers", "SpeakerSampleRate", "SpeakerChannels")):
            if p.get(rate) and p.get(ch):
                n = int(p[ch])
                v[key] = f"{int(p[rate]) / 1000:g} kHz " + {1: "mono", 2: "stereo"}.get(n, f"{n} ch")
        if p.get("SessionRunning"):
            v["vr_session"] = "in VR"
    return v


def _active_openxr_manifests() -> list[Path]:
    dirs = [os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))]
    dirs += (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
    out = []
    for d in dirs:
        p = Path(d) / "openxr" / "1" / "active_runtime.json"
        if p.exists():
            out.append(p)
    return out


def find_libmonado() -> Optional[str]:
    """libmonado of the active OpenXR runtime (WiVRn: libmonado_wivrn.so)."""
    env = os.environ.get("LIBMONADO_PATH")
    if env and Path(env).exists():
        return env
    for manifest in _active_openxr_manifests():
        try:
            data = json.loads(manifest.read_text())
            lib = (data.get("runtime") or {}).get("MND_libmonado_path")
        except (OSError, ValueError):
            continue
        if not lib:
            continue
        for base in (manifest.parent, manifest.resolve().parent):
            cand = Path(lib) if os.path.isabs(lib) else (base / lib)
            if cand.exists():
                return str(cand)
    for cand in ("/usr/lib/wivrn/libmonado_wivrn.so", "/usr/lib64/wivrn/libmonado_wivrn.so",
                 "/usr/lib/x86_64-linux-gnu/wivrn/libmonado_wivrn.so",
                 "/usr/local/lib/wivrn/libmonado_wivrn.so", "/usr/lib/libmonado.so",
                 "/usr/lib/x86_64-linux-gnu/libmonado.so", "/usr/local/lib/libmonado.so"):
        if Path(cand).exists():
            return cand
    return None


_MND = {}


def monado_devices() -> dict:
    """Names and batteries of headset, controllers and trackers from the running
    Monado/WiVRn service. Connects as a monitoring client (like monado-ctl), never as a
    VR app, and disconnects right after reading."""
    import ctypes
    path = find_libmonado()
    if not path:
        return {}
    lib = _MND.get(path)
    if lib is None:
        lib = ctypes.CDLL(path)
        _MND[path] = lib
    root = ctypes.c_void_p()
    if lib.mnd_root_create(ctypes.byref(root)) != 0:
        return {}                              # service not running
    v: dict = {}
    try:
        count = ctypes.c_uint32()
        lib.mnd_root_get_device_count(root, ctypes.byref(count))

        def name_of(i):
            s = ctypes.c_char_p()
            if hasattr(lib, "mnd_root_get_device_info_string"):
                if lib.mnd_root_get_device_info_string(root, ctypes.c_uint32(i), 0,
                                                       ctypes.byref(s)) == 0 and s.value:
                    return s.value.decode("utf-8", "replace")
            idx = ctypes.c_uint32()
            if lib.mnd_root_get_device_info(root, ctypes.c_uint32(i), ctypes.byref(idx),
                                            ctypes.byref(s)) == 0 and s.value:
                return s.value.decode("utf-8", "replace")
            return ""

        def battery(i):
            if not hasattr(lib, "mnd_root_get_device_battery_status"):
                return None
            present, charging, charge = ctypes.c_bool(), ctypes.c_bool(), ctypes.c_float()
            r = lib.mnd_root_get_device_battery_status(root, ctypes.c_uint32(i),
                                                       ctypes.byref(present),
                                                       ctypes.byref(charging),
                                                       ctypes.byref(charge))
            if r != 0 or not present.value:
                return None
            return _pct(charge.value), bool(charging.value)

        def role(name):
            idx = ctypes.c_int32(-1)
            if lib.mnd_root_get_device_from_role(root, name.encode(), ctypes.byref(idx)) == 0:
                return idx.value
            return -1

        head, left, right = role("head"), role("left"), role("right")
        names = [name_of(i) for i in range(count.value)]
        tracker_batt, trackers = [], []
        for i, n in enumerate(names):
            b = battery(i)
            low = n.lower()
            if i == head:
                if b:
                    v["hmd_battery"] = b[0]
                    if b[1]:
                        v["hmd_charging"] = "charging"
            elif i == left:
                if b:
                    v["ctrl_left_battery"] = b[0]
            elif i == right:
                if b:
                    v["ctrl_right_battery"] = b[0]
            elif "tracker" in low or "body" in low or "feet" in low:
                trackers.append(n)
                if b:
                    tracker_batt.append(int(b[0]))
        for i in (left, right):
            if 0 <= i < len(names) and names[i]:
                m = clean_controller(re.sub(r"^(WiVRn|Monado)\s+", "", names[i]))
                v.setdefault("ctrl_model", m)
        lb, rb = v.get("ctrl_left_battery", ""), v.get("ctrl_right_battery", "")
        v["ctrl_battery"] = " · ".join(x for x in (f"L {lb}%" if lb else "",
                                                   f"R {rb}%" if rb else "") if x)
        if trackers:
            v["trackers"] = str(len(trackers))
            v["tracker_model"] = trackers[0]
            if len(trackers) >= 3:
                v["fbt"] = "Full body"
        if tracker_batt:
            v["tracker_battery"] = str(min(tracker_batt))
        if 0 <= head < len(names) and names[head]:
            v["hmd_device"] = names[head]
            if hasattr(lib, "mnd_root_get_device_info_string"):
                s = ctypes.c_char_p()
                if lib.mnd_root_get_device_info_string(root, ctypes.c_uint32(head), 1,
                                                       ctypes.byref(s)) == 0 and s.value:
                    v["hmd_serial"] = s.value.decode("utf-8", "replace")
    finally:
        lib.mnd_root_destroy(ctypes.byref(root))
    return v
