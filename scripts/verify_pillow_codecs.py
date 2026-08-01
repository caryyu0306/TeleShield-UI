#!/usr/bin/env python3
"""Verify the codecs retained in the frozen sidecar runtime."""

from __future__ import annotations

import base64
import io
from pathlib import Path
import sys


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1Pe"
    "AAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)
JPEG_1X1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEA"
    "AAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIh"
    "MUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0"
    "RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqK"
    "jpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09"
    "fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBA"
    "QDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYk"
    "NOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eH"
    "l6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1"
    "dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigD//2Q=="
)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} SIDECAR_DIR", file=sys.stderr)
        return 2

    internal_dir = Path(sys.argv[1]).resolve() / "_internal"
    if not internal_dir.is_dir():
        raise SystemExit(f"missing PyInstaller internal directory: {internal_dir}")
    sys.path.insert(0, str(internal_dir))

    # PyInstaller stores Pillow's Python modules in its PYZ archive, while the
    # native extensions remain in _internal/PIL. Reuse the venv's pure-Python
    # modules for this check, but force their package to load the frozen
    # extension from the output directory rather than the host environment.
    import PIL

    PIL.__path__.insert(0, str(internal_dir / "PIL"))
    from PIL import Image, features
    import PIL._imaging as imaging

    if Path(imaging.__file__).resolve().parent != internal_dir / "PIL":
        raise SystemExit(f"Pillow verifier loaded a host extension: {imaging.__file__}")

    if not features.check("jpg"):
        raise SystemExit("bundled Pillow does not provide JPEG support")
    if not features.check("zlib"):
        raise SystemExit("bundled Pillow does not provide PNG/zlib support")

    for name, payload, expected_format in (
        ("JPEG", JPEG_1X1, "JPEG"),
        ("PNG", PNG_1X1, "PNG"),
    ):
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.format != expected_format:
                raise SystemExit(
                    f"bundled Pillow decoded {name} as {image.format!r}"
                )
        print(f"Pillow {name} decode: ok")

    for codec in ("webp", "avif", "jpg_2000", "libtiff", "littlecms2", "xcb"):
        if features.check(codec):
            raise SystemExit(f"unexpected optional Pillow codec remains: {codec}")

    print("Pillow optional codec reduction: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
