"""Collect a point-in-time snapshot of system performance.

Everything here works without sudo. macOS-specific data (battery health,
memory pressure, thermal throttling) comes from `ioreg`, `sysctl` and `pmset`;
the rest comes from psutil. Any source that fails is reported as None rather
than raising, so a snapshot is always produced.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import plistlib
import re
import subprocess
import time
from typing import Any

import psutil

PRESSURE_LEVELS = {1: "normal", 2: "warn", 4: "critical"}
THERMAL_STATES = ("nominal", "fair", "serious", "critical")  # NSProcessInfo.ThermalState
TOP_N = 5
APP_RE = re.compile(r"/([^/]+)\.app/")
HELPER_RE = re.compile(r"\s+(Helper|Web Content|Networking|Graphics and Media)\b.*$")


def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode(errors="replace")


def _sysctl(name: str) -> str | None:
    out = _run(["sysctl", "-n", name])
    return out.strip() if out else None


def system_info() -> dict[str, Any]:
    perf_cores = _sysctl("hw.perflevel0.logicalcpu")
    eff_cores = _sysctl("hw.perflevel1.logicalcpu")
    return {
        "hostname": platform.node(),
        "model": _sysctl("hw.model"),
        "chip": _sysctl("machdep.cpu.brand_string"),
        "macos": platform.mac_ver()[0] or None,
        "cpu_count": psutil.cpu_count(),
        "performance_cores": int(perf_cores) if perf_cores and perf_cores.isdigit() else None,
        "efficiency_cores": int(eff_cores) if eff_cores and eff_cores.isdigit() else None,
        "memory_total": psutil.virtual_memory().total,
        "boot_time": psutil.boot_time(),
    }


def memory_pressure() -> dict[str, Any]:
    level = _sysctl("kern.memorystatus_vm_pressure_level")
    free = _sysctl("kern.memorystatus_level")
    return {
        "level": PRESSURE_LEVELS.get(int(level), "unknown") if level and level.isdigit() else None,
        "free_percent": int(free) if free and free.isdigit() else None,
    }


def battery() -> dict[str, Any] | None:
    raw = _run(["ioreg", "-rn", "AppleSmartBattery", "-a"])
    if not raw:
        return None
    try:
        entries = plistlib.loads(raw.encode())
    except Exception:
        return None
    if not entries:
        return None  # desktop Mac, no battery
    b = entries[0]
    design = b.get("DesignCapacity")
    raw_max = b.get("AppleRawMaxCapacity") or b.get("NominalChargeCapacity")
    temp = b.get("Temperature")
    ps = psutil.sensors_battery()
    return {
        "percent": ps.percent if ps else b.get("CurrentCapacity"),
        "plugged_in": bool(b.get("ExternalConnected")),
        "charging": bool(b.get("IsCharging")),
        "cycle_count": b.get("CycleCount"),
        # What System Settings shows as "Maximum Capacity".
        "max_capacity_percent": b.get("MaxCapacity"),
        # Measured full-charge capacity vs. design capacity.
        "health_percent": round(100 * raw_max / design, 1) if design and raw_max else None,
        "design_capacity_mah": design,
        "full_charge_capacity_mah": raw_max,
        "temperature_c": round(temp / 100, 1) if temp else None,
        "minutes_left": (ps.secsleft // 60) if ps and ps.secsleft and ps.secsleft > 0 else None,
    }


def thermal_state() -> str | None:
    """macOS's own thermal verdict, read from Foundation without PyObjC.

    "serious" and "critical" mean the system is actively slowing the Mac down to
    cool it, which on a fanless MacBook Air is how heat shows up.
    """
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send_ptr = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(addr)
        send_long = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)(addr)
        info = send_ptr(objc.objc_getClass(b"NSProcessInfo"), objc.sel_registerName(b"processInfo"))
        state = send_long(info, objc.sel_registerName(b"thermalState"))
        return THERMAL_STATES[state] if 0 <= state < len(THERMAL_STATES) else None
    except Exception:
        return None


def thermal() -> dict[str, Any]:
    out = _run(["pmset", "-g", "therm"]) or ""
    limit = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
    speed_limit = int(limit.group(1)) if limit else 100
    state = thermal_state()
    return {
        "state": state,
        "cpu_speed_limit": speed_limit,
        "throttled": speed_limit < 100 or state in ("serious", "critical"),
    }


def disk() -> dict[str, Any]:
    # On modern macOS "/" is the read-only system volume; user data lives here.
    path = "/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else "/"
    u = psutil.disk_usage(path)
    return {
        "path": path,
        "total": u.total,
        "used": u.used,
        "free": u.free,
        "free_percent": round(100 * u.free / u.total, 1),
    }


def app_name(name: str, exe: str | None = None) -> str:
    """Group helper processes under the app the user knows ("Google Chrome", not
    "Google Chrome Helper (Renderer)")."""
    if exe:
        match = APP_RE.search(exe)  # outermost .app in the path
        if match and not exe.startswith("/System/Library/"):
            return match.group(1)
    if name.startswith("com.apple.WebKit"):
        return "Safari & web views"
    return HELPER_RE.sub("", name) or name


def _process_table(procs: list[psutil.Process]) -> list[dict[str, Any]]:
    rows = []
    for p in procs:
        try:
            with p.oneshot():
                name = p.name()
                try:
                    exe = p.exe()
                except (psutil.AccessDenied, OSError):
                    exe = None
                rows.append({
                    "pid": p.pid,
                    "name": name,
                    "app": app_name(name, exe),
                    "cpu_percent": round(p.cpu_percent(None), 1),
                    "rss": p.memory_info().rss,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return rows


def group_apps(rows: list[dict[str, Any]], top: int = 6) -> list[dict[str, Any]]:
    """Per-app totals: the heaviest apps by memory plus the busiest by CPU."""
    apps: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = apps.setdefault(r["app"], {"name": r["app"], "rss": 0, "cpu_percent": 0.0, "processes": 0})
        a["rss"] += r["rss"]
        a["cpu_percent"] = round(a["cpu_percent"] + r["cpu_percent"], 1)
        a["processes"] += 1
    by_mem = sorted(apps.values(), key=lambda a: a["rss"], reverse=True)[:top]
    by_cpu = sorted(apps.values(), key=lambda a: a["cpu_percent"], reverse=True)[:top]
    picked = {a["name"]: a for a in by_mem + by_cpu}
    return sorted(picked.values(), key=lambda a: a["rss"], reverse=True)


def collect(interval: float = 1.0) -> dict[str, Any]:
    """Take one snapshot. Blocks for `interval` seconds to measure rates."""
    procs = list(psutil.process_iter())
    for p in procs:  # prime per-process CPU counters
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    net0, io0 = psutil.net_io_counters(), psutil.disk_io_counters()

    per_core = psutil.cpu_percent(interval=interval, percpu=True)

    net1, io1 = psutil.net_io_counters(), psutil.disk_io_counters()
    rows = _process_table(procs)
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    load1, load5, load15 = os.getloadavg()

    def rate(a, b, attr):
        if a is None or b is None:
            return None
        return round((getattr(b, attr) - getattr(a, attr)) / interval)

    return {
        "timestamp": time.time(),
        "system": system_info(),
        "cpu": {
            "percent": round(sum(per_core) / len(per_core), 1),
            "per_core": per_core,
            "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        },
        "memory": {
            "total": vm.total,
            "used": vm.total - vm.available,
            "available": vm.available,
            "percent": vm.percent,
            "swap_total": sw.total,
            "swap_used": sw.used,
            "pressure": memory_pressure(),
        },
        "disk": disk(),
        "io": {
            "disk_read_bps": rate(io0, io1, "read_bytes"),
            "disk_write_bps": rate(io0, io1, "write_bytes"),
            "net_recv_bps": rate(net0, net1, "bytes_recv"),
            "net_sent_bps": rate(net0, net1, "bytes_sent"),
        },
        "battery": battery(),
        "thermal": thermal(),
        "top_cpu": sorted(rows, key=lambda r: r["cpu_percent"], reverse=True)[:TOP_N],
        "top_memory": sorted(rows, key=lambda r: r["rss"], reverse=True)[:TOP_N],
        "apps": group_apps(rows),
        "process_count": len(rows),
    }
