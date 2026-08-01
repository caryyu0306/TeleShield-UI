#!/usr/bin/env python3
"""Replace byte-identical bundled dylibs with relative symlinks.

PyInstaller and the manually bundled Tesseract runtime can place the same
dependency at both the helper root and tesseract-runtime/lib. Only exact byte
matches are deduplicated; differently-rewritten Mach-O files are left alone
because their loader paths may intentionally differ.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def canonical_key(path: Path) -> tuple[int, str]:
    # Keep the explicitly bundled Tesseract copy as the physical file. The
    # helper-root path remains available through a relative symlink.
    is_tesseract_runtime = "tesseract-runtime" in path.parts and "lib" in path.parts
    return (0 if is_tesseract_runtime else 1, str(path))


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} SIDECAR_DIR", file=sys.stderr)
        return 2

    sidecar_dir = Path(sys.argv[1]).resolve()
    internal_dir = sidecar_dir / "_internal"
    if not internal_dir.is_dir():
        raise SystemExit(f"missing PyInstaller internal directory: {internal_dir}")

    groups: dict[str, list[Path]] = {}
    for path in sorted(internal_dir.rglob("*.dylib")):
        if path.is_symlink() or not path.is_file():
            continue
        groups.setdefault(digest(path), []).append(path)

    deduplicated = 0
    for paths in groups.values():
        if len(paths) < 2:
            continue
        canonical = sorted(paths, key=canonical_key)[0]
        for duplicate in sorted(paths):
            if duplicate == canonical:
                continue
            duplicate.unlink()
            duplicate.symlink_to(os.path.relpath(canonical, duplicate.parent))
            deduplicated += 1
            print(f"Deduplicated {duplicate} -> {canonical}")

    # Fail early if a generated link is broken before the app is assembled.
    for link in internal_dir.rglob("*.dylib"):
        if link.is_symlink():
            link.resolve(strict=True)

    print(f"Deduplicated dylib files: {deduplicated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
