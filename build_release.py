#!/usr/bin/env python3
"""Builds the release packages:
    dist/Chatbox-Studio-<version>-Windows-Setup.exe     Windows setup (needs makensis: nsis)
    dist/Chatbox-Studio-<version>-Windows.zip           the same, as a ZIP with Install.bat
    dist/Chatbox-Studio-<version>-Linux.zip             any distribution, install.sh
    dist/chatbox-studio_<version>_amd64.deb             Debian, Ubuntu, Mint, Pop!_OS, ...
    dist/chatbox-studio-<version>-1.x86_64.rpm          Fedora, openSUSE, RHEL/Alma/Rocky, ...
    dist/chatbox-studio-<version>-1-x86_64.pkg.tar.zst  Arch, Manjaro, EndeavourOS, CachyOS
Tools: dpkg-deb (.deb), rpmbuild (.rpm), bsdtar + zstd (Arch). A missing tool skips its
package with a note.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (SRC / "version.py").read_text()).group(1)
DIST = ROOT / "dist"

SKIP = {"__pycache__", ".pytest_cache"}
LINUX_ONLY = {"bootstrap.py", "install_rocm.sh"}       # not shipped in the Windows packages
CRLF = {".bat", ".ps1", ".txt"}            # Windows text files need CRLF line endings
EXEC = {".sh"}


def app_files(windows: bool = False):
    for p in sorted(SRC.rglob("*")):
        if p.is_file() and not (set(p.parts) & SKIP) and p.suffix != ".pyc":
            if windows and p.name in LINUX_ONLY:
                continue
            yield p, Path("app") / p.relative_to(SRC)


def add(z: zipfile.ZipFile, src: Path, arc: str, windows: bool):
    data = src.read_bytes()
    if windows and src.suffix.lower() in CRLF:
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    info = zipfile.ZipInfo(arc, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = 0o755 if src.suffix in EXEC else 0o644
    info.external_attr = (0o100000 | mode) << 16
    z.writestr(info, data)


def build(platform: str, extra: list[tuple[Path, str]]) -> Path:
    name = f"Chatbox-Studio-{VERSION}-{platform}"
    out = DIST / f"{name}.zip"
    windows = platform == "Windows"
    with zipfile.ZipFile(out, "w") as z:
        for src, rel in app_files(windows):
            add(z, src, f"{name}/{rel.as_posix()}", windows)
        for src, rel in extra:
            add(z, src, f"{name}/{rel}", windows)
    return out


def build_exe(extra: list[tuple[Path, str]]) -> Path | None:
    """The Windows setup program (NSIS). Same files as the ZIP, in a self-extracting exe."""
    nsis = shutil.which("makensis")
    if not nsis:
        print("makensis not found (apt install nsis) - skipping the setup .exe")
        return None
    out = DIST / f"Chatbox-Studio-{VERSION}-Windows-Setup.exe"
    with tempfile.TemporaryDirectory() as tmp:
        payload = Path(tmp) / "payload"
        files = list(app_files(True)) + [(src, Path(rel)) for src, rel in extra]
        for src, rel in files:
            dst = payload / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
            if src.suffix.lower() in CRLF:
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            dst.write_bytes(data)
        r = subprocess.run([nsis, "-V2", f"-DVERSION={VERSION}", f"-DPAYLOAD={payload}",
                            f"-DOUTFILE={out}", f"-DICON={SRC / 'assets' / 'icon.ico'}",
                            str(ROOT / "windows" / "setup.nsi")], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"makensis failed:\n{r.stdout}\n{r.stderr}")
    return out


# --------------------------------------------------------------------------- #
# Linux system packages
# --------------------------------------------------------------------------- #
PKG = "chatbox-studio"
SUMMARY = "VRChat chatbox studio: local speech recognition, translation and live info"
DESCRIPTION = ("Speak and your words appear in the VRChat chatbox, translated if you like. "
               "Speech recognition (Whisper) and translation (NLLB) run on your own PC - on "
               "NVIDIA (CUDA), AMD (ROCm or Vulkan), Intel and integrated graphics (Vulkan) "
               "or the CPU. Plus now playing, lyrics, clocks, PC and VR headset stats.")
URL = "https://github.com/LuminoOwO/chatbox-studio"
# Runtime libraries of the app (Qt, audio, GPU detection), per packaging family
DEB_DEPENDS = ("python3 (>= 3.8), python3-tk, libportaudio2, libxcb-cursor0, libpulse0, "
               "pulseaudio-utils | pipewire-pulse, pciutils, libvulkan1, libgl1, libegl1, "
               "libxkbcommon-x11-0, libfontconfig1, libdbus-1-3")
DEB_RECOMMENDS = "mesa-vulkan-drivers, python3-venv"
RPM_REQUIRES = ["python3", "(python3-tkinter or python3-tk)", "(portaudio or libportaudio2)",
                "(xcb-util-cursor or libxcb-cursor0)", "(pulseaudio-libs or libpulse0)",
                "pciutils", "(vulkan-loader or libvulkan1)", "(libxkbcommon-x11 or "
                "libxkbcommon-x11-0)", "fontconfig"]
RPM_RECOMMENDS = ["(mesa-vulkan-drivers or libvulkan_radeon)"]
ARCH_DEPENDS = ["python", "tk", "portaudio", "xcb-util-cursor", "libpulse", "pciutils",
                "vulkan-icd-loader", "libxkbcommon-x11", "fontconfig"]
ARCH_OPTDEPENDS = ["vulkan-radeon: speech recognition on AMD graphics (Vulkan)",
                   "vulkan-intel: speech recognition on Intel graphics (Vulkan)",
                   "nvidia-utils: NVIDIA graphics cards (CUDA)"]

DESKTOP = """[Desktop Entry]
Type=Application
Name=Chatbox Studio
GenericName=VRChat chatbox and voice translator
Comment=Speech recognition, translation, music and status for your VRChat chatbox
Exec=chatbox-studio
Icon=chatbox-studio
Terminal=false
Categories=AudioVideo;Utility;
Keywords=VRChat;OSC;chatbox;translator;speech;
StartupWMClass=chatbox-studio
"""
DESKTOP_SETUP = """[Desktop Entry]
Type=Application
Name=Chatbox Studio Setup
Comment=Add or remove parts of Chatbox Studio (speech models, translator, graphics support)
Exec=chatbox-studio-setup
Icon=chatbox-studio
Terminal=false
Categories=Settings;Utility;
"""


def stage_linux(root: Path):
    """The installed file tree (shared by .deb, .rpm and Arch)."""
    opt = root / "opt" / PKG
    for src, rel in app_files():
        dst = opt / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        dst.chmod(0o755 if src.suffix in EXEC or src.name == "bootstrap.py" else 0o644)
    shutil.copy2(ROOT / "README.md", opt / "README.md")
    shutil.copy2(ROOT / "docs" / "CHANGELOG.md", opt / "CHANGELOG.md")
    shutil.copy2(ROOT / "linux" / "setup_translator.sh", opt / "setup_translator.sh")
    (opt / "setup_translator.sh").chmod(0o755)
    (opt / "bin").mkdir(parents=True, exist_ok=True)
    launcher = opt / "bin" / PKG
    shutil.copy2(ROOT / "linux" / "package" / PKG, launcher)
    launcher.chmod(0o755)
    (opt / "VERSION").write_text(VERSION + "\n")
    ub = root / "usr" / "bin"
    ub.mkdir(parents=True, exist_ok=True)
    for tool in (PKG, f"{PKG}-setup"):
        (ub / tool).symlink_to(f"/opt/{PKG}/bin/{PKG}")
    apps = root / "usr" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / f"{PKG}.desktop").write_text(DESKTOP)
    (apps / f"{PKG}-setup.desktop").write_text(DESKTOP_SETUP)
    icons = root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / "assets" / "icon.png", icons / f"{PKG}.png")
    doc = root / "usr" / "share" / "doc" / PKG
    doc.mkdir(parents=True, exist_ok=True)
    (doc / "copyright").write_text(f"Chatbox Studio\nSource: {URL}\n")
    for d in [root] + [p for p in root.rglob("*") if p.is_dir()]:
        d.chmod(0o755)


POSTINST = """#!/bin/sh
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
exit 0
"""


def installed_kb(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()) // 1024


def build_deb() -> Path | None:
    tool = shutil.which("dpkg-deb")
    if not tool:
        print("dpkg-deb not found - skipping the .deb")
        return None
    out = DIST / f"{PKG}_{VERSION}_amd64.deb"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        stage_linux(root)
        deb = root / "DEBIAN"
        deb.mkdir()
        (deb / "control").write_text(
            f"Package: {PKG}\nVersion: {VERSION}\nArchitecture: amd64\n"
            f"Maintainer: Chatbox Studio <noreply@github.com>\nSection: sound\nPriority: optional\n"
            f"Installed-Size: {installed_kb(root)}\nDepends: {DEB_DEPENDS}\n"
            f"Recommends: {DEB_RECOMMENDS}\nHomepage: {URL}\n"
            f"Description: {SUMMARY}\n {DESCRIPTION}\n .\n The first start downloads the "
            "setup window into ~/.local/share/chatbox-studio;\n there you pick the speech "
            "model, translator and graphics support.\n")
        for name in ("postinst", "postrm"):
            (deb / name).write_text(POSTINST)
            (deb / name).chmod(0o755)
        r = subprocess.run([tool, "--root-owner-group", "-Zxz", "--build", str(root), str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"dpkg-deb failed:\n{r.stderr}")
    return out


def build_rpm() -> Path | None:
    tool = shutil.which("rpmbuild")
    if not tool:
        print("rpmbuild not found - skipping the .rpm")
        return None
    with tempfile.TemporaryDirectory() as tmp:
        top = Path(tmp)
        stage = top / "stage"
        stage_linux(stage)
        files = []
        for p in sorted(stage.rglob("*")):
            rel = "/" + p.relative_to(stage).as_posix()
            if p.is_dir():
                if rel.startswith(f"/opt/{PKG}") or rel == f"/usr/share/doc/{PKG}":
                    files.append(f"%dir {rel}")
            else:
                files.append(rel)
        spec = top / f"{PKG}.spec"
        req = "\n".join(f"Requires: {r}" for r in RPM_REQUIRES)
        rec = "\n".join(f"Recommends: {r}" for r in RPM_RECOMMENDS)
        spec.write_text(f"""Name: {PKG}
