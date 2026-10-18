"""Self-contained HTML report (no JavaScript, no CDN) built from stored history."""

from __future__ import annotations

import html
import time
from collections import Counter
from datetime import datetime
from typing import Any

from .fmt import days_label, human_bytes, human_duration

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


def _chart(points: list[tuple[float, float]], lo: float = 0, hi: float | None = None,
           unit: str = "", color: str = "var(--accent)") -> str:
    w, h, pad = 320, 110, 4
    pts = [(t, v) for t, v in points if v is not None]
    if len(pts) < 2:
        return '<p class="adv">Not enough data yet. Keep recording.</p>'
    t0, t1 = pts[0][0], pts[-1][0]
    hi = hi if hi is not None else max(v for _, v in pts) * 1.1 or 1
    span_t, span_v = (t1 - t0) or 1, (hi - lo) or 1

    def xy(t, v):
        return pad + (t - t0) / span_t * (w - 2 * pad), h - pad - (min(max(v, lo), hi) - lo) / span_v * (h - 2 * pad)

    coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(t, v) for t, v in pts))
    first_x, last_x = xy(t0, lo)[0], xy(t1, lo)[0]
    base_y = h - pad
    grid = "".join(f'<line x1="0" x2="{w}" y1="{y}" y2="{y}" stroke="var(--line)"/>'
                   for y in (pad, h / 2, h - pad))
    labels = (f'<text x="{w}" y="12" text-anchor="end" font-size="10" fill="var(--muted)">{hi:.0f}{unit}</text>'
              f'<text x="{w}" y="{h - 6}" text-anchor="end" font-size="10" fill="var(--muted)">{lo:.0f}{unit}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img">{grid}'
            f'<polygon points="{first_x:.1f},{base_y} {coords} {last_x:.1f},{base_y}" fill="{color}" opacity=".12"/>'
            f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linejoin="round"/>{labels}</svg>')


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
    offenders = Counter(p["name"] for r in rows if r.get("snapshot")
                        for p in r["snapshot"].get("top_cpu", [])[:3] if p["cpu_percent"] >= 10)
    mem_hogs = Counter(p["name"] for r in rows if r.get("snapshot")
                       for p in r["snapshot"].get("top_memory", [])[:3])

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
        _card("Memory used", f"now {last.get('mem_percent', 0):.0f}%, pressure {last.get('pressure')}",
              _chart(_series(rows, "mem_percent"), 0, 100, "%", "var(--info)")),
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
        body = "".join(f"<tr><td>{html.escape(n)}</td><td>{c}</td></tr>" for n, c in counter.most_common(8))
        return f"<table><tr><th>Process</th><th>{label}</th></tr>{body}</table>"

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
<div class="card"><h3>Most often hogging CPU</h3>{table(offenders, "Samples in top 3")}</div>
<div class="card"><h3>Most often hogging memory</h3>{table(mem_hogs, "Samples in top 3")}</div></div>
<footer>Generated {generated} by macpulse.</footer>
</main></body></html>"""
