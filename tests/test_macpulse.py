import copy
import sys
import time

import pytest

from macpulse import report, scheduler, storage
from macpulse.checks import evaluate, health_score, worst_level

GB = 1024 ** 3


def make_snapshot(**over):
    snap = {
        "timestamp": time.time(),
        "system": {"chip": "Apple M5", "model": "Mac17,1", "macos": "26.5", "cpu_count": 10,
                   "memory_total": 16 * GB, "boot_time": time.time() - 3600},
        "cpu": {"percent": 12.0, "per_core": [12.0] * 10, "load_avg": [1.0, 1.0, 1.0]},
        "memory": {"total": 16 * GB, "used": 8 * GB, "available": 8 * GB, "percent": 50.0,
                   "swap_total": 0, "swap_used": 0, "pressure": {"level": "normal", "free_percent": 70}},
        "disk": {"path": "/", "total": 1000 * GB, "used": 300 * GB, "free": 700 * GB, "free_percent": 70.0},
        "io": {},
        "battery": {"percent": 80.0, "plugged_in": False, "charging": False, "cycle_count": 70,
                    "max_capacity_percent": 100, "health_percent": 99.0, "temperature_c": 30.0},
        "thermal": {"cpu_speed_limit": 100, "warning_level": 0, "throttled": False},
        "top_cpu": [{"pid": 1, "name": "Safari", "cpu_percent": 5.0, "rss": GB}],
        "top_memory": [{"pid": 1, "name": "Safari", "cpu_percent": 5.0, "rss": GB}],
        "process_count": 400,
    }
    for path, value in over.items():
        node = snap
        *parents, leaf = path.split("__")
        for k in parents:
            node = node[k]
        node[leaf] = value
    return snap


def test_healthy_mac_scores_100():
    findings = evaluate(make_snapshot())
    assert worst_level(findings) == "ok"
    assert health_score(findings) == 100


@pytest.mark.parametrize("path,value,level,area", [
    ("memory__pressure", {"level": "critical"}, "critical", "memory"),
    ("memory__swap_used", 3 * GB, "warn", "memory"),
    ("memory__swap_used", 8 * GB, "critical", "memory"),
    ("disk__free_percent", 10.0, "warn", "disk"),
    ("battery__temperature_c", 41.0, "warn", "battery"),
    ("battery__max_capacity_percent", 75, "warn", "battery"),
    ("thermal__throttled", True, "warn", "thermal"),
    ("cpu__percent", 97.0, "warn", "cpu"),
])
def test_problems_are_flagged(path, value, level, area):
    findings = evaluate(make_snapshot(**{path: value}))
    assert any(f.level == level and f.area == area for f in findings)
    assert health_score(findings) < 100


def test_desktop_without_battery():
    findings = evaluate(make_snapshot(battery=None))
    assert not any(f.area == "battery" for f in findings)


def test_storage_roundtrip_and_report(tmp_path, monkeypatch):
    monkeypatch.setenv("MACPULSE_HOME", str(tmp_path))
    conn = storage.connect()
    base = make_snapshot()
    for i in range(5):
        s = copy.deepcopy(base)
        s["timestamp"] = base["timestamp"] - (5 - i) * 900
        s["cpu"]["percent"] = 10.0 * i
        f = evaluate(s)
        storage.save(conn, s, f, health_score(f))
    rows = storage.load(conn, days=1, full=True)
    assert [r["cpu_percent"] for r in rows] == [0, 10, 20, 30, 40]
    page = report.build(rows, base, evaluate(base), 1)
    assert "<svg" in page and "Safari" in page
    storage.export_csv(rows, tmp_path / "h.csv")
    csv_text = (tmp_path / "h.csv").read_text()
    assert csv_text.count("\n") == 6 and csv_text.startswith("time,ts,")
    old = copy.deepcopy(base)
    old["timestamp"] -= 200 * 86400
    storage.save(conn, old, [], 100)
    assert storage.prune(conn, keep_days=90) == 1


def test_launchd_plist(tmp_path, monkeypatch):
    monkeypatch.setenv("MACPULSE_HOME", str(tmp_path))
    plist = scheduler.build_plist(30)
    assert plist["StartInterval"] == 1800
    assert plist["ProgramArguments"][1:4] == ["-m", "macpulse", "record"]
    assert "--notify" in plist["ProgramArguments"]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_live_collect():
    from macpulse.collectors import collect
    snap = collect(interval=0.2)
    assert 0 <= snap["cpu"]["percent"] <= 100
    assert snap["disk"]["total"] > 0
    assert evaluate(snap)


