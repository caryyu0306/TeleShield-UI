"""macOS login-item integration for the native SwiftUI application."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


STARTUP_ID = "org.teleshield.desktop"
STARTUP_NAME = "TeleShield"


def application_command() -> list[str]:
    """Return the command used by launchd to reopen the SwiftUI app hidden."""
    app_path = os.getenv("TELESHIELD_STARTUP_APP", "").strip()
    if not app_path:
        raise RuntimeError("找不到 SwiftUI App 路徑，無法設定開機啟動")
    return ["/usr/bin/open", "-a", app_path, "--args", "--background"]


def startup_file_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{STARTUP_ID}.plist"


def set_start_on_login(enabled: bool) -> None:
    """Enable or disable launching the SwiftUI app when the user logs in."""
    path = startup_file_path()
    uid = str(os.getuid())
    domain = f"gui/{uid}"

    # Remove a previously bootstrapped agent first. It is fine if it was not
    # loaded; launchctl returns a non-zero status in that case.
    subprocess.run(
        ["launchctl", "bootout", domain, str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not enabled:
        path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": STARTUP_ID,
        "ProgramArguments": application_command(),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }
    path.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML))
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        raise RuntimeError("macOS launchctl 無法註冊開機啟動項目")


def is_start_on_login_enabled() -> bool:
    return startup_file_path().exists()
