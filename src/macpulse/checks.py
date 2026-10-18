"""Turn a snapshot into findings with plain-English advice, and a 0-100 score."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .fmt import human_bytes

GB = 1024 ** 3
LEVELS = ("ok", "info", "warn", "critical")
PENALTY = {"ok": 0, "info": 2, "warn": 10, "critical": 25}


@dataclass
class Finding:
    level: str  # one of LEVELS
    area: str
    message: str
    advice: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cpu(s: dict) -> list[Finding]:
    cpu, cores = s["cpu"], s["system"]["cpu_count"] or 1
    load_ratio = cpu["load_avg"][1] / cores
    out = []
    if cpu["percent"] >= 90:
        out.append(Finding("warn", "cpu", f"CPU is busy at {cpu['percent']:.0f}%.",
                           "Check the top CPU processes below; quit anything you are not using."))
    if load_ratio >= 1.5:
        out.append(Finding("warn", "cpu", f"5-minute load is {cpu['load_avg'][1]:.1f} on {cores} cores.",
                           "Sustained load above core count means work is queuing up."))
    if not out:
        out.append(Finding("ok", "cpu", f"CPU {cpu['percent']:.0f}%, load {cpu['load_avg'][1]:.1f} on {cores} cores."))
    for p in s.get("top_cpu", [])[:1]:
        if p["cpu_percent"] >= 80:
            out.append(Finding("info", "cpu", f"{p['name']} (pid {p['pid']}) is using {p['cpu_percent']:.0f}% CPU.",
                               "Expected if it is doing real work; otherwise it may be stuck."))
    return out


def _memory(s: dict) -> list[Finding]:
    m = s["memory"]
    level = (m.get("pressure") or {}).get("level")
    swap = m["swap_used"]
    out = []
    if level == "critical":
        out.append(Finding("critical", "memory", "Memory pressure is CRITICAL.",
                           "macOS is compressing and swapping heavily. Close large apps or browser tabs."))
    elif level == "warn":
        out.append(Finding("warn", "memory", "Memory pressure is elevated.",
                           "Close apps you are not using; this is the main cause of slowdowns on 8-16 GB Macs."))
    if swap >= 6 * GB:
        out.append(Finding("critical", "memory", f"Swap in use: {human_bytes(swap)}.",
                           "Heavy swapping wears the SSD and slows everything. A restart clears it."))
    elif swap >= 2 * GB:
        out.append(Finding("warn", "memory", f"Swap in use: {human_bytes(swap)}.",
                           "Some swapping is normal, but sustained growth means you need more free RAM."))
    if not out:
        out.append(Finding("ok", "memory", f"Memory pressure {level or 'n/a'}, {m['percent']:.0f}% used, "
                                           f"swap {human_bytes(swap)}."))
    biggest = (s.get("apps") or s.get("top_memory") or [])[:1]
    for p in biggest:
        if p["rss"] >= 0.25 * m["total"]:
            out.append(Finding("info", "memory", f"{p['name']} is using {human_bytes(p['rss'])} of RAM.",
                               "Restarting a long-running app often returns leaked memory."))
    return out


def _disk(s: dict) -> list[Finding]:
    d = s["disk"]
    pct, free = d["free_percent"], d["free"]
    if pct < 5 or free < 10 * GB:
        return [Finding("critical", "disk", f"Only {human_bytes(free)} free ({pct:.0f}%).",
                        "macOS needs free space for swap and updates. Clear Downloads, caches, or old iOS backups.")]
    if pct < 15:
        return [Finding("warn", "disk", f"Disk is getting full: {human_bytes(free)} free ({pct:.0f}%).",
                        "Use System Settings > General > Storage to find large files.")]
    return [Finding("ok", "disk", f"{human_bytes(free)} free ({pct:.0f}%).")]


def _battery(s: dict) -> list[Finding]:
    b = s.get("battery")
    if not b:
        return []
    out = []
    health = b.get("max_capacity_percent") or b.get("health_percent")
    if health is not None and health < 80:
        out.append(Finding("warn", "battery", f"Battery maximum capacity is {health:.0f}%.",
                           "Apple considers batteries below 80% consumed; consider a service appointment."))
    if (b.get("cycle_count") or 0) >= 1000:
        out.append(Finding("info", "battery", f"Battery has {b['cycle_count']} cycles (rated for 1000).", ""))
    temp = b.get("temperature_c")
    if temp is not None and temp >= 45:
        out.append(Finding("critical", "battery", f"Battery is hot: {temp:.0f}°C.",
                           "Move the Mac off soft surfaces and out of the sun; stop heavy work until it cools."))
    elif temp is not None and temp >= 38:
        out.append(Finding("warn", "battery", f"Battery is warm: {temp:.0f}°C.",
                           "The MacBook Air is fanless, so sustained heat leads to throttling."))
    if not out:
        out.append(Finding("ok", "battery", f"{b['percent']:.0f}% charged, capacity {health}%, "
                                            f"{b.get('cycle_count')} cycles, {temp}°C."))
    return out


def _thermal(s: dict) -> list[Finding]:
    t = s["thermal"]
    state = t.get("state")
    if state == "critical":
        return [Finding("critical", "thermal", "macOS reports CRITICAL heat and is slowing the Mac hard.",
                        "Stop heavy work now and give it airflow: hard surface, out of the sun.")]
    if t["throttled"]:
        detail = f"limited to {t['cpu_speed_limit']}% speed" if t["cpu_speed_limit"] < 100 else "thermal state is serious"
        return [Finding("warn", "thermal", f"Your Mac is slowing itself down to cool off ({detail}).",
                        "Reduce load or improve airflow. On a fanless MacBook Air this is the main cause of "
                        "slowdowns during long heavy tasks.")]
    if state == "fair":
        return [Finding("info", "thermal", "Your Mac is warm (thermal state: fair).",
                        "Not slowing down yet, but sustained heavy work may start throttling.")]
    return [Finding("ok", "thermal", "No thermal throttling.")]


def _uptime(s: dict) -> list[Finding]:
    days = (s["timestamp"] - s["system"]["boot_time"]) / 86400
    if days >= 14:
        return [Finding("info", "uptime", f"Up for {days:.0f} days.",
                        "A restart clears swap, leaked memory and pending updates.")]
    return []


RULES = (_cpu, _memory, _disk, _battery, _thermal, _uptime)


def evaluate(snapshot: dict) -> list[Finding]:
    findings = [f for rule in RULES for f in rule(snapshot)]
    return sorted(findings, key=lambda f: -LEVELS.index(f.level))


def health_score(findings: list[Finding]) -> int:
    return max(0, 100 - sum(PENALTY[f.level] for f in findings))


def worst_level(findings: list[Finding]) -> str:
    return max((f.level for f in findings), key=LEVELS.index, default="ok")
