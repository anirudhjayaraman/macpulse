"""Build and drive MacpulseNotifier.app, macpulse's own notification app.

macOS only lets proper apps send clickable notifications. Rather than ship a
binary, macpulse ships the Swift source and compiles it on your Mac with Apple's
Command Line Tools, then signs it locally ("ad-hoc"), which needs no Apple account.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import __version__
from ..storage import data_dir

BUNDLE_ID = "io.github.macpulse.notifier"
NAME = "MacpulseNotifier"
SOURCE = Path(__file__).with_name(f"{NAME}.swift")


# The bundle is named "macpulse" because macOS shows that name in
# System Settings > General > Login Items for the background agent.
APP_NAME = "macpulse"
LEGACY_APP_NAMES = (f"{NAME}.app",)


def app_path() -> Path:
    return data_dir() / f"{APP_NAME}.app"


def executable() -> Path:
    return app_path() / "Contents" / "MacOS" / NAME


def is_installed() -> bool:
    return executable().exists()


def info_plist() -> dict:
    return {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "macpulse",
        "CFBundleDisplayName": "macpulse",
        "CFBundleExecutable": NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,  # no Dock icon or menu bar
        "NSUserNotificationAlertStyle": "banner",
    }


def install() -> Path:
    """Compile, bundle and sign the app. Raises RuntimeError with a readable reason."""
    # /usr/bin/swiftc exists even without the tools (it pops up an installer), so ask xcode-select.
    if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0 or not shutil.which("swiftc"):
        raise RuntimeError("needs Apple's Command Line Tools. Run `xcode-select --install`, "
                           "then `macpulse setup` again.")
    app = app_path()
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / f"{NAME}.app"
        (staged / "Contents" / "MacOS").mkdir(parents=True)
        res = subprocess.run(
            ["swiftc", "-O", "-swift-version", "5", str(SOURCE),
             "-o", str(staged / "Contents" / "MacOS" / NAME),
             "-framework", "AppKit", "-framework", "UserNotifications"],
            capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Compiling the notifier failed:\n{res.stderr.strip()}")
        with open(staged / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump(info_plist(), f)
        res = subprocess.run(["codesign", "--force", "--sign", "-", str(staged)], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"Signing the notifier failed:\n{res.stderr.strip()}")
        if app.exists():
            shutil.rmtree(app)
        shutil.move(str(staged), str(app))
    for legacy in LEGACY_APP_NAMES:
        old = data_dir() / legacy
        if old.exists():
            shutil.rmtree(old)
    # Register with Launch Services so macOS knows which app owns the notifications.
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
                    "/Support/lsregister", "-f", str(app)], capture_output=True)
    return app


def uninstall() -> bool:
    removed = False
    for path in (app_path(), *(data_dir() / n for n in LEGACY_APP_NAMES)):
        if path.exists():
            shutil.rmtree(path)
            removed = True
    return removed


def send(title: str, message: str, on_click: list[str] | None = None, timeout: float = 15) -> str:
    """Post a notification. Returns "ok", or a reason such as "denied: ..."."""
    if not is_installed():
        return "not installed"
    with tempfile.NamedTemporaryFile(prefix="macpulse-notify-", suffix=".txt", delete=False) as f:
        status_file = Path(f.name)
    status_file.write_text("")
    args = ["--title", title, "--message", message, "--status-file", str(status_file)]
    if on_click:
        args += ["--exec", *on_click]
    # Launch through Launch Services (`open`), as macOS expects for notification apps.
    # -n: new instance even if one is lingering, -g: don't steal focus.
    res = subprocess.run(["open", "-n", "-g", str(app_path()), "--args", *args], capture_output=True, text=True)
    if res.returncode != 0:
        status_file.unlink(missing_ok=True)
        return f"error: {res.stderr.strip()}"
    # The first run waits for you to answer macOS's permission prompt.
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline and not status:
        time.sleep(0.2)
        status = status_file.read_text().strip()
    status_file.unlink(missing_ok=True)
    return status or "timeout: no answer from the notifier (is a permission prompt waiting?)"


def report_command() -> list[str]:
    return [sys.executable, "-m", "macpulse", "report"]