Version: {VERSION}
Release: 1
Summary: {SUMMARY}
License: Proprietary
URL: {URL}
BuildArch: x86_64
AutoReqProv: no
{req}
{rec}

%description
{DESCRIPTION}
The first start downloads the setup window into ~/.local/share/chatbox-studio; there you
pick the speech model, translator and graphics support.

%install
cp -a {stage}/. %{{buildroot}}/

%post
{POSTINST.split(chr(10), 1)[1]}
%postun
{POSTINST.split(chr(10), 1)[1]}
%files
%defattr(-,root,root,-)
""" + "\n".join(files) + "\n")
        r = subprocess.run([tool, "-bb", "--quiet", "--define", f"_topdir {top / 'rpm'}",
                            "--define", "__os_install_post %{nil}",
                            "--define", "_build_id_links none", str(spec)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"rpmbuild failed:\n{r.stdout}\n{r.stderr}")
        built = next((top / "rpm" / "RPMS").rglob("*.rpm"))
        out = DIST / built.name
        shutil.copy2(built, out)
    return out


def build_arch() -> Path | None:
    bsdtar, zstd = shutil.which("bsdtar"), shutil.which("zstd")
    if not (bsdtar and zstd):
        print("bsdtar/zstd not found - skipping the Arch package")
        return None
    import time
    out = DIST / f"{PKG}-{VERSION}-1-x86_64.pkg.tar.zst"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        stage_linux(root)
        info = [f"pkgname = {PKG}", f"pkgbase = {PKG}", f"pkgver = {VERSION}-1",
                f"pkgdesc = {SUMMARY}", f"url = {URL}", f"builddate = {int(time.time())}",
                "packager = Chatbox Studio release script", f"size = {installed_kb(root) * 1024}",
                "arch = x86_64", "license = custom"]
        info += [f"depend = {d}" for d in ARCH_DEPENDS]
        info += [f"optdepend = {d}" for d in ARCH_OPTDEPENDS]
        (root / ".PKGINFO").write_text("\n".join(info) + "\n")
        (root / ".INSTALL").write_text(
            "post_install() {\n  update-desktop-database -q /usr/share/applications 2>/dev/null"
            "\n  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null\n  true\n}"
            "\npost_upgrade() {\n  post_install\n}\npost_remove() {\n  post_install\n}\n")
        entries = [".PKGINFO", ".INSTALL", "opt", "usr"]
        env = dict(os.environ, LANG="C")
        subprocess.run([bsdtar, "-czf", ".MTREE", "--format=mtree", "--options="
                        "!all,use-set,type,uid,gid,mode,time,size,md5,sha256,link", *entries],
                       cwd=root, check=True, env=env)
        tar = Path(tmp) / "pkg.tar"
        subprocess.run([bsdtar, "-cf", str(tar), "--uid", "0", "--gid", "0", "--uname", "root",
                        "--gname", "root", ".MTREE", *entries], cwd=root, check=True, env=env)
        subprocess.run([zstd, "-q", "-19", "-T0", "-f", str(tar), "-o", str(out)], check=True)
    return out


def main():
    DIST.mkdir(exist_ok=True)
    win = ROOT / "windows"
    lin = ROOT / "linux"
    docs = ROOT / "docs"
    win_files = [
        (win / "Install.bat", "Install.bat"),
        (win / "install.ps1", "install.ps1"),
        (win / "support" / "Uninstall.bat", "support/Uninstall.bat"),
        (win / "support" / "uninstall.ps1", "support/uninstall.ps1"),
        (win / "support" / "Start with console.bat", "support/Start with console.bat"),
        (docs / "README-Windows.txt", "README.txt"),
        (docs / "CHANGELOG.md", "CHANGELOG.md"),
    ]
    exe = build_exe(win_files)
    built = ([exe] if exe else []) + [
        build("Windows", win_files),
        build("Linux", [
            (lin / "install.sh", "install.sh"),
            (lin / "uninstall.sh", "uninstall.sh"),
            (lin / "setup_translator.sh", "setup_translator.sh"),
            (ROOT / "README.md", "README.md"),
            (docs / "CHANGELOG.md", "CHANGELOG.md"),
        ]),
    ]
    for fn in (build_deb, build_rpm, build_arch):
        p = fn()
        if p:
            built.append(p)
    for p in built:
        print(f"{p.relative_to(ROOT)}  ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--stage":   # distro packaging (PKGBUILD)
        stage_linux(Path(sys.argv[2]))
    else:
        main()
