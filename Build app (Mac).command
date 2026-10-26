#!/bin/bash
# Builds CreatorScout.app (double-click app, no Terminal) and puts it in ~/Applications.
set -e
cd "$(dirname "$0")"
echo "=== CreatorScout: build CreatorScout.app ==="

has_tk() { "$1" -c 'import sys, tkinter; assert sys.version_info >= (3, 10); assert tkinter.TkVersion >= 8.6' >/dev/null 2>&1; }

PY=""
for c in /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
         /opt/homebrew/bin/python3 "$HOME/.homebrew/bin/python3" /usr/local/bin/python3 \
         "$(command -v python3 || true)"; do
  if [ -x "$c" ] && has_tk "$c"; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  BREW="$(command -v brew || ls "$HOME/.homebrew/bin/brew" /opt/homebrew/bin/brew 2>/dev/null | head -1)"
  if [ -n "$BREW" ]; then
    echo "Installing Python with Tkinter via Homebrew..."
    "$BREW" install python python-tk
    for c in "$("$BREW" --prefix)/bin/python3"; do has_tk "$c" && PY="$c"; done
  fi
fi
if [ -z "$PY" ]; then
  echo "[ERROR] No Python 3.10+ with Tkinter found. Install Python from https://www.python.org/downloads/macos/ and run this again."
  exit 1
fi
echo "Using: $PY ($("$PY" --version))"

"$PY" -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt pyinstaller
python tests/test_pipeline.py && python tests/test_enrich.py && python tests/test_model.py && python tests/test_embed.py && python tests/test_lookup.py && python tests/test_email_precision.py && python tests/test_explain.py && python tests/test_steam.py
python -m PyInstaller --noconfirm --clean --windowed --name CreatorScout \
  --osx-bundle-identifier ro.creatorscout.app --add-data "assets:assets" --collect-data selenium --collect-binaries onnxruntime --collect-binaries tokenizers app.py

mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/CreatorScout.app"
cp -R dist/CreatorScout.app "$HOME/Applications/"
echo
echo "=== DONE: ~/Applications/CreatorScout.app ==="
open "$HOME/Applications/CreatorScout.app"
