# Releasing a new version

1. Set the new number in `src/version.py` (e.g. `__version__ = "1.31"`) and in
   `packaging/arch/PKGBUILD` (`pkgver`).
2. Add a `## <version>` section at the top of `docs/CHANGELOG.md` - it becomes the release
   notes.
3. Commit and push to `main`:

   ```bash
   git push origin main
   ```

   Because `src/version.py` changed, `.github/workflows/release.yml` builds every package
   and publishes the release `v<version>` (it creates the tag):

   | File | For |
   |---|---|
   | `Chatbox-Studio-<version>-Windows-Setup.exe` | Windows (NSIS, `windows/setup.nsi`) |
   | `Chatbox-Studio-<version>-Windows.zip` | Windows, when Smart App Control blocks the .exe |
   | `Chatbox-Studio-<version>-Linux.zip` | any Linux distribution (`install.sh`) |
   | `chatbox-studio_<version>_amd64.deb` | Debian, Ubuntu, Mint, Pop!_OS, ... |
   | `chatbox-studio-<version>-1.x86_64.rpm` | Fedora, openSUSE, RHEL-likes |
   | `chatbox-studio-<version>-1-x86_64.pkg.tar.zst` | Arch and derivatives |

To build locally: `python3 build_release.py` (needs `nsis`, `dpkg-deb`, `rpm`,
`libarchive-tools` and `zstd`; a missing tool skips its package).

The **Clanker Index** badge in the README (`.clanker/*.svg`) is rebuilt and committed by
`.github/workflows/clanker-index.yml` on every push to `main`, so it updates with each
version. After a release, `git pull` before your next push to get that badge commit.

## The Vulkan speech engine

`.github/workflows/engines.yml` builds whisper.cpp's `whisper-server` with Vulkan for Linux
and Windows, tests it with the app's client (`tests/test_whispercpp_engine.py`) and publishes
it as the release `engine-whispercpp-<version>`. The app downloads it from there
(`src/whispercpp.py`, `ENGINE_URL`). To move to a newer whisper.cpp, change
`WHISPERCPP_VERSION` in both files and run the workflow (Actions → Vulkan speech engine →
Run workflow).

## Where things live

Settings live outside the app folder, so they survive updates:

| | Settings | App, Python, models, logs |
|---|---|---|
| Windows | `%APPDATA%\Chatbox Studio` | `%LOCALAPPDATA%\Chatbox Studio` |
| Linux | `~/.config/chatbox-studio` | `~/.local/share/chatbox-studio` (packages: app in `/opt/chatbox-studio`) |
