"""Self-contained HTML report (no JavaScript, no CDN) built from stored history."""

from __future__ import annotations

import html
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .fmt import days_label, human_bytes, human_duration
from .insights import apps_in

GB = 1024 ** 3

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--accent:#2f6fde;
--ok:#1f9d55;--info:#2a7ab0;--warn:#c47f00;--critical:#d1352b}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--ink:#f2f2f4;--muted:#9a9aa0;
--line:#2e2e33;--accent:#6b9cff;--ok:#43c47a;--info:#5aaee0;--warn:#e3a72f;--critical:#ff6b5e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;margin:0 0 4px;letter-spacing:-.02em}h2{font-size:17px;margin:32px 0 12px}
.sub{color:var(--muted);margin:0 0 24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.tile,.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.tile .k{color:var(--muted);font-size:13px}.tile .v{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.card h3{margin:0 0 4px;font-size:14px}.card .now{color:var(--muted);font-size:13px;margin-bottom:8px}
svg{width:100%;height:auto;display:block}
.f{display:flex;gap:10px;padding:10px 0;border-top:1px solid var(--line)}.f:first-child{border-top:0}
.pill{flex:none;font-size:11px;font-weight:600;text-transform:uppercase;border-radius:6px;padding:2px 7px;
height:fit-content;color:#fff}.ok{background:var(--ok)}.info{background:var(--info)}
.warn{background:var(--warn)}.critical{background:var(--critical)}
.adv{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
td,th{text-align:left;padding:6px 4px;border-top:1px solid var(--line);font-size:14px}th{color:var(--muted);font-weight:500}
.ins .k{color:var(--muted);font-size:13px}.ins .hl{font-size:17px;font-weight:600;margin:4px 0 8px;line-height:1.35}
.ok-t{color:var(--ok)}.info-t{color:var(--info)}.warn-t{color:var(--warn)}
.ins ul{margin:0 0 8px;padding-left:18px;font-size:14px}.ins li{margin:3px 0}
footer{margin-top:40px;color:var(--muted);font-size:13px}
"""


GAP_SECONDS = 2 * 3600  # a longer gap between samples means the Mac was asleep or off


def _segments(pts: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Split into runs of consecutive samples, so lines don't bridge sleep."""
    segs = [[pts[0]]]
    for prev, cur in zip(pts, pts[1:]):
        if cur[0] - prev[0] > GAP_SECONDS:
            segs.append([])
        segs[-1].append(cur)
    return segs


def _ticks(t0: float, t1: float) -> list[tuple[float, str]]:
    """Midnight ticks labelled "Mon 19" (thinned for long spans); 6-hour ticks for short ones."""
    start = datetime.fromtimestamp(t0).replace(hour=0, minute=0, second=0, microsecond=0)
    if t1 - t0 < 2 * 86400:
        step, fmt = timedelta(hours=6), "%H:%M"
    else:
        step, fmt = timedelta(days=1), "%a %-d"
    ticks, t = [], start
    while t.timestamp() <= t1:
        if t.timestamp() >= t0:
            ticks.append((t.timestamp(), t.strftime(fmt)))
        t += step
    every = max(1, len(ticks) // 7)
    return ticks[::every]


def _chart(points: list[tuple[float, float]], lo: float = 0, hi: float | None = None,
           unit: str = "", color: str = "var(--accent)",
           bands: list[tuple[float, float]] | None = None) -> str:
    """Line chart with day labels, gaps where the Mac slept, and optional shaded bands."""
    w, plot_h, axis_h, pad = 320, 110, 14, 4
    h = plot_h + axis_h
    pts = [(t, v) for t, v in points if v is not None]
    if len(pts) < 2:
        return '<p class="adv">Not enough data yet. Keep recording.</p>'
    t0, t1 = pts[0][0], pts[-1][0]
    hi = hi if hi is not None else max(v for _, v in pts) * 1.1 or 1
    span_t, span_v = (t1 - t0) or 1, (hi - lo) or 1
    base_y = plot_h - pad

    def x_of(t):
        return pad + (t - t0) / span_t * (w - 2 * pad)

    def y_of(v):
        return plot_h - pad - (min(max(v, lo), hi) - lo) / span_v * (plot_h - 2 * pad)

    shading = "".join(
        f'<rect x="{x_of(a):.1f}" y="{pad}" width="{max(1.5, x_of(b) - x_of(a)):.1f}" '
        f'height="{plot_h - 2 * pad}" fill="var(--warn)" opacity=".18"/>'
        for a, b in bands or [])
    grid = "".join(f'<line x1="0" x2="{w}" y1="{y}" y2="{y}" stroke="var(--line)"/>'
                   for y in (pad, plot_h / 2, base_y))
    ticks = "".join(
        f'<line x1="{x_of(t):.1f}" x2="{x_of(t):.1f}" y1="{pad}" y2="{base_y}" stroke="var(--line)" '
        f'stroke-dasharray="2 3"/>'
        f'<text x="{x_of(t):.1f}" y="{h - 2}" text-anchor="middle" font-size="9" fill="var(--muted)">{label}</text>'
        for t, label in _ticks(t0, t1))
    lines = ""
    for seg in _segments(pts):
        coords = " ".join(f"{x_of(t):.1f},{y_of(v):.1f}" for t, v in seg)
        if len(seg) == 1:
            x, y = x_of(seg[0][0]), y_of(seg[0][1])
            lines += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="{color}"/>'
            continue
        lines += (f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.6" '
                  f'stroke-linejoin="round" stroke-linecap="round"/>')
    labels = (f'<text x="{w}" y="12" text-anchor="end" font-size="10" fill="var(--muted)">{hi:.0f}{unit}</text>'
              f'<text x="{w}" y="{base_y - 3}" text-anchor="end" font-size="10" fill="var(--muted)">{lo:.0f}{unit}</text>')
    return f'<svg viewBox="0 0 {w} {h}" role="img">{shading}{grid}{ticks}{lines}{labels}</svg>'


def _pressure_bands(rows: list[dict]) -> list[tuple[float, float]]:
    """Time spans where memory pressure was elevated (each sample shades until the next)."""
    bands = []
    for i, r in enumerate(rows):
        if r.get("pressure") in ("warn", "critical"):
            nxt = rows[i + 1]["ts"] if i + 1 < len(rows) else r["ts"] + 900
            bands.append((r["ts"], min(nxt, r["ts"] + 900)))
    return bands


def _series(rows: list[dict], key: str, scale: float = 1) -> list[tuple[float, float]]:
    return [(r["ts"], r[key] / scale if r[key] is not None else None) for r in rows]


def _card(title: str, now: str, chart: str) -> str:
    return f'<div class="card"><h3>{title}</h3><div class="now">{html.escape(now)}</div>{chart}</div>'


def _avg(vals: list[Any]) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def build(rows: list[dict], latest_snapshot: dict, findings: list, days: float,
          insights: list | None = None) -> str:
    sysinfo = latest_snapshot["system"]
    last = rows[-1] if rows else {}
    # Count each app at most once per sample, so no app can exceed the sample count.
    snaps = [r["snapshot"] for r in rows if r.get("snapshot")]
    offenders = Counter(name for snap in snaps for name in
                        {a["name"] for a in apps_in(snap, "cpu_percent")[:3] if a["cpu_percent"] >= 10})
    mem_hogs = Counter(name for snap in snaps for name in {a["name"] for a in apps_in(snap, "rss")[:3]})

    def stat(label, value):
        return f'<div class="tile"><div class="k">{label}</div><div class="v">{value}</div></div>'

    avg_cpu = _avg([r["cpu_percent"] for r in rows])
    peak_swap = max((r["swap_used"] or 0 for r in rows), default=0)
    throttled = sum(r["throttled"] or 0 for r in rows)
    tiles = "".join([
        stat("Health score now", last.get("score", "–")),
        stat(f"Avg CPU ({days:g}d)", f"{avg_cpu:.0f}%" if avg_cpu is not None else "–"),
        stat("Peak swap", human_bytes(peak_swap)),
        stat("Battery capacity", f"{last['battery_health']:.0f}%" if last.get("battery_health") else "–"),
        stat("Throttled samples", f"{throttled} / {len(rows)}"),
    ])

    charts = "".join([
        _card("Health score", f"now {last.get('score', '–')}", _chart(_series(rows, "score"), 0, 100, "", "var(--ok)")),
        _card("CPU", f"now {last.get('cpu_percent', 0):.0f}%", _chart(_series(rows, "cpu_percent"), 0, 100, "%")),
        _card("Memory used", f"now {last.get('mem_percent', 0):.0f}%, pressure {last.get('pressure')}"
                            + (" · shaded: memory pressure" if any(r.get("pressure") in ("warn", "critical") for r in rows) else ""),
              _chart(_series(rows, "mem_percent"), 0, 100, "%", "var(--info)", _pressure_bands(rows))),
        _card("Swap used", f"now {human_bytes(last.get('swap_used'))}",
              _chart(_series(rows, "swap_used", GB), 0, None, " GB", "var(--warn)")),
        _card("Disk free", f"now {last.get('disk_free_percent', 0):.0f}%",
              _chart(_series(rows, "disk_free_percent"), 0, 100, "%", "var(--info)")),
        _card("Battery charge", f"now {last.get('battery_percent') or 0:.0f}%",
              _chart(_series(rows, "battery_percent"), 0, 100, "%", "var(--ok)")),
        _card("Battery temperature", f"now {last.get('battery_temp_c') or 0:.1f}°C",
              _chart(_series(rows, "battery_temp_c"), 20, 50, "°", "var(--critical)")),
    ])

    finding_html = "".join(
        f'<div class="f"><span class="pill {f.level}">{f.level}</span><div>{html.escape(f.message)}'
        f'{"<div class=adv>" + html.escape(f.advice) + "</div>" if f.advice else ""}</div></div>'
        for f in findings)

    def table(counter: Counter, label: str) -> str:
        if not counter:
            return '<p class="adv">No data yet.</p>'
        body = "".join(f"<tr><td>{html.escape(n)}</td><td>{c} ({100 * c / len(snaps):.0f}%)</td></tr>"
                       for n, c in counter.most_common(8))
        return f"<table><tr><th>App</th><th>{label}</th></tr>{body}</table>"

    level_class = {"good": "ok", "ok": "info", "attention": "warn"}
    insight_html = "".join(
        f'<div class="card ins"><div class="k">{html.escape(i.title)}</div>'
        f'<div class="hl {level_class[i.level]}-t">{html.escape(i.headline)}</div>'
        f'<ul>{"".join(f"<li>{html.escape(d)}</li>" for d in i.details)}</ul>'
        f'<div class="adv">{html.escape(i.confidence)}</div></div>'
        for i in insights or [])
    insight_section = f'<h2>Insights</h2><div class="grid">{insight_html}</div>' if insight_html else ""

    span = f"{len(rows)} samples over the last {days_label(days)}" if rows else "no stored history yet"
    uptime = human_duration(latest_snapshot["timestamp"] - sysinfo["boot_time"])
    generated = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>macpulse report</title>
<style>{CSS}</style></head><body><main>
<h1>macpulse report</h1>
<p class="sub">{html.escape(sysinfo.get('chip') or '')} · {html.escape(sysinfo.get('model') or '')} · macOS {html.escape(sysinfo.get('macos') or '')}
· {human_bytes(sysinfo['memory_total'])} RAM · up {uptime} · {span}</p>
<div class="tiles">{tiles}</div>
{insight_section}
<h2>Right now</h2><div class="card">{finding_html}</div>
<h2>Trends</h2><div class="grid">{charts}</div>
<h2>Repeat offenders</h2><div class="grid">
<div class="card"><h3>Most often hogging CPU</h3>{table(offenders, f"Samples in top 3, of {len(snaps)}")}</div>
<div class="card"><h3>Most often hogging memory</h3>{table(mem_hogs, f"Samples in top 3, of {len(snaps)}")}</div></div>
<footer>Generated {generated} by macpulse.</footer>
</main></body></html>"""
