#!/usr/bin/env bash
# Chatbox Studio - uninstaller.
ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/chatbox-studio"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/chatbox-studio"

pkill -f "$ROOT/app/app.py" 2>/dev/null || true
rm -f "$HOME/.local/share/applications/chatbox-studio.desktop" \
      "$HOME/.local/share/applications/chatbox-studio-setup.desktop" \
      "$HOME/.local/bin/chatbox-studio" "$HOME/.local/bin/chatbox-studio-setup"

read -r -p "Keep your settings and the downloaded translation model? [Y/n] " keep
if [[ "$keep" =~ ^[nN] ]]; then
    rm -rf "$ROOT" "$CONFIG"
    echo "Removed everything."
else
    rm -rf "$ROOT/app" "$ROOT/venv" "$ROOT/run.sh" "$ROOT/setup_translator.sh" \
           "$ROOT/installed-version.txt" "$ROOT/README.md" "$ROOT/CHANGELOG.md" "$ROOT/uninstall.sh" \
           "$ROOT/setup.sh" "$ROOT/components.json"
    echo "Removed the app. Settings and models were kept in $CONFIG and $ROOT."
fi
echo "The Whisper model cache in ~/.cache/huggingface can be deleted too if you like."
