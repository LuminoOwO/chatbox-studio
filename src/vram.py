"""
Live graphics memory use per card: how much is in use right now, by which programs, and
how much is realistically left for Chatbox Studio (e.g. while VRChat runs).

    measure(gpus) -> {"nvidia:0": Usage, "amd:0": Usage}

Sources:
    NVIDIA          nvidia-smi (totals; processes on Linux)
    AMD, Linux      /sys/class/drm/.../mem_info_vram_used, processes from /proc/*/fdinfo
    Windows, all    the "GPU Adapter Memory" / "GPU Process Memory" performance counters
                    (the numbers Task Manager shows), matched to cards through DXGI
Chatbox Studio's own processes count as free: their models are what the setup plans.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import paths

IS_WINDOWS = paths.IS_WINDOWS
MB = 2 ** 20


@dataclass
class Usage:
    total_mb: int = 0
    used_mb: int = 0                 # whole card, all programs (incl. the desktop)
    ours_mb: int = 0                 # Chatbox Studio itself (already running)
    procs: list = field(default_factory=list)      # [(name, MB)] biggest first, not ours
    ok: bool = False                 # measured at all
    util: Optional[int] = None       # % busy, whole card (like Task Manager)
    ours_util: Optional[int] = None  # % of it from Chatbox Studio (Windows)
    temp: Optional[int] = None       # °C

    @property
    def others_mb(self) -> int:
        return max(0, self.used_mb - self.ours_mb)

    @property
    def free_mb(self) -> int:
        """What Chatbox Studio can realistically use next to everything else."""
        return max(0, self.total_mb - self.others_mb)

    def vrchat_mb(self) -> int:
        return sum(mb for n, mb in self.procs if is_vrchat(n))


def is_vrchat(name: str) -> bool:
    return Path(name.replace("\\", "/")).name.lower().startswith("vrchat")


def _run(cmd, timeout=5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              **paths.hidden_subprocess_kwargs()).stdout
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Which processes are Chatbox Studio
# --------------------------------------------------------------------------- #
def _our_roots() -> list[str]:
    roots = {str(paths.data_dir()), str(Path(__file__).resolve().parent)}
    return [r.lower() for r in roots if r]


def _is_ours(pid: int, cmdline: str) -> bool:
    if pid == os.getpid():
        return True
    c = cmdline.replace("\\", "/").lower()
    return any(r.replace("\\", "/") in c for r in _our_roots())


def _linux_cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", "replace").strip()
    except OSError:
        return ""


def _linux_name(pid: int, cmdline: str = "") -> str:
    try:
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        comm = ""
    # Proton / Wine games: the .exe is in the command line, comm is often cut at 15 chars
    m = re.search(r"([^/\\\s]+\.exe)\b", cmdline or _linux_cmdline(pid), re.I)
    if m:
        return m.group(1)
    return comm or f"pid {pid}"


def vrchat_running() -> bool:
    if IS_WINDOWS:
        out = _run(["tasklist", "/FI", "IMAGENAME eq VRChat.exe", "/NH"])
        return "vrchat.exe" in out.lower()
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        try:
            if "vrchat" in (d / "comm").read_text().lower():
                return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- #
# NVIDIA
# --------------------------------------------------------------------------- #
_SMI_PROC = re.compile(r"^\|\s+(\d+)(?:\s+(?:N/A|\d+)\s+(?:N/A|\d+))?\s+(\d+)\s+(C\+G|C|G)\s+"
                       r"(.+?)\s+(\d+)\s*MiB\s*\|")


def _nvidia(out: dict, gpus: list):
    exe = shutil.which("nvidia-smi")
    nv = [g for g in gpus if g.vendor == "nvidia"]
    if not exe or not nv:
        return
    txt = _run([exe, "--query-gpu=index,memory.used,memory.total,utilization.gpu,"
                     "temperature.gpu", "--format=csv,noheader,nounits"])
    for line in txt.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        try:
            idx, used, total = int(p[0]), int(float(p[1])), int(float(p[2]))
        except (ValueError, IndexError):
            continue
        u = Usage(total, used, ok=True)
        u.util = _int(p[3] if len(p) > 3 else "")
        u.temp = _int(p[4] if len(p) > 4 else "")
        out[f"nvidia:{idx}"] = u
    if IS_WINDOWS:
        return                      # per-program numbers come from the counters below
    procs = parse_smi_processes(_run([exe]))
    for idx, pid, name, mb in procs:
        u = out.get(f"nvidia:{idx}")
        if u is None:
            continue
        cmd = _linux_cmdline(pid)
        if _is_ours(pid, cmd or name):
            u.ours_mb += mb
        else:
            u.procs.append((_linux_name(pid, cmd) if cmd else Path(name).name, mb))


def _int(v) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def parse_smi_processes(text: str) -> list:
    """[(gpu index, pid, name, MB)] from nvidia-smi's process table."""
    res = []
    for line in text.splitlines():
        m = _SMI_PROC.match(line)
        if m:
            res.append((int(m.group(1)), int(m.group(2)), m.group(4).strip(),
                        int(m.group(5))))
    return res


