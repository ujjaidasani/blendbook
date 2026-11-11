#!/bin/bash
# Builds dist/Blendbook.app and dist/Blendbook-<version>-mac-<arch>.dmg.
# Needs: python with requirements.txt + requirements-build.txt installed.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' server.py)
ARCH=$(uname -m)

ICONSET=$(mktemp -d)/Blendbook.iconset
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out "$ICONSET/icon_${s}x${s}.png" > /dev/null
  sips -z $((s * 2)) $((s * 2)) packaging/icon.png --out "$ICONSET/icon_${s}x${s}@2x.png" > /dev/null
done
iconutil -c icns "$ICONSET" -o packaging/Blendbook.icns

rm -rf build dist
"$PY" -m PyInstaller --noconfirm --clean packaging/blendbook.spec

# the same self-check CI runs: does the bundle load everything it needs?
dist/Blendbook.app/Contents/MacOS/Blendbook --self-test

STAGE=$(mktemp -d)
cp -R dist/Blendbook.app "$STAGE/"
# PyInstaller's own signing fails in a synced folder like ~/Documents, and a broken seal reads as "damaged" on other Macs
xattr -cr "$STAGE/Blendbook.app"
codesign -s - --force --deep "$STAGE/Blendbook.app"
codesign --verify --deep --strict "$STAGE/Blendbook.app"
DMG="dist/Blendbook-$VERSION-mac-$ARCH.dmg"
# the installer window: dark background with an arrow, app on the left, Applications on the right
"$PY" -m dmgbuild -s packaging/dmg_settings.py -D app="$STAGE/Blendbook.app" "Blendbook" "$DMG"
echo "Built $DMG"
