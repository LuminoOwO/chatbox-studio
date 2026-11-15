"""Updating Chatbox Studio from inside the app.

Looks up the newest release on GitHub, downloads the package that matches how this copy
was installed, checks it, and installs it:

| installed with            | the update                                                  |
|---------------------------|-------------------------------------------------------------|
| Windows setup .exe / ZIP  | the new setup .exe runs after the app closes (no clicks)     |
| .deb / .rpm / Arch package| the system's package manager, after asking for your password |
| Linux ZIP (install.sh)    | the new ZIP's install.sh runs after the app closes           |
| a source checkout         | no automatic update - opens the download page                |

Settings, models and the Python environment are kept in every case. No Qt in here, so
the logic can be tested on its own (update_ui.py is the window).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import paths
from version import __version__

REPO = "LuminoOwO/chatbox-studio"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
UA = {"User-Agent": f"ChatboxStudio/{__version__}", "Accept": "application/vnd.github+json"}
APP_DIR = Path(__file__).resolve().parent
OPT = Path("/opt/chatbox-studio")


class UpdateError(RuntimeError):
    pass


@dataclass
class Release:
    version: str
    notes: str = ""
    url: str = PAGE
    assets: list = field(default_factory=list)


def parse_version(v: str) -> tuple:
    """'v1.32' → (1, 32). Parts that aren't numbers count as 0."""
    out = []
    for part in v.strip().lstrip("vV").split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    return tuple(out)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def latest(timeout: float = 10) -> Release:
    """The newest published release."""
    req = urllib.request.Request(API, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except OSError as e:
        raise UpdateError(f"GitHub can't be reached ({getattr(e, 'reason', e)})") from None
    except ValueError:
        raise UpdateError("GitHub sent an answer that isn't readable") from None
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError("No release found on GitHub")
    return Release(version=tag.lstrip("vV"), notes=str(data.get("body") or ""),
                   url=str(data.get("html_url") or PAGE), assets=list(data.get("assets") or []))


# ---------------------------------------------------------------------------- install kind

def install_kind(app_dir: Path = APP_DIR) -> str:
    """How this copy was installed: windows, deb, rpm, arch, linux-zip or source."""
    app_dir = app_dir.resolve()
    if paths.IS_WINDOWS:
        root = app_dir.parent
        if app_dir.name == "app" and (root / "installed-version.txt").exists():
            return "windows"
        return "source"
    if app_dir == OPT / "app":
        marker = OPT / "PACKAGE"
        kind = marker.read_text().strip() if marker.exists() else ""
        if kind in ("deb", "rpm", "arch"):
            return kind
        # packages from before 1.32 have no marker: ask the package managers
        for kind, cmd in (("deb", ["dpkg-query", "-W", "chatbox-studio"]),
                          ("rpm", ["rpm", "-q", "chatbox-studio"]),
                          ("arch", ["pacman", "-Q", "chatbox-studio"])):
            if shutil.which(cmd[0]):
                try:
                    if subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0:
                        return kind
                except (OSError, subprocess.TimeoutExpired):
                    pass
        return "source"
    if app_dir.name == "app" and (app_dir.parent / "run.sh").exists():
        return "linux-zip"
    return "source"


KIND_NAMES = {
    "windows": "the Windows installer", "deb": "the .deb package", "rpm": "the .rpm package",
    "arch": "the Arch package", "linux-zip": "the Linux ZIP", "source": "the source code",
}


def asset_name(kind: str, version: str) -> Optional[str]:
    return {
        "windows": f"Chatbox-Studio-{version}-Windows-Setup.exe",
        "deb": f"chatbox-studio_{version}_amd64.deb",
        "rpm": f"chatbox-studio-{version}-1.x86_64.rpm",
        "arch": f"chatbox-studio-{version}-1-x86_64.pkg.tar.zst",
        "linux-zip": f"Chatbox-Studio-{version}-Linux.zip",
    }.get(kind)


def asset_for(kind: str, rel: Release) -> Optional[dict]:
    name = asset_name(kind, rel.version)
    for a in rel.assets:
        if name and a.get("name") == name:
            url = str(a.get("browser_download_url") or "")
            if url.startswith("https://github.com/"):
                return a
    return None


def package_tool(kind: str) -> Optional[list]:
    """The command that installs a downloaded package file (without the file)."""
    if kind == "deb" and shutil.which("apt-get"):
        return [shutil.which("apt-get"), "install", "-y"]
    if kind == "deb" and shutil.which("dpkg"):
        return [shutil.which("dpkg"), "-i"]
    if kind == "rpm":
        for tool, args in (("dnf5", ["install", "-y"]), ("dnf", ["install", "-y"]),
                           ("zypper", ["--non-interactive", "install", "--allow-unsigned-rpm"]),
                           ("rpm", ["-U"])):
            if shutil.which(tool):
                return [shutil.which(tool)] + args
    if kind == "arch" and shutil.which("pacman"):
        return [shutil.which("pacman"), "-U", "--noconfirm"]
    return None


def why_not(kind: str) -> str:
    """Empty when this copy can update itself, otherwise the reason (for the window)."""
    if kind == "source":
        return ("This copy runs from the source code, so it isn't updated automatically. "
                "Get the new version from the download page (or with git pull).")
    if kind in ("deb", "rpm", "arch"):
        if not package_tool(kind):
            return "No package manager was found to install the update with."
        if not shutil.which("pkexec"):
            return ("Installing a system package needs administrator rights, and 'pkexec' "
                    "(which asks for your password) isn't installed. Download the package "
                    "and install it with your package manager.")
    return ""


# ---------------------------------------------------------------------------- download

def download(asset: dict, progress: Callable[[int, int], None] = lambda done, total: None,
             cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Downloads a release file into a fresh temporary folder and checks it (size, and the
    SHA-256 GitHub publishes for it). Returns the file."""
    name = Path(str(asset["name"])).name
    total = int(asset.get("size") or 0)
    folder = Path(tempfile.mkdtemp(prefix="chatbox-studio-update-"))
    folder.chmod(0o755)            # the package manager (root / _apt) must be able to read it
    dest = folder / name
    sha = hashlib.sha256()
    done = 0
    req = urllib.request.Request(str(asset["browser_download_url"]), headers={"User-Agent": UA["User-Agent"]})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            total = total or int(r.headers.get("Content-Length") or 0)
            while True:
                if cancelled():
                    raise UpdateError("Cancelled")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                progress(done, total)
    except OSError as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError(f"The download failed ({getattr(e, 'reason', e)})") from None
    except UpdateError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if total and done != total:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("The download is incomplete - try again")
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:") and digest[7:].lower() != sha.hexdigest():
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateError("The downloaded file is damaged (its checksum doesn't match) - try again")
    dest.chmod(0o644)
    return dest


# ---------------------------------------------------------------------------- install

def package_command(kind: str, file: Path) -> list:
    """pkexec + the package manager + the file (for a QProcess / subprocess)."""
    tool = package_tool(kind)
    if not tool or not shutil.which("pkexec"):
        raise UpdateError(why_not(kind) or "Can't install the package here")
    return [shutil.which("pkexec")] + tool + [str(file)]


def parts_fingerprint(app_dir: Path) -> str:
    """Changes when the packages the parts need changed (then the setup window runs once
    after the update, to bring them up to date)."""
    h = hashlib.sha256()
    for p in sorted((app_dir / "req").glob("*.txt")) + [app_dir / "components.py"]:
        if p.name == "base.txt":
            continue                     # the launcher itself handles base.txt
        try:
            h.update(p.name.encode() + b"\0" + p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()


def after_package_command(before: str) -> list:
    """What starts after a package update: the app, or - when the parts' packages changed
    - the setup window in update mode (it starts the app when it's done)."""
    launcher = os.environ.get("CHATBOX_STUDIO_LAUNCHER") or "/usr/bin/chatbox-studio"
    if parts_fingerprint(OPT / "app") != before:
        setup = str(Path(launcher).with_name("chatbox-studio-setup"))
        if not Path(setup).exists():
            setup = "/usr/bin/chatbox-studio-setup"
        return [setup, "--update"]
    return [launcher]


def run_after_exit(cmd: list, log_file: Optional[Path] = None) -> None:
    """Starts `cmd` once this process has ended (the app allows only one copy at a time,
    and installers replace the files it runs from)."""
    pid = os.getpid()
    if paths.IS_WINDOWS:
        def q(s):
            return "'" + str(s).replace("'", "''") + "'"
        args = ", ".join(q(a) for a in cmd[1:])
        script = (f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; "
                  f"Start-Process -FilePath {q(cmd[0])}"
                  + (f" -ArgumentList @({args})" if cmd[1:] else ""))
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-Command", script],
                         cwd=tempfile.gettempdir(), creationflags=flags, close_fds=True)
        return
    out = subprocess.DEVNULL
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        out = open(log_file, "ab")
    script = 'while kill -0 "$0" 2>/dev/null; do sleep 0.3; done; exec "$@"'
    subprocess.Popen(["sh", "-c", script, str(pid)] + [str(c) for c in cmd],
                     cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL, stdout=out,
                     stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)


def prepare_after_exit(kind: str, file: Path) -> list:
    """The command that installs a downloaded Windows setup / Linux ZIP once the app has
    closed. (Packages are installed while the app runs; see package_command.)"""
    if kind == "windows":
        return [str(file), "/UPDATE"]       # the setup skips its welcome page and runs
    if kind == "linux-zip":
        out = file.parent / "unpacked"
        try:
            with zipfile.ZipFile(file) as z:
                for m in z.infolist():
                    target = (out / m.filename).resolve()
                    if not str(target).startswith(str(out.resolve())):
                        raise UpdateError("The ZIP contains unexpected paths")
                z.extractall(out)
        except (zipfile.BadZipFile, OSError) as e:
            raise UpdateError(f"The ZIP can't be unpacked ({e})") from None
        script = next(out.rglob("install.sh"), None)
        if not script:
            raise UpdateError("install.sh is missing in the downloaded ZIP")
        return ["bash", str(script)]
    raise UpdateError(f"Updating {KIND_NAMES.get(kind, kind)} this way isn't possible")


def describe(cmd: list) -> str:
    return " ".join(shlex.quote(str(c)) for c in cmd)


if __name__ == "__main__":       # python updater.py: what an update would do here
    k = install_kind()
    print(f"Chatbox Studio {__version__}, installed with {KIND_NAMES[k]} ({k})")
    rel = latest()
    print(f"Newest release: {rel.version}" + (" - newer" if is_newer(rel.version) else ""))
    a = asset_for(k, rel)
    print("Download:", a["browser_download_url"] if a else "-")
    print("Blocker:", why_not(k) or "none")
    sys.exit(0)
