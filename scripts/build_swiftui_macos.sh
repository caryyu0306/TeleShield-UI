#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="TeleShieldSwiftUI"
APP_DIR="${ROOT_DIR}/dist/${APP_NAME}.app"
SIDECAR_DIR="${ROOT_DIR}/dist/TeleShieldCore"
ICON_FILE="${ROOT_DIR}/swiftui/Resources/TeleShield.icns"
PYTHON_BIN="${PYTHON_BIN:-python3}"

rm -rf "$APP_DIR" "$SIDECAR_DIR"

test -f "$ICON_FILE"

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --console \
  --name TeleShieldCore \
  --additional-hooks-dir "${ROOT_DIR}/scripts/pyinstaller-hooks" \
  --collect-data opencc \
  --add-data "build/tesseract-runtime:tesseract-runtime" \
  core_service.py

"$PYTHON_BIN" "${ROOT_DIR}/scripts/dedupe_macos_dylibs.py" "$SIDECAR_DIR"
"$PYTHON_BIN" "${ROOT_DIR}/scripts/verify_pillow_codecs.py" "$SIDECAR_DIR"

swift build --package-path swiftui -c release

mkdir -p \
  "$APP_DIR/Contents/MacOS" \
  "$APP_DIR/Contents/Helpers" \
  "$APP_DIR/Contents/Resources"
cp "swiftui/.build/release/TeleShieldApp" "$APP_DIR/Contents/MacOS/TeleShieldApp"
cp -R "$SIDECAR_DIR" "$APP_DIR/Contents/Helpers/TeleShieldCore"
cp "swiftui/Info.plist" "$APP_DIR/Contents/Info.plist"
cp "$ICON_FILE" "$APP_DIR/Contents/Resources/TeleShield.icns"
chmod 755 "$APP_DIR/Contents/MacOS/TeleShieldApp"
chmod 755 "$APP_DIR/Contents/Helpers/TeleShieldCore/TeleShieldCore"

test -x "$APP_DIR/Contents/MacOS/TeleShieldApp"
test -x "$APP_DIR/Contents/Helpers/TeleShieldCore/TeleShieldCore"

echo "Built $APP_DIR"
du -sh "$APP_DIR"
du -sh "$APP_DIR/Contents/Helpers/TeleShieldCore"
