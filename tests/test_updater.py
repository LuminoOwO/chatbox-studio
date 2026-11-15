"""Offline checks of the in-app updater (python -m pytest tests/test_updater.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import updater as U  # noqa: E402


def test_versions():
    assert U.parse_version("v1.32") == (1, 32) == U.parse_version("1.32.0")
    assert U.is_newer("1.4", "1.31") is False
    assert U.is_newer("1.40", "1.31") and U.is_newer("2.0", "1.99")
    assert not U.is_newer("1.32", "1.32")


def test_assets():
    names = [U.asset_name(k, "1.40") for k in ("windows", "deb", "rpm", "arch", "linux-zip")]
    rel = U.Release("1.40", assets=[
        {"name": n, "browser_download_url": f"https://github.com/x/{n}"} for n in names] + [
        {"name": "chatbox-studio_1.40_amd64.deb", "browser_download_url": "http://evil/x"}])
    for k, n in zip(("windows", "deb", "rpm", "arch", "linux-zip"), names):
        assert U.asset_for(k, rel)["name"] == n
    assert U.asset_for("source", rel) is None
    assert U.why_not("source")


def test_install_kind(tmp_path):
    assert U.install_kind(tmp_path) == "source"
    app = tmp_path / "chatbox-studio" / "app"
    app.mkdir(parents=True)
    (app.parent / "run.sh").write_text("")
    assert U.install_kind(app) == ("source" if U.paths.IS_WINDOWS else "linux-zip")


def test_zip_paths_checked(tmp_path):
    import zipfile
    z = tmp_path / "Chatbox-Studio-1.40-Linux.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../evil.sh", "x")
    try:
        U.prepare_after_exit("linux-zip", z)
    except U.UpdateError:
        return
    raise AssertionError("a path outside the folder was accepted")
