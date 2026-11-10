#!/usr/bin/env bash
# Chatbox Studio - Linux installer and updater.
# Installs to ~/.local/share/chatbox-studio. Running it again with a newer package
# updates the app and keeps settings, models and (if unchanged) the Python packages.
# Also takes over an install of "VRC Voice Translator" (the old name, versions 1.0-1.8).
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SRC="$HERE/app"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
ROOT="$DATA/chatbox-studio"
APP="$ROOT/app"
VENV="$ROOT/venv"
VERFILE="$ROOT/installed-version.txt"
HASHFILE="$VENV/base.hash"
OLD_ROOT="$DATA/vrc-voice-translator"
OLD_CONF="$CONF/vrc-voice-translator"
APPS="$HOME/.local/share/applications"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$1"; exit 1; }

[ -f "$SRC/app.py" ] || fail "app files missing - extract the whole ZIP and run install.sh from there."
VERSION=$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$SRC/version.py")
OLD=""
[ -f "$VERFILE" ] && OLD=$(cat "$VERFILE")
LEGACY=""
LEGACY_VER=""
if [ -d "$OLD_ROOT" ] || [ -d "$OLD_CONF" ]; then
    LEGACY_VER=$(cat "$OLD_ROOT/installed-version.txt" 2>/dev/null || true)   # empty before 1.4
    LEGACY="yes"
fi

echo
echo "  Chatbox Studio $VERSION"
if [ -n "$LEGACY" ]; then
    echo "  Updating your VRC Voice Translator${LEGACY_VER:+ $LEGACY_VER} - the app is now called Chatbox Studio."
    echo "  Your settings, routes, filters and models are taken over."
elif [ -n "$OLD" ]; then
    echo "  Updating from $OLD. Your settings and models are kept."
else
    echo "  Installing to $ROOT"
fi

# 1. system packages (Debian/Ubuntu/Mint)
if command -v dpkg >/dev/null 2>&1 && [ "${CBS_SKIP_APT:-0}" != 1 ]; then
    missing=()
    for pkg in python3-venv libportaudio2 libxcb-cursor0 libpulse0 pulseaudio-utils pciutils; do
        dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        step "Installing system packages: ${missing[*]}"
        sudo apt install -y "${missing[@]}"
    fi
fi

# 2. close a running copy (new or old name)
if pgrep -f "chatbox-studio/app/app.py|vrc-voice-translator/app/app.py|vrc-voice-translator/app.py" \
        >/dev/null 2>&1; then
    step "Closing the running app"
    pkill -f "chatbox-studio/app/app.py|vrc-voice-translator/app/app.py|vrc-voice-translator/app.py" || true
    sleep 1
fi

# 3. take over the old "VRC Voice Translator" install
if [ -n "$LEGACY" ]; then
    step "Moving your old install over"
    if [ -d "$OLD_ROOT" ]; then
        if [ ! -e "$ROOT" ]; then
            mv "$OLD_ROOT" "$ROOT"                  # keeps the Python packages and models
        else
            if [ -d "$OLD_ROOT/models" ] && [ ! -d "$ROOT/models" ]; then
                mv "$OLD_ROOT/models" "$ROOT/models"
            fi
            rm -rf "$OLD_ROOT"
        fi
        rm -f "$ROOT/run.sh" "$ROOT/installed-version.txt"
    fi
    if [ -d "$OLD_CONF" ]; then
        if [ ! -e "$CONF/chatbox-studio/settings.json" ]; then
            mkdir -p "$CONF/chatbox-studio"
            cp -a "$OLD_CONF/." "$CONF/chatbox-studio/"
        fi
        rm -rf "$OLD_CONF"
    fi
    rm -f "$APPS/vrc-voice-translator.desktop" "$HOME/.local/bin/vrc-voice-translator"
fi
mkdir -p "$ROOT"

# 4. Python environment. The AI packages need Python 3.10 - 3.13; a system Python outside
#    that (or without venv) gets a standalone Python 3.12 (python-build-standalone) instead.
PY_STANDALONE="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16+20261026-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
pick_python() {
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys, venv, ensurepip; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)' >/dev/null 2>&1; then
        echo python3
        return
    fi
    if [ ! -x "$ROOT/python/bin/python3" ]; then
        step "Downloading Python 3.12 (your system Python doesn't fit the AI packages, ~30 MB)" >&2
        tmp=$(mktemp -d)
        if command -v curl >/dev/null 2>&1; then curl -fsSL -o "$tmp/py.tgz" "$PY_STANDALONE"
        else wget -qO "$tmp/py.tgz" "$PY_STANDALONE"; fi || fail "Downloading Python failed."
        tar -xzf "$tmp/py.tgz" -C "$tmp" && rm -rf "$ROOT/python" && mv "$tmp/python" "$ROOT/python"
        rm -rf "$tmp"
    fi
    echo "$ROOT/python/bin/python3"
}
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c "import sys" >/dev/null 2>&1; then
    PY=$(pick_python)
    step "Creating the Python environment"
    rm -rf "$VENV"
    "$PY" -m venv "$VENV"
    "$VENV/bin/python" -m pip install -q --upgrade pip
