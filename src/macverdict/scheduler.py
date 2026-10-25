"""Run `macverdict record` on a schedule with a per-user launchd agent.

launchd is macOS's native scheduler: no cron, no daemon to keep alive, and it
catches up after the Mac wakes from sleep.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from . import notifier
from .storage import LEGACY_NAME, data_dir, legacy_dir, stable_python

LABEL = "io.github.macverdict.record"


LEGACY_LABEL = f"io.github.{LEGACY_NAME}.record"


def remove_legacy() -> list[str]:
    """Remove the background agent and alert app left by macpulse (macverdict's old name)."""
    removed = []
    legacy_plist = Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"
    if _launchctl("print", f"{_domain()}/{LEGACY_LABEL}").returncode == 0:
        _launchctl("bootout", f"{_domain()}/{LEGACY_LABEL}")
    if legacy_plist.exists():
        legacy_plist.unlink()
        removed.append("background agent")
    legacy_app = legacy_dir() / f"{LEGACY_NAME}.app"
    if legacy_app.exists():
        shutil.rmtree(legacy_app)
        removed.append("alert app")
    return removed


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def build_plist(every_minutes: int, notify: bool = True) -> dict:
    args = [stable_python(), "-m", "macverdict", "record", "--quiet"]
    if notify:
        args.append("--notify")
    if notifier.is_installed():
        # Start through macverdict.app so Login Items shows "macverdict", not "python3".
        args = [str(notifier.executable()), "--run", *args]
    logs = data_dir()
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if os.environ.get("MACVERDICT_HOME"):
        env["MACVERDICT_HOME"] = os.environ["MACVERDICT_HOME"]
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "StartInterval": every_minutes * 60,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "EnvironmentVariables": env,
        "StandardOutPath": str(logs / "agent.log"),
        "StandardErrorPath": str(logs / "agent.err.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(every_minutes: int = 15, notify: bool = True) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    with open(path, "wb") as f:
        plistlib.dump(build_plist(every_minutes, notify), f)
    res = _launchctl("bootstrap", _domain(), str(path))
    if res.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {res.stderr.strip() or res.stdout.strip()}")
    return path


def refresh() -> bool:
    """Re-install an existing agent with its current settings (e.g. after the app changes)."""
    st = status()
    if not st["installed"]:
        return False
    with open(plist_path(), "rb") as f:
        notify_on = "--notify" in plistlib.load(f).get("ProgramArguments", [])
    install(st["every_minutes"] or 15, notify=notify_on)
    return True


def uninstall() -> bool:
    path = plist_path()
    if is_loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    if path.exists():
        path.unlink()
        return True
    return False


def is_loaded() -> bool:
    return _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0


def status() -> dict:
    path = plist_path()
    info = {"installed": path.exists(), "loaded": is_loaded(), "plist": str(path), "every_minutes": None}
    if path.exists():
        with open(path, "rb") as f:
            info["every_minutes"] = plistlib.load(f).get("StartInterval", 0) // 60
    return info


def notify(title: str, message: str, open_report: bool = True) -> str:
    """Show a macOS notification banner and return which backend sent it.

    With MacverdictNotifier installed (`macverdict notifier install`), clicking the
    banner rebuilds and opens the HTML report. Otherwise it falls back to
    osascript, whose banners belong to Script Editor (clicking opens Script Editor).
    """
    if notifier.is_installed():
        on_click = notifier.report_command() if open_report else None
        if notifier.send(title, message, on_click) == "ok":
            return "macverdict"

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    subprocess.run(["osascript", "-e", script], capture_output=True)
    return "osascript"