# --------------------------------------------------------------------------- #
# AMD on Linux
# --------------------------------------------------------------------------- #
def _bus_of(g) -> str:
    """PCI address of an AMD chip: known (ROCm list) or found by its device id (Vulkan)."""
    if getattr(g, "bus", ""):
        return g.bus
    did = getattr(g, "device_id", 0)
    if not did:
        return ""
    for dev in Path("/sys/bus/pci/devices").glob("*"):
        try:
            if (dev / "vendor").read_text().strip() == "0x1002" and \
                    int((dev / "device").read_text().strip(), 16) == did:
                return dev.name
        except (OSError, ValueError):
            continue
    return ""


def _amd_linux(out: dict, gpus: list):
    amd = [(g, _bus_of(g)) for g in gpus
           if g.vendor == "amd" or getattr(g, "maker", "") == "amd"]
    amd = [(g, b) for g, b in amd if b]
    if not amd:
        return
    by_bus = {}
    for g, bus in amd:
        dev = Path("/sys/bus/pci/devices") / bus
        try:
            used = int((dev / "mem_info_vram_used").read_text()) // MB
            total = int((dev / "mem_info_vram_total").read_text()) // MB
        except (OSError, ValueError):
            continue
        u = Usage(total, used, ok=True)
        try:
            u.util = int((dev / "gpu_busy_percent").read_text())
        except (OSError, ValueError):
            pass
        for t in dev.glob("hwmon/hwmon*/temp1_input"):        # "edge" temperature
            try:
                u.temp = int(t.read_text()) // 1000
                break
            except (OSError, ValueError):
                pass
        out[f"{g.vendor}:{g.index}"] = u
        by_bus[bus.lower()] = u
    if by_bus:
        for pid, bus, mb in fdinfo_vram():
            u = by_bus.get(bus)
            if u is None:
                continue
            cmd = _linux_cmdline(pid)
            if _is_ours(pid, cmd):
                u.ours_mb += mb
            else:
                u.procs.append((_linux_name(pid, cmd), mb))