fi

# 5. the setup window's own packages (Qt), only when they changed.
#    Everything else - speech engine, CUDA, models - is picked in the setup window.
HASH=$(sha256sum "$SRC/req/base.txt" | cut -d' ' -f1)
if [ "${CBS_SKIP_DEPS:-0}" = 1 ]; then
    step "Skipping packages (CBS_SKIP_DEPS=1)"
elif [ "$(cat "$HASHFILE" 2>/dev/null)" != "$HASH" ]; then
    step "Installing the setup window (~100 MB)"
    "$VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
    "$VENV/bin/python" -m pip install -r "$SRC/req/base.txt"
    echo "$HASH" > "$HASHFILE"
fi

# 6. app files and launcher
step "Copying the app"
rm -rf "$APP"
cp -r "$SRC" "$APP"
for f in setup_translator.sh uninstall.sh README.md CHANGELOG.md; do
    [ -f "$HERE/$f" ] && cp "$HERE/$f" "$ROOT/$f"
done
chmod +x "$ROOT"/*.sh

cat > "$ROOT/run.sh" <<'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
SP=$("$ROOT/venv/bin/python" -c 'import site; print(site.getsitepackages()[0])')
export LD_LIBRARY_PATH="$SP/nvidia/cublas/lib:$SP/nvidia/cudnn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[ -d /opt/rocm/lib ] && export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:/opt/rocm/lib"   # AMD (ROCm)
export CUDA_DEVICE_ORDER=PCI_BUS_ID   # GPU numbers match nvidia-smi
exec "$ROOT/venv/bin/python" "$ROOT/app/app.py" "$@"
EOF
chmod +x "$ROOT/run.sh"
cat > "$ROOT/setup.sh" <<'EOF'
#!/usr/bin/env bash
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
exec "$ROOT/venv/bin/python" "$ROOT/app/setup_gui.py" "$@"
EOF
chmod +x "$ROOT/setup.sh"
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/run.sh" "$HOME/.local/bin/chatbox-studio"
ln -sf "$ROOT/setup.sh" "$HOME/.local/bin/chatbox-studio-setup"

# 7. menu entry
step "Adding the menu entry"
mkdir -p "$APPS"
cat > "$APPS/chatbox-studio.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Chatbox Studio
GenericName=VRChat chatbox and voice translator
Comment=Voice translation, music, clock and status for your VRChat chatbox
Exec="$ROOT/run.sh"
Icon=$APP/assets/icon.png
Terminal=false
Categories=AudioVideo;Utility;
Keywords=VRChat;OSC;chatbox;translator;speech;
EOF
cat > "$APPS/chatbox-studio-setup.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Chatbox Studio Setup
Comment=Add or remove parts of Chatbox Studio (speech models, translator, AI)
Exec="$ROOT/setup.sh" --modify
Icon=$APP/assets/icon.png
Terminal=false
Categories=Settings;Utility;
EOF
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPS" >/dev/null 2>&1 || true

echo "$VERSION" > "$VERFILE"

# 8. pick the parts: setup window, or the recommended parts without one
MODE="--first"
[ -n "$LEGACY$OLD" ] && MODE="--update"
if [ "${CBS_DEFAULTS:-0}" = 1 ] || [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    step "Installing the recommended parts"
    "$VENV/bin/python" "$APP/setup_gui.py" --defaults ${OLD:+--update} || \
        fail "Installing the packages failed. Check your internet connection and run install.sh again."
    if [ -z "${CBS_NO_START:-}" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        nohup "$ROOT/run.sh" >/dev/null 2>&1 &
    fi
else
    step "Opening the setup window - pick what you want there"
    "$VENV/bin/python" "$APP/setup_gui.py" "$MODE" || true
fi

echo
if [ -n "$LEGACY$OLD" ]; then printf '\033[1;32m  Updated to Chatbox Studio %s.\033[0m\n' "$VERSION"
else printf '\033[1;32m  Installed Chatbox Studio %s.\033[0m\n' "$VERSION"; fi
echo "  Start it from the menu ('Chatbox Studio') or with: chatbox-studio"
echo "  Add or remove parts later: 'Chatbox Studio Setup' in the menu, or chatbox-studio-setup"
echo "  You can delete the extracted folder now."
