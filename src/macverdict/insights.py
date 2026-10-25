"""Turn stored history into answers: who slowed the Mac down, is the RAM enough,
how long will the battery last, and does the Mac throttle.

Every insight works on whatever history exists and says how confident it is, so
it is useful on day one and gets sharper as samples accumulate.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .collectors import app_name
from .fmt import human_bytes

GB = 1024 ** 3
DAY = 86400
# Apple's rating: 80% of original capacity after 1000 full cycles.
APPLE_CYCLES, APPLE_FLOOR = 1000, 80.0
TYPICAL_WEAR_PER_CYCLE = (100 - APPLE_FLOOR) / APPLE_CYCLES
HEALTH_WINDOW_DAYS = 3   # health = median reading over this many days
WEAR_NOISE = 1.5         # percentage points of wear needed before forecasting
RAM_TIERS_GB = (8, 16, 24, 32, 48, 64, 96, 128)


@dataclass
class Insight:
    key: str
    title: str
    headline: str
    level: str  # "good", "ok" or "attention"
    details: list[str] = field(default_factory=list)
    confidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- helpers

def _snap(row: dict) -> dict:
    return row.get("snapshot") or {}


def _span_days(rows: list[dict]) -> float:
    return (rows[-1]["ts"] - rows[0]["ts"]) / DAY if len(rows) > 1 else 0.0


def _confidence(rows: list[dict], want_days: float = 7) -> str:
    days = _span_days(rows)
    if days >= 1:
        span = f"{days:.1f} days"
    elif days * 24 >= 1:
        span = f"{days * 24:.1f} hours"
    else:
        span = f"{max(1, round(days * 1440))} minutes"
    note = f"Based on {len(rows)} samples over {span}."
    if days < want_days * 0.95:  # a week of 15-minute samples spans just under 7 days
        note += f" Early read: it gets reliable after about {want_days:g} days of normal use."
    return note


def apps_in(snap: dict, by: str) -> list[dict]:
    """Per-app totals in a snapshot, sorted by `by` ("rss" or "cpu_percent").

    Falls back to grouping the stored top processes for samples saved before
    per-app totals existed.
    """
    return _apps({"snapshot": snap}, by)


def _apps(row: dict, by: str) -> list[dict]:
    snap = _snap(row)
    if snap.get("apps"):
        apps = snap["apps"]
    else:
        merged: dict[str, dict] = {}
        for p in snap.get("top_memory", []) + snap.get("top_cpu", []):
            name = app_name(p["name"])
            a = merged.setdefault(name, {"name": name, "rss": 0, "cpu_percent": 0.0, "_pids": set()})
            if p["pid"] not in a["_pids"]:
                a["_pids"].add(p["pid"])
                a["rss"] += p["rss"]
                a["cpu_percent"] += p["cpu_percent"]
        apps = list(merged.values())
    return sorted(apps, key=lambda a: a[by], reverse=True)


def _day(ts: float):
    return datetime.fromtimestamp(ts).date()


def _fmt_days(days: list) -> str:
    """[Oct 19, Oct 20] -> "Oct 19–20"; otherwise a short comma list."""
    days = sorted(set(days))
    if not days:
        return ""
    consecutive = all((b - a).days == 1 for a, b in zip(days, days[1:]))
    if len(days) > 1 and consecutive and days[0].month == days[-1].month:
        return f"{days[0]:%b} {days[0].day}–{days[-1].day}"
    return ", ".join(f"{d:%b} {d.day}" for d in days)


def _round_half_gb(n: float) -> str:
    return f"{max(0.5, round(n / GB * 2) / 2):g} GB"


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "0%"


def hardware_model() -> str | None:
    """e.g. "MacBook Air". Slow-ish (~0.5 s), so only used for insights."""
    try:
        out = subprocess.run(["system_profiler", "SPHardwareDataType", "-json"],
                             capture_output=True, timeout=10, check=True).stdout
        return json.loads(out)["SPHardwareDataType"][0].get("machine_name")
    except Exception:
        return None


# --------------------------------------------------------------------------- 1. blame

def _stress_reasons(row: dict, prev: dict | None) -> set[str]:
    reasons = set()
    if row.get("pressure") in ("warn", "critical"):
        reasons.add("memory")
    if prev and (row.get("swap_used") or 0) - (prev.get("swap_used") or 0) >= 256 * 1024 ** 2:
        reasons.add("memory")  # swap grew: the Mac ran out of room since the last sample
    if (row.get("cpu_percent") or 0) >= 80:
        reasons.add("cpu")
    if row.get("throttled"):
        reasons.add("heat")
    return reasons


def slowdown_blame(rows: list[dict]) -> Insight:
    """Blame the app that was the single biggest consumer at each slowdown moment.

    Counting every app in the top few equally would blame whatever is always open
    (Finder, Slack) as much as the app actually eating the memory.
    """
    title = "What's slowing you down"
    moments = 0
    lead: Counter = Counter()        # biggest consumer at that moment
    involved: Counter = Counter()    # also using a meaningful share
    why: dict[str, Counter] = defaultdict(Counter)
    usage: dict[str, list[float]] = defaultdict(list)
    moment_days: list = []
    prev = None
    for row in rows:
        reasons = _stress_reasons(row, prev)
        prev = row
        if not reasons:
            continue
        moments += 1
        moment_days.append(_day(row["ts"]))
        total = _snap(row).get("system", {}).get("memory_total") or 16 * GB
        leaders, others = set(), set()
        if "memory" in reasons:
            apps = _apps(row, "rss")
            if apps:
                leaders.add(apps[0]["name"])
                why[apps[0]["name"]]["memory"] += 1
                usage[apps[0]["name"]].append(apps[0]["rss"])
                others.update(a["name"] for a in apps[1:3] if a["rss"] >= 0.05 * total)
        if reasons & {"cpu", "heat"}:
            apps = [a for a in _apps(row, "cpu_percent") if a["cpu_percent"] >= 20]
            if apps:
                leaders.add(apps[0]["name"])
                why[apps[0]["name"]]["CPU"] += 1
                others.update(a["name"] for a in apps[1:3])
        lead.update(leaders)
        involved.update((leaders | others))

    if not moments:
        everyday = Counter()
        for row in rows:
            for a in _apps(row, "rss")[:1]:
                everyday[a["name"]] += 1
        top = ", ".join(n for n, _ in everyday.most_common(3)) or "n/a"
        return Insight("blame", title, f"No slowdowns recorded in {len(rows)} samples.", "good",
                       [f"Usually your biggest memory user: {top}.",
                        "A slowdown here means memory pressure, swap growing, CPU above 80%, or thermal throttling."],
                       _confidence(rows))

    worst, worst_count = lead.most_common(1)[0]
    kind = why[worst].most_common(1)[0][0]
    details = []
    for name, count in lead.most_common(3):
        kinds = " and ".join(k for k, _ in why[name].most_common())
        mem = f", typically {human_bytes(statistics.median(usage[name]))}" if usage.get(name) else ""
        details.append(f"{name}: the biggest {kinds} user in {_pct(count, moments)} of slowdowns{mem}.")
    all_days = {_day(r["ts"]) for r in rows}
    if len(set(moment_days)) <= 3 and len(all_days) >= 4:
        details.insert(0, f"All on {_fmt_days(moment_days)}; the other {len(all_days - set(moment_days))} days "
                          "had none.")
    supporting = [n for n, _ in involved.most_common() if n not in lead][:2]
    if supporting:
        details.append("Also contributing: " + ", ".join(supporting) + ".")
    details.append(f"Biggest win: quit or trim {worst} when your Mac feels slow "
                   "(fewer tabs, or restart it if it has been open for days).")
    return Insight("blame", title,
                   f"{worst} was the biggest {kind} user in {_pct(worst_count, moments)} "
                   f"of your {moments} slowdown moments.",
                   "attention" if moments / len(rows) >= 0.1 else "ok", details, _confidence(rows))


# --------------------------------------------------------------------------- 2. RAM verdict

def _next_tier(gb: int) -> int:
    return next((t for t in RAM_TIERS_GB if t > gb), gb * 2)


def _elevated(row: dict) -> bool:
    return row.get("pressure") in ("warn", "critical")


def ram_verdict(rows: list[dict], memory_total: int) -> Insight:
    """Judge RAM by how many *days* ran short, and by which app was to blame.

    Counting samples alone lets one heavy afternoon outweigh a quiet week.
    """
    title = "Do you need more RAM?"
    gb = round(memory_total / GB)
    n = len(rows)
    by_day: dict = defaultdict(list)
    for r in rows:
        by_day[_day(r["ts"])].append(r)
    n_days = len(by_day)
    # A day counts once it has two elevated samples: one blip isn't running short.
    pressure_days = [d for d, rs in sorted(by_day.items()) if sum(map(_elevated, rs)) >= 2]
    critical_days = [d for d, rs in sorted(by_day.items()) if any(r.get("pressure") == "critical" for r in rs)]
    swaps = sorted(r.get("swap_used") or 0 for r in rows)
    p90_swap = swaps[min(n - 1, int(0.9 * n))] if swaps else 0
    peak_swap = swaps[-1] if swaps else 0
    growth = sum(1 for a, b in zip(rows, rows[1:])
                 if (b.get("swap_used") or 0) - (a.get("swap_used") or 0) >= 256 * 1024 ** 2)

    # Which app was the biggest memory user whenever pressure was elevated?
    elevated_rows = [r for r in rows if _elevated(r)]
    leaders = Counter()
    sizes: dict[str, list[int]] = defaultdict(list)
    for r in elevated_rows:
        apps = _apps(r, "rss")
        if apps:
            leaders[apps[0]["name"]] += 1
            sizes[apps[0]["name"]].append(apps[0]["rss"])
    trigger = None
    if leaders:
        name, count = leaders.most_common(1)[0]
        if count / len(elevated_rows) >= 0.7:
            trigger = name
    if trigger:
        hot = sorted(sizes[trigger])
        threshold = hot[len(hot) // 4]  # it was at least this big in 3 out of 4 short moments
        calm = [a["rss"] for r in rows if not _elevated(r) for a in _apps(r, "rss") if a["name"] == trigger]
        # Only name a size to watch for if the app really was bigger than usual when it hurt.
        clear_threshold = not calm or threshold >= 1.25 * statistics.median(calm)

    if pressure_days:
        line = f"Memory pressure ran high on {len(pressure_days)} of {n_days} days"
        if len(pressure_days) <= 3:
            line += f" ({_fmt_days(pressure_days)})"
        since = (max(by_day) - pressure_days[-1]).days
        line += f"; none in the {since} days since." if since >= 2 else "."
        details = [line]
    elif elevated_rows:
        details = ["Memory pressure only blipped briefly, never for long."]
    else:
        details = ["Memory pressure never went above normal."]
    if peak_swap:
        details.append(f"Swap: typically under {human_bytes(p90_swap)}, peak {human_bytes(peak_swap)}. "
                       "Swap is only cleared by a restart, so a past spike lingers.")
    else:
        details.append("No swap used: everything fit in RAM.")

    # "Not enough" needs breadth (most days) and depth (a real share of the time).
    most_days = n_days >= 3 and len(pressure_days) / n_days >= 0.5 and len(elevated_rows) / n >= 0.10
    if most_days or len(critical_days) >= 2 or p90_swap >= 4 * GB:
        level = "attention"
        headline = f"{gb} GB isn't enough for how you work. Your next Mac should have {_next_tier(gb)} GB."
        if trigger:
            details.append(f"{trigger} was the biggest memory user every time, typically "
                           f"{human_bytes(statistics.median(sizes[trigger]))}: trimming it is the free fix.")
        details.append("Until then: close what you're not using, and restart every few days to clear swap.")
    elif pressure_days and trigger and clear_threshold:
        level = "ok"
        headline = f"{gb} GB is enough, except when {trigger} grows past about {_round_half_gb(threshold)}."
        usual = f" It usually sits around {human_bytes(statistics.median(calm))}." if calm else ""
        details.append(f"Every time you ran short, {trigger} was the biggest memory user.{usual} "
                       f"Restart it or close tabs when it passes {_round_half_gb(threshold)}; no new Mac needed.")
    elif pressure_days or p90_swap >= GB or growth >= max(3, n // 50):
        level = "ok"
        headline = (f"{gb} GB is enough for how you work; you only ran short on a few days."
                    if pressure_days and len(pressure_days) / n_days < 0.5 else
                    f"{gb} GB is just enough: you ran short on {len(pressure_days)} of {n_days} days, but only briefly."
                    if pressure_days else f"{gb} GB is just enough: you only run short occasionally.")
        if trigger:
            details.append(f"Each time, {trigger} was the biggest memory user "
                           f"(typically {human_bytes(statistics.median(sizes[trigger]))}); closing tabs or "
                           f"restarting it is the quickest fix.")
        details.append(f"Heavier future workloads (more tabs, VMs, local AI models) would push you to "
                       f"{_next_tier(gb)} GB.")
    else:
        level, headline = "good", f"{gb} GB is plenty for how you use this Mac."
        details.append("macOS reports high “memory used” because it caches files; pressure and swap are "
                       "what matter, and yours stay low.")
    return Insight("ram", title, headline, level, details, _confidence(rows, want_days=7))


# --------------------------------------------------------------------------- 3. battery forecast

def battery_forecast(rows: list[dict], now: float | None = None) -> Insight | None:
    """Forecast battery wear, without mistaking measurement noise for wear.

    A battery's measured full-charge capacity drifts by a percent or so between
    readings (temperature, charge level, calibration), so health is the median of
    the last few days, and no forecast is made until wear clearly exceeds that drift.
    """
    title = "How long will your battery last?"
    samples = [(r["ts"], _snap(r).get("battery") or {}) for r in rows]
    samples = [(ts, b) for ts, b in samples if b.get("cycle_count") is not None]
    if not samples:
        return None
    now = now or time.time()
    ts_last, b = samples[-1]
    cycles = b["cycle_count"]
    recent = [bb["health_percent"] for ts, bb in samples
              if ts >= ts_last - HEALTH_WINDOW_DAYS * DAY and bb.get("health_percent") is not None]
    health = statistics.median(recent) if recent else b.get("max_capacity_percent")
    if health is None:
        return None
    apple = b.get("max_capacity_percent")  # what System Settings shows
    shown = apple if apple is not None else round(health)
    details = [f"Maximum capacity {shown}% after {cycles} cycles "
               f"(Apple rates batteries for {APPLE_FLOOR:.0f}% at {APPLE_CYCLES} cycles)."]
    confidence = _confidence(rows, want_days=14)

    if (apple if apple is not None else health) <= APPLE_FLOOR:
        return Insight("battery", title, f"Your battery is at {shown}%: Apple considers it consumed.",
                       "attention", details + ["A battery replacement will bring back full runtime."], confidence)

    worn = 100 - health
    if worn < WEAR_NOISE or cycles < 20:
        if len(recent) > 1 and max(recent) - min(recent) >= 0.5:
            details.append(f"Its measured capacity drifts by about ±{(max(recent) - min(recent)) / 2:.1f}% between "
                           "readings, so a forecast waits until wear is clearly larger than that.")
        return Insight("battery", title, f"Healthy: {shown}% capacity, with too little wear to measure yet.",
                       "good", details, confidence)

    wear = worn / cycles
    pace = wear / TYPICAL_WEAR_PER_CYCLE
    details.append(f"It's wearing {'slower' if pace < 1 else 'faster'} than Apple's rating: "
                   f"{wear * 100:.1f}% of capacity per 100 cycles, vs {TYPICAL_WEAR_PER_CYCLE * 100:.0f}% expected.")
    # Never assume better than 4x Apple's rating: early wear estimates are rough.
    cycles_to_floor = (health - APPLE_FLOOR) / max(wear, TYPICAL_WEAR_PER_CYCLE / 4)

    ts_first, b_first = samples[0]
    days = (ts_last - ts_first) / DAY
    per_day = (cycles - b_first["cycle_count"]) / days if days >= 7 and cycles > b_first["cycle_count"] else None
    if per_day:
        eta = datetime.fromtimestamp(now + cycles_to_floor / per_day * DAY)
        details.append(f"You use about {per_day * 7:.1f} cycles a week.")
        level = "attention" if (eta.timestamp() - now) / DAY < 365 else "good"
        headline = f"At your pace, it reaches {APPLE_FLOOR:.0f}% capacity around {eta:%B %Y}."
    else:
        level = "good" if health >= 90 else "ok"
        headline = (f"Healthy: {shown}% capacity, roughly {round(cycles_to_floor, -2):,.0f} "
                    f"more cycles before {APPLE_FLOOR:.0f}%.")
    hot = sum(1 for _, bb in samples if (bb.get("temperature_c") or 0) >= 35)
    if hot / len(samples) >= 0.1:
        details.append(f"The battery was above 35°C in {_pct(hot, len(samples))} of samples; heat is what ages it fastest.")
    if not per_day:
        details.append("A dated forecast appears once there's a week of history with some charging.")
    return Insight("battery", title, headline, level, details, confidence)


# --------------------------------------------------------------------------- 4. throttling

def throttling(rows: list[dict], model: str | None) -> Insight:
    is_air = bool(model and "MacBook Air" in model)
    title = "Is your Air overheating?" if is_air else "Does your Mac overheat?"
    measured = [r for r in rows if "state" in _snap(r).get("thermal", {})] or rows
    n = len(measured)
    throttled = [r for r in measured if r.get("throttled")]
    warm = [r for r in measured if _snap(r).get("thermal", {}).get("state") == "fair"]
    details = [f"Throttled in {_pct(len(throttled), n)} of samples; warm (not yet slowing) in {_pct(len(warm), n)}."]

    busy = Counter()
    for r in throttled + warm:
        for a in _apps(r, "cpu_percent")[:2]:
            if a["cpu_percent"] >= 20:
                busy[a["name"]] += 1
    if busy:
        details.append("Busiest apps when it ran hot: " + ", ".join(n for n, _ in busy.most_common(3)) + ".")
    temps = [r["battery_temp_c"] for r in measured if r.get("battery_temp_c")]
    if temps:
        details.append(f"Battery temperature (a proxy for chassis heat): typical {statistics.median(temps):.0f}°C, "
                       f"peak {max(temps):.0f}°C.")

    share = len(throttled) / n if n else 0
    if not throttled:
        level = "good"
        headline = f"No throttling in {n} samples." + (" Your fanless Air is keeping up." if is_air else "")
    elif share < 0.05:
        level = "ok"
        headline = f"Occasional throttling ({_pct(len(throttled), n)} of samples)."
    else:
        level = "attention"
        headline = f"Your Mac slowed itself down to cool off in {_pct(len(throttled), n)} of samples."
    if throttled and is_air:
        details.append("The Air has no fan, so long exports, builds or games will always throttle eventually. "
                       "A hard, cool surface helps; for sustained heavy work, a MacBook Pro's fan is the real fix.")
    return Insight("thermal", title, headline, level, details, _confidence(rows))


# --------------------------------------------------------------------------- all

def build(rows: list[dict], memory_total: int, model: str | None = None) -> list[Insight]:
    if not rows:
        return []
    out = [slowdown_blame(rows), ram_verdict(rows, memory_total)]
    battery = battery_forecast(rows)
    if battery:
        out.append(battery)
    out.append(throttling(rows, model))
    return out
