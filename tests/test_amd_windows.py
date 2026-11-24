"""Windows + AMD: which card runs what (python -m pytest tests/test_amd_windows.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import components as K  # noqa: E402
import devices  # noqa: E402
import gpus as G  # noqa: E402

RX6600 = 0x73FF   # gfx1032: not in the ROCm build
RX7800 = 0x747E   # gfx1101: in the ROCm build


def hw_with(device_id, rocm_listed):
    hw = K.Hardware(cores=8)
    if rocm_listed:
        hw.gpus.append(G.GPU(0, "AMD Radeon RX 7800 XT", 16384, "amd", arch="gfx1101",
                             maker="amd", device_id=device_id))
    hw.gpus.append(G.GPU(0, "AMD Radeon RX", 8192, "vulkan", maker="amd", device_id=device_id))
    return hw


def windows(monkeypatch, dml=True, rocm=False):
    monkeypatch.setattr(K, "CONFIG", Path("/nonexistent/settings.json"))
    monkeypatch.setattr(K, "IS_WINDOWS", True)
    monkeypatch.setattr(G, "IS_WINDOWS", True)
    monkeypatch.setattr(devices.paths, "IS_WINDOWS", True)
    monkeypatch.setattr(devices, "directml_installed", lambda: dml)
    monkeypatch.setattr(devices, "rocm_installed", lambda: rocm)


def test_rocm_table(monkeypatch):
    windows(monkeypatch)
    assert G.AMD_IDS["73ff"][1] == "gfx1032" and G.AMD_IDS["67df"][1] == "gfx803"
    assert not G.rocm_ok("gfx1032") and not G.rocm_ok("") and not G.rocm_ok("gfx906")
    assert G.rocm_ok("gfx1101") and G.rocm_ok("gfx1201")


def test_new_setup_uses_vulkan_and_directml(monkeypatch):
    windows(monkeypatch)
    for did, listed in ((RX6600, False), (RX7800, True)):
        c = K.preset(hw_with(did, listed), "standard")
        assert c.gpu == "vulkan:0", c.gpu
        assert "vulkan" in c.groups and "rocm" not in c.groups
        assert c.spec("translator") == "vulkan:0"          # DirectML, not the CPU
        assert K.uses_directml(c)
        assert "vulkan" in c.vendors()


def test_linux_translator_on_vulkan_stays_cpu(monkeypatch):
    monkeypatch.setattr(K, "IS_WINDOWS", False)
    c = K.Choice(groups={"core", "vulkan"}, gpu="vulkan:0", nllb="nllb-200-distilled-600M")
    assert c.spec("translator") == "cpu" and not K.uses_directml(c)


def test_broken_rocm_setup_moves_to_vulkan(monkeypatch):
    windows(monkeypatch, rocm=False)
    # 1.32 and older: ROCm failed, the setup dropped the rocm part, gpu stayed amd:0
    state = {"groups": ["core", "media"], "gpu": "amd:0", "nllb": "nllb-200-distilled-600M",
             "whisper": "large-v3"}
    for did, listed in ((RX6600, False), (RX7800, True)):
        c = K.recommend(hw_with(did, listed), state)
        assert c.gpu == "vulkan:0" and "vulkan" in c.groups, (did, c)
        assert c.spec("speech") == "vulkan:0" and c.spec("translator") == "vulkan:0"


def test_working_rocm_setup_stays(monkeypatch):
    windows(monkeypatch, rocm=True)
    state = {"groups": ["core", "rocm"], "gpu": "amd:0", "nllb": "nllb-200-distilled-600M"}
    c = K.recommend(hw_with(RX7800, True), state)
    assert c.gpu == "amd:0" and "rocm" in c.groups


def test_plan_installs_directml(monkeypatch):
    windows(monkeypatch, dml=False)
    import nllb_onnx
    monkeypatch.setattr(nllb_onnx, "is_installed", lambda name: False)
    hw = hw_with(RX6600, False)
    c = K.preset(hw, "standard")
    labels = [s.label for s in K.plan(hw, c, K.Choice())]
    assert any("DirectML (the translator" in s for s in labels), labels
    assert any("translator for DirectML" in s for s in labels), labels


def test_dml_index_matches_by_device_id(monkeypatch):
    windows(monkeypatch)
    vk = [G.GPU(0, "AMD Radeon RX 6600", 8192, "vulkan", maker="amd", device_id=RX6600)]
    monkeypatch.setattr(G, "vulkan", lambda: vk)
    monkeypatch.setattr(G, "dxgi_adapters", lambda: [
        {"index": 0, "name": "Intel(R) UHD Graphics 770", "vendor_id": 0x8086, "device_id": 0x4680,
         "vram_mb": 128, "software": False},
        {"index": 1, "name": "AMD Radeon RX 6600", "vendor_id": 0x1002, "device_id": RX6600,
         "vram_mb": 8176, "software": False},
        {"index": 2, "name": "Microsoft Basic Render Driver", "vendor_id": 0x1414,
         "device_id": 0x8c, "vram_mb": 0, "software": True}])
    assert devices.dml_index("vulkan:0") == 1
    assert devices.dml_index("vulkan:5") == -1


def test_vulkan_probe_fails_windows_lists_card_anyway(monkeypatch):
    windows(monkeypatch)
    import subprocess

    class R:
        returncode, stdout, stderr = 1, "", "OSError: [WinError 126] vulkan-1.dll"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(G.paths, "hidden_subprocess_kwargs", lambda: {})
    monkeypatch.setattr(G, "_vk_cache", None)
    monkeypatch.setattr(G, "dxgi_adapters", lambda: [
        {"index": 0, "name": "AMD Radeon RX 6600", "vendor_id": 0x1002, "device_id": RX6600,
         "vram_mb": 8176, "shared_mb": 16000, "software": False},
        {"index": 1, "name": "Microsoft Basic Render Driver", "vendor_id": 0x1414,
         "device_id": 0x8C, "vram_mb": 0, "shared_mb": 16000, "software": True}])
    vk = G.vulkan()
    assert [(g.index, g.name, g.maker, g.vram_mb) for g in vk] == \
        [(0, "AMD Radeon RX 6600", "amd", 8176)]
    assert "WinError 126" in G.vulkan_note and "adapter list" in G.vulkan_note
    hw = K.Hardware(cores=8, gpus=vk)
    c = K.preset(hw, "standard")
    assert c.gpu == "vulkan:0" and c.spec("translator") == "vulkan:0"


RX9070 = 0x7550


def hw_9070():
    return K.Hardware(cores=8, ram_gb=32, gpus=[
        G.GPU(0, "AMD Radeon RX 9070 XT", 16304, "amd", arch="gfx1201", maker="amd",
              device_id=RX9070),
        G.GPU(0, "AMD Radeon RX 9070 XT", 16304, "vulkan", maker="amd", device_id=RX9070),
        G.GPU(1, "AMD Radeon(TM) Graphics", 10805, "vulkan", maker="amd", integrated=True,
              device_id=0x13C0)])


def test_cpu_install_from_missed_card_moves_to_it(monkeypatch):
    windows(monkeypatch)
    # 1.38: the card wasn't found, so the setup saved everything on the CPU
    state = {"groups": ["core", "media", "vr"], "gpu": "cpu", "whisper": "small",
             "nllb": "nllb-200-distilled-600M", "tr_dev": "same"}
    c = K.recommend(hw_9070(), state)
    assert c.gpu == "vulkan:0" and "vulkan" in c.groups, c
    assert c.whisper == "large-v3" and c.nllb == "nllb-200-distilled-1.3B"
    assert c.spec("translator") == "vulkan:0"


def test_cpu_chosen_with_the_card_listed_stays(monkeypatch):
    windows(monkeypatch)
    state = {"groups": ["core"], "gpu": "cpu", "whisper": "small", "nllb": None,
             "gpus_seen": ["amd:0", "vulkan:0", "vulkan:1"]}
    c = K.recommend(hw_9070(), state)
    assert c.gpu == "cpu" and "vulkan" not in c.groups