@pytest.mark.parametrize("argv", [[], ["--json"], ["--interval", "0.1"]])
def test_bare_options_default_to_check(argv, monkeypatch):
    from macpulse import cli
    calls = []
    monkeypatch.setattr(cli, "cmd_check", lambda args: calls.append(args) or 0)
    assert cli.main(argv) == 0
    assert len(calls) == 1


def test_days_label():
    from macpulse.fmt import days_label
    assert days_label(1) == "1 day" and days_label(7) == "7 days" and days_label(0.5) == "0.5 days"


class _Done:
    returncode = 0


def test_notify_prefers_macpulse_notifier(monkeypatch):
    from macpulse import notifier
    sent = []
    monkeypatch.setattr(notifier, "is_installed", lambda: True)
    monkeypatch.setattr(notifier, "send", lambda t, m, on_click=None: sent.append(on_click) or "ok")
    assert scheduler.notify("t", "m") == "macpulse"
    assert sent[0][1:] == ["-m", "macpulse", "report"]


def test_notify_falls_back_to_osascript(monkeypatch):
    from macpulse import notifier
    calls = []
    monkeypatch.setattr(notifier, "is_installed", lambda: True)
    monkeypatch.setattr(notifier, "send", lambda *a, **k: "denied: notifications are turned off")
    monkeypatch.setattr(scheduler.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _Done())
    assert scheduler.notify("t", 'say "hi"') == "osascript"
    assert calls[0][0] == "osascript" and '\\"hi\\"' in calls[0][2]


def test_notifier_bundle(tmp_path, monkeypatch):
    from macpulse import notifier
    monkeypatch.setenv("MACPULSE_HOME", str(tmp_path))
    info = notifier.info_plist()
    assert info["CFBundleIdentifier"] == notifier.BUNDLE_ID and info["LSUIElement"] is True
    assert notifier.SOURCE.exists()
    assert not notifier.is_installed()
    assert notifier.send("t", "m") == "not installed"


@pytest.mark.skipif(sys.platform != "darwin" or not __import__("shutil").which("swiftc"),
                    reason="needs macOS with the Swift compiler")
def test_notifier_compiles(tmp_path, monkeypatch):
    from macpulse import notifier
    monkeypatch.setenv("MACPULSE_HOME", str(tmp_path))
    app = notifier.install()
    assert app.name == "macpulse.app"
    assert (app / "Contents" / "MacOS" / notifier.NAME).exists()
    import subprocess
    ran = subprocess.run([str(notifier.executable()), "--run", "/bin/sh", "-c", "echo hi; exit 3"],
                         capture_output=True, text=True)
    assert ran.stdout == "hi\n" and ran.returncode == 3
    assert notifier.uninstall() and not notifier.is_installed()


def test_agent_runs_through_app_when_installed(tmp_path, monkeypatch):
    from macpulse import notifier
    monkeypatch.setenv("MACPULSE_HOME", str(tmp_path))
    assert scheduler.build_plist(15)["ProgramArguments"][1:3] == ["-m", "macpulse"]
    monkeypatch.setattr(notifier, "is_installed", lambda: True)
    args = scheduler.build_plist(15)["ProgramArguments"]
    assert args[0].endswith("macpulse.app/Contents/MacOS/MacpulseNotifier")
    assert args[1] == "--run" and args[3:6] == ["-m", "macpulse", "record"]


def test_setup_works_without_command_line_tools(monkeypatch, capsys):
    from macpulse import cli, notifier
    installed = []
    def no_tools():
        raise RuntimeError("needs Apple's Command Line Tools.")
    monkeypatch.setattr(notifier, "install", no_tools)
    monkeypatch.setattr(notifier, "is_installed", lambda: False)
    monkeypatch.setattr(scheduler, "install", lambda every, notify: installed.append((every, notify)))
    monkeypatch.setattr(scheduler, "notify", lambda *a, **k: "osascript")
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert installed == [(15, True)]
    assert "Skipped clickable alerts" in out and '"python3"' in out