def fdinfo_vram(proc: Path = Path("/proc")) -> list:
    """[(pid, pci bus, MB)] from the DRM fdinfo of every readable process (amdgpu)."""
    res = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        seen = {}
        try:
            fds = list((d / "fdinfo").iterdir())
        except OSError:
            continue
        for f in fds:
            try:
                txt = f.read_text()
            except OSError:
                continue
            if "-vram" not in txt:
                continue
            kv = dict(re.findall(r"^([\w-]+):\s*(.+?)\s*$", txt, re.M))
            client = kv.get("drm-client-id", f.name)
            raw = kv.get("drm-memory-vram") or kv.get("drm-resident-vram") or \
                kv.get("drm-total-vram") or "0"          # older / newer kernels
            m = re.match(r"(\d+)\s*(\w*)", raw)
            if not m:
                continue
            n = int(m.group(1)) * {"KiB": 1024, "MiB": MB, "": 1}.get(m.group(2), 1024)
            seen[(kv.get("drm-pdev", "").lower(), client)] = n
        per_bus = {}
        for (bus, _c), n in seen.items():
            per_bus[bus] = per_bus.get(bus, 0) + n
        for bus, n in per_bus.items():
            if n >= 16 * MB:
                res.append((int(d.name), bus, n // MB))
    return res


# --------------------------------------------------------------------------- #
# Windows: performance counters + DXGI
# --------------------------------------------------------------------------- #
def _dxgi_adapters() -> list:
    """[(description, vendor id, dedicated MB, 'luid_0x..._0x...')]"""
    import ctypes
    from ctypes import wintypes

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint),
                    ("DeviceId", ctypes.c_uint), ("SubSysId", ctypes.c_uint),
                    ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                    ("DedicatedSystemMemory", ctypes.c_size_t),
                    ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID),
                    ("Flags", ctypes.c_uint)]

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                    ("d", ctypes.c_ubyte * 8)]

    def com(obj, idx, *argtypes):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[idx])

    iid = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(
        0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))          # IDXGIFactory1
    factory = ctypes.c_void_p()
    if ctypes.windll.dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
        return []
    res = []
    try:
        for i in range(16):
            ad = ctypes.c_void_p()
            if com(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                    factory, i, ctypes.byref(ad)) != 0:              # EnumAdapters1
                break
            d = DESC1()
            if com(ad, 10, ctypes.POINTER(DESC1))(ad, ctypes.byref(d)) == 0:   # GetDesc1
                if not d.Flags & 2:                                  # not the software one
                    luid = "luid_0x%08x_0x%08x" % (d.AdapterLuid.HighPart & 0xFFFFFFFF,
                                                   d.AdapterLuid.LowPart)
                    res.append((d.Description, d.VendorId,
                                int(d.DedicatedVideoMemory // MB), luid))
            com(ad, 2)(ad)                                           # Release
    finally:
        com(factory, 2)(factory)
    return res


class _Pdh:
    """One performance counter query kept open: rate counters (GPU engine utilization)
    need two samples, so the first read of those is empty."""
    PDH_FMT_LARGE, PDH_FMT_DOUBLE = 0x00000400, 0x00000200

    def __init__(self, paths_: list):
        import ctypes
        self.ct = ctypes
        self.pdh = ctypes.windll.pdh
        self.q = ctypes.c_void_p()
        self.counters = {}
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.q)) != 0:
            raise OSError("PdhOpenQuery failed")
        for p in paths_:
            c = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.q, ctypes.c_wchar_p(p), None,
                                              ctypes.byref(c)) == 0:
                self.counters[p] = c

    def collect(self) -> bool:
        return self.pdh.PdhCollectQueryData(self.q) == 0

    def read(self, path: str, double: bool = False) -> dict:
        ctypes = self.ct
        from ctypes import wintypes
        c = self.counters.get(path)
        if c is None:
            return {}

        class VALUE(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD),
                        ("v", ctypes.c_double if double else ctypes.c_longlong)]

        class ITEM(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", VALUE)]
        fmt = self.PDH_FMT_DOUBLE if double else self.PDH_FMT_LARGE
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        self.pdh.PdhGetFormattedCounterArrayW(c, fmt, ctypes.byref(size), ctypes.byref(count),
                                              None)
        if not size.value:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(c, fmt, ctypes.byref(size),
                                                 ctypes.byref(count), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(ITEM * count.value))[0]
        return {it.szName: it.FmtValue.v for it in items
                if it.szName and it.FmtValue.CStatus in (0, 1)}


P_ADAPTER = r"\GPU Adapter Memory(*)\Dedicated Usage"
P_PROCESS = r"\GPU Process Memory(*)\Dedicated Usage"
P_ENGINE = r"\GPU Engine(*)\Utilization Percentage"
_pdh_query: Optional[_Pdh] = None


def _pdh_now() -> Optional[_Pdh]:
    global _pdh_query
    if _pdh_query is None:
        _pdh_query = _Pdh([P_ADAPTER, P_PROCESS, P_ENGINE])
        _pdh_query.collect()                 # first sample for the rate counter
    return _pdh_query if _pdh_query.collect() else None


def parse_engine_counters(values: dict) -> dict:
    """{luid: {"all": %, pid: %}}: like Task Manager, a card's load is its busiest engine
    (3D, compute, copy, video...), summed over the programs using that engine."""
    per: dict = {}
    for inst, v in values.items():
        m = re.match(r"pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+_eng_(\d+)", inst,
                     re.I)
        if not m or v <= 0:
            continue
        pid, luid, eng = int(m.group(1)), m.group(2).lower(), m.group(3)
        per.setdefault(luid, {}).setdefault(eng, {})
        per[luid][eng][pid] = per[luid][eng].get(pid, 0.0) + v
    res = {}
    for luid, engines in per.items():
        totals = {e: sum(p.values()) for e, p in engines.items()}
        busiest = max(totals, key=totals.get)
        d = {"all": min(100, round(totals[busiest]))}
        for e in engines.values():                 # each program: its busiest engine
            for pid, v in e.items():
                d[pid] = max(d.get(pid, 0.0), v)
        res[luid] = d
    return res


def _win_exe(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        k.CloseHandle(h)


def match_adapters(gpus: list, adapters: list) -> dict:
    """{gpu key: luid}: by name first, then by vendor and order."""
    VENDOR = {"nvidia": 0x10DE, "amd": 0x1002, "intel": 0x8086}
    left = list(adapters)
    res = {}
    for g in gpus:
        cand = [a for a in left if a[1] == VENDOR.get(getattr(g, "maker", "") or g.vendor)]
        hit = next((a for a in cand if a[0].strip().lower() == g.name.strip().lower()), None) \
            or next((a for a in cand if abs(a[2] - g.vram_mb) < 600), None) \
            or (cand[0] if cand else None)
        if hit:
            res[f"{g.vendor}:{g.index}"] = hit[3]
            left.remove(hit)
    return res


def parse_process_counters(values: dict) -> dict:
    """{luid: [(pid, MB)]} from '\\GPU Process Memory(*)\\Dedicated Usage' instances."""
    res: dict = {}
    for inst, v in values.items():
        m = re.match(r"pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+", inst, re.I)
        if m and v >= 16 * MB:
            res.setdefault(m.group(2).lower(), []).append((int(m.group(1)), v // MB))
    return res


def _windows(out: dict, gpus: list):
    try:
        luids = match_adapters(gpus, _dxgi_adapters())
    except Exception:
        luids = {}
    if not luids:
        return
    try:
        q = _pdh_now()
        if q is None:
            return
        adapters = q.read(P_ADAPTER)
        procs = parse_process_counters(q.read(P_PROCESS))
        engines = parse_engine_counters(q.read(P_ENGINE, double=True))
    except Exception:
        return
    for g in gpus:
        key = f"{g.vendor}:{g.index}"
        luid = luids.get(key)
        if not luid:
            continue
        used = sum(v for inst, v in adapters.items() if inst.lower().startswith(luid)) // MB
        u = out.get(key) or Usage(g.vram_mb, 0)
        if used or key not in out:
            u.used_mb = used
        u.total_mb = u.total_mb or g.vram_mb
        u.ok = True
        exes = {}
        for pid, mb in procs.get(luid, []):
            exe = exes[pid] = _win_exe(pid)
            if exe and _is_ours(pid, exe):
                u.ours_mb += mb
            else:
                u.procs.append((Path(exe).name if exe else f"pid {pid}", mb))
        eng = engines.get(luid)
        if eng is not None:
            if u.util is None:
                u.util = eng["all"]
            ours = 0.0
            for pid, v in eng.items():
                if pid == "all":
                    continue
                exe = exes[pid] if pid in exes else _win_exe(pid)
                if exe and _is_ours(pid, exe):
                    ours += v
            u.ours_util = min(100, round(ours))
        out[key] = u


# --------------------------------------------------------------------------- #
def measure(gpus: list) -> dict:
    out: dict = {}
    for fn in (_nvidia, _windows if IS_WINDOWS else _amd_linux):
        try:
            fn(out, gpus)
        except Exception as e:                       # never break the setup over this
            print(f"vram: {type(e).__name__}: {e}", file=sys.stderr)
    for u in out.values():
        merged: dict = {}
        for n, mb in u.procs:
            merged[n] = merged.get(n, 0) + mb
        u.procs = sorted(merged.items(), key=lambda x: -x[1])
        u.ours_mb = min(u.ours_mb, u.used_mb)
    return out



if __name__ == "__main__":
    import gpus as G
    for k, u in measure(G.nvidia() + G.amd()).items():
        print(k, u)
