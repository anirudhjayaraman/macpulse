"""Insights on simulated history: each scenario should produce the verdict a person would expect."""

from datetime import datetime

from macverdict import insights, report
from macverdict.checks import evaluate
from macverdict.collectors import app_name

GB = 1024 ** 3
DAY = 86400


def make_rows(days=14, per_day=96, total_gb=16, stress=lambda i: {}, battery=lambda i, frac: {}):
    """`per_day` samples a day (96 = every 15 minutes); `stress(i)` overrides fields of sample i."""
    n = int(days * per_day)
    # Start at local midnight so sample i always falls at the same time of day, whatever the clock says.
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() - days * DAY
    rows = []
    for i in range(n):
        frac = i / max(1, n - 1)
        over = stress(i)
        apps = over.get("apps") or [
            {"name": "Google Chrome", "rss": 3 * GB, "cpu_percent": 8.0, "processes": 25},
            {"name": "Slack", "rss": 1 * GB, "cpu_percent": 2.0, "processes": 6},
            {"name": "Finder", "rss": GB // 4, "cpu_percent": 0.5, "processes": 1},
        ]
        bat = {"percent": 70.0, "cycle_count": 100, "health_percent": 96.0, "max_capacity_percent": 96,
               "temperature_c": 30.0, **battery(i, frac)}
        state = over.get("state", "nominal")
        snap = {
            "system": {"memory_total": total_gb * GB},
            "apps": apps,
            "battery": bat,
            "thermal": {"state": state, "cpu_speed_limit": 100,
                        "throttled": state in ("serious", "critical")},
        }
        rows.append({
            "ts": start + i * DAY / per_day,
            "cpu_percent": over.get("cpu", 10.0),
            "pressure": over.get("pressure", "normal"),
            "swap_used": over.get("swap", 0),
            "throttled": int(state in ("serious", "critical")),
            "battery_temp_c": bat["temperature_c"],
            "snapshot": snap,
        })
    return rows


def by_key(found):
    return {i.key: i for i in found}


def test_light_user_gets_all_clear():
    found = by_key(insights.build(make_rows(), 16 * GB, "MacBook Air"))
    assert found["blame"].level == "good" and "No slowdowns" in found["blame"].headline
    assert found["ram"].level == "good" and "16 GB is plenty" in found["ram"].headline
    assert found["thermal"].level == "good" and "fanless Air" in found["thermal"].headline
    assert "Early read" not in found["ram"].confidence  # two weeks of data is enough


def test_heavy_chrome_user_on_8gb():
    heavy = [{"name": "Google Chrome", "rss": 5 * GB, "cpu_percent": 40.0, "processes": 60},
             {"name": "Slack", "rss": 1 * GB, "cpu_percent": 2.0, "processes": 6}]

    def stress(i):
        if i % 3 == 0:  # a third of the time: memory pressure with swap climbing
            return {"pressure": "warn", "swap": (i % 30) * GB // 2, "apps": heavy}
        return {}

    found = by_key(insights.build(make_rows(total_gb=8, stress=stress), 8 * GB))
    blame = found["blame"]
    assert blame.level == "attention"
    assert blame.headline.startswith("Google Chrome was the biggest memory user in 100%")
    assert any("Also contributing: Slack" in d for d in blame.details)
    assert any("quit or trim Google Chrome" in d for d in blame.details)
    ram = found["ram"]
    assert ram.level == "attention" and "next Mac should have 16 GB" in ram.headline


def test_occasional_pressure_is_just_enough():
    found = by_key(insights.build(make_rows(stress=lambda i: {"pressure": "warn"} if i % 25 == 0 else {}),
                                  16 * GB))
    assert found["ram"].level == "ok" and "just enough" in found["ram"].headline and "briefly" in found["ram"].headline
    assert "24 GB" in " ".join(found["ram"].details)


def test_battery_forecast_with_dated_eta():
    # 30 days, 100 -> 115 cycles (3.5 a week), 90% health: wearing faster than Apple's rating.
    rows = make_rows(days=30, per_day=24,
                     battery=lambda i, frac: {"cycle_count": 100 + round(15 * frac), "health_percent": 90.0,
                                              "max_capacity_percent": 90})
    b = insights.battery_forecast(rows)
    assert "reaches 80% capacity around" in b.headline
    assert any("3.5 cycles a week" in d for d in b.details)
    assert any("faster than Apple's rating" in d for d in b.details)


def test_battery_without_history_is_undated_and_consumed_battery_flagged():
    b = insights.battery_forecast(make_rows(days=1))
    assert b.headline.startswith("Healthy: 96% capacity") and "dated forecast" in b.details[-1]
    worn = insights.battery_forecast(make_rows(days=1, battery=lambda i, f: {"health_percent": 78.0,
                                                                             "max_capacity_percent": 78}))
    assert worn.level == "attention" and "consumed" in worn.headline


def test_air_throttling_names_the_busy_app():
    fcp = [{"name": "Final Cut Pro", "rss": 4 * GB, "cpu_percent": 380.0, "processes": 3}]
    rows = make_rows(stress=lambda i: {"state": "serious", "cpu": 95.0, "apps": fcp} if i % 5 == 0 else {})
    t = insights.throttling(rows, "MacBook Air")
    assert t.level == "attention" and t.title == "Is your Air overheating?"
    assert any("Final Cut Pro" in d for d in t.details)
    assert any("no fan" in d for d in t.details)
    assert insights.throttling(rows, "MacBook Pro").title == "Does your Mac overheat?"


def test_old_samples_without_app_totals_still_work():
    rows = make_rows(days=2, stress=lambda i: {"pressure": "warn"} if i % 2 else {})
    for r in rows:
        r["snapshot"].pop("apps")
        r["snapshot"]["top_memory"] = [{"pid": 7, "name": "Google Chrome Helper (Renderer)", "rss": 2 * GB,
                                        "cpu_percent": 1.0}]
        r["snapshot"]["top_cpu"] = []
    blame = insights.slowdown_blame(rows)
    assert blame.headline.startswith("Google Chrome was the biggest memory user")


def test_app_name_grouping():
    exe = "/Applications/Google Chrome.app/Contents/Frameworks/X.framework/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/x"
    assert app_name("Google Chrome Helper (Renderer)", exe) == "Google Chrome"
    assert app_name("Code Helper (Plugin)") == "Code"
    assert app_name("com.apple.WebKit.WebContent") == "Safari & web views"
    assert app_name("python3.12", "/usr/local/bin/python3.12") == "python3.12"


def test_report_shows_insights():
    from test_macverdict import make_snapshot
    rows = make_rows(days=2)
    for r in rows:
        r.update(score=100, mem_percent=50.0, disk_free_percent=50.0, battery_percent=70.0, battery_health=96)
    snap = make_snapshot()
    page = report.build(rows, snap, evaluate(snap), 2, insights.build(rows, 16 * GB, "MacBook Air"))
    assert "<h2>Insights</h2>" in page and "16 GB is plenty" in page


# --- v0.1.1 regressions, from a real week of data -----------------------------------------

def test_one_heavy_day_does_not_mean_buy_more_ram():
    # Like the real week: a day and a half of Chrome-driven pressure, then six quiet days.
    chrome = lambda gb: [{"name": "Google Chrome", "rss": gb * GB, "cpu_percent": 5.0, "processes": 24},
                         {"name": "Claude", "rss": 2 * GB, "cpu_percent": 3.0, "processes": 13}]

    def stress(i):
        if 30 <= i < 150 and i % 4:     # most of day 1 and part of day 2
            return {"pressure": "warn", "swap": 2 * GB, "apps": chrome(8)}
        return {"apps": chrome(3)}

    ram = insights.ram_verdict(make_rows(days=8, stress=stress), 16 * GB)
    assert ram.level == "ok" and "isn't enough" not in ram.headline
    assert "Google Chrome grows past about" in ram.headline   # 8 GB when it hurt vs 3 GB normally
    assert "ran high on 2 of" in ram.details[0] and "none in the" in ram.details[0]


def test_daily_pressure_most_of_the_time_still_means_more_ram():
    rows = make_rows(days=10, stress=lambda i: {"pressure": "warn"} if i % 4 == 0 else {})
    ram = insights.ram_verdict(rows, 16 * GB)
    assert ram.level == "attention" and "next Mac should have 24 GB" in ram.headline


def test_battery_noise_is_not_reported_as_wear():
    # Capacity readings bounce between 98.2% and 100.1%, as real ones do.
    noisy = [98.2, 99.4, 100.1, 98.9, 99.7]
    rows = make_rows(days=7, per_day=24, battery=lambda i, f: {
        "cycle_count": 74, "max_capacity_percent": 100, "health_percent": noisy[i % len(noisy)]})
    b = insights.battery_forecast(rows)
    assert b.headline == "Healthy: 100% capacity, with too little wear to measure yet."
    assert any("drifts by about" in d for d in b.details)
    assert not any("more cycles" in d for d in [b.headline, *b.details])


def test_repeat_offenders_never_exceed_sample_count():
    from test_macverdict import make_snapshot
    rows = make_rows(days=1, per_day=48)
    for r in rows:   # three same-named Chrome helpers in every old-style sample
        r["snapshot"].pop("apps")
        r["snapshot"]["top_memory"] = [{"pid": p, "name": "Google Chrome Helper (Renderer)", "rss": GB,
                                        "cpu_percent": 1.0} for p in (1, 2, 3)]
        r["snapshot"]["top_cpu"] = []
        r.update(score=100, mem_percent=50.0, disk_free_percent=50.0, battery_percent=70.0, battery_health=96)
    snap = make_snapshot()
    page = report.build(rows, snap, evaluate(snap), 1)
    assert "<td>Google Chrome</td><td>48 (100%)</td>" in page


def test_charts_break_across_sleep_and_label_days():
    rows = make_rows(days=3, per_day=24)
    rows = [r for i, r in enumerate(rows) if not 20 <= i < 30]   # ~10 hours asleep
    pts = [(r["ts"], 50.0) for r in rows]
    svg = report._chart(pts)
    assert svg.count("<polyline") == 2
    assert len(report._ticks(pts[0][0], pts[-1][0])) >= 2
