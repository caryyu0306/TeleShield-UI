#!/usr/bin/env bash
set -euo pipefail

# Build Pillow with only the codecs used by the sidecar OCR path. A normal
# Pillow wheel links _imaging against TIFF, JPEG2000, WebP, AVIF, LCMS, and
# XCB even though this app only needs JPEG and PNG decoding.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python}"
ZLIB_ROOT="$(brew --prefix zlib)"
JPEG_ROOT="$(brew --prefix jpeg-turbo)"

export ZLIB_ROOT
export JPEG_ROOT
export CPPFLAGS="${CPPFLAGS:-} -I${ZLIB_ROOT}/include -I${JPEG_ROOT}/include"
export LDFLAGS="${LDFLAGS:-} -L${ZLIB_ROOT}/lib -L${JPEG_ROOT}/lib"

"$PYTHON_BIN" -m pip install \
  --force-reinstall \
  --no-cache-dir \
  --no-binary=Pillow \
  --config-settings=jpeg=enable \
  --config-settings=zlib=enable \
  --config-settings=tiff=disable \
  --config-settings=jpeg2000=disable \
  --config-settings=webp=disable \
  --config-settings=avif=disable \
  --config-settings=lcms=disable \
  --config-settings=xcb=disable \
  --config-settings=freetype=disable \
  --config-settings=raqm=disable \
  --config-settings=imagequant=disable \
  --config-settings=platform-guessing=disable \
  'Pillow>=10,<13'

# Keep Pillow out of the requirements-file install above. pip does not pass
# PEP 517 config settings reliably to a package reached through -r, so Pillow
# must be installed as a direct requirement for the codec switches to apply.
"$PYTHON_BIN" -m pip install \
  --no-cache-dir \
  -r "$ROOT_DIR/requirements.txt" \
  'pytesseract>=0.3.13,<1' \
  'pyinstaller>=6.10,<7'
