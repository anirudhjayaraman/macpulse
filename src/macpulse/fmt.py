"""Small formatting helpers shared by the CLI and report."""

from __future__ import annotations

import os
import sys


def human_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {rem // 60}m"


def days_label(days: float) -> str:
    return f"{days:g} day" + ("" if days == 1 else "s")


_COLORS = {"ok": "32", "info": "36", "warn": "33", "critical": "31", "dim": "2", "bold": "1"}


def color(text: str, style: str, stream=sys.stdout) -> str:
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        return text
    return f"\033[{_COLORS[style]}m{text}\033[0m"
