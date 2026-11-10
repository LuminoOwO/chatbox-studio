#!/usr/bin/env bash
# One-time setup of the NLLB-200 translation model (used for translating into
# languages other than English). Downloads Meta's model, converts it to an int8
# CTranslate2 model, and removes everything else afterwards.
#
#   ./setup_translator.sh                          # nllb-200-distilled-600M (default)
#   ./setup_translator.sh nllb-200-distilled-1.3B  # better, bigger
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

MODEL="${1:-nllb-200-distilled-600M}"
case "$MODEL" in
    nllb-200-distilled-600M|nllb-200-distilled-1.3B) ;;
    *) echo "Unknown model: $MODEL" >&2; exit 2 ;;
esac

DEST="${XDG_DATA_HOME:-$HOME/.local/share}/chatbox-studio/models/$MODEL"
if [ -f "$DEST/model.bin" ] && [ -f "$DEST/sentencepiece.bpe.model" ]; then
    echo "$MODEL is already installed in $DEST"
    exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export HF_HOME="$WORK/hf"          # keep the big original download out of your home cache
export PIP_DISABLE_PIP_VERSION_CHECK=1

echo "[1/3] Preparing converter tools (temporary, CPU only)..."
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --index-url https://download.pytorch.org/whl/cpu torch
"$WORK/venv/bin/pip" install -q "transformers>=4.40,<5" sentencepiece protobuf "ctranslate2>=4.5"

echo "[2/3] Downloading facebook/$MODEL and converting to int8 (takes a few minutes)..."
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST.partial"
"$WORK/venv/bin/ct2-transformers-converter" \
    --model "facebook/$MODEL" \
    --quantization int8 \
    --copy_files sentencepiece.bpe.model \
    --output_dir "$DEST.partial"

echo "[3/3] Finishing..."
rm -rf "$DEST"
mv "$DEST.partial" "$DEST"
du -sh "$DEST" | awk '{print "Installed " $1 " to " $2}'
echo "DONE"
