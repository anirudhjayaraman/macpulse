"""Command-line interface: `macpulse check | record | report | history | schedule | export`."""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__, insights, notifier, report, scheduler, storage
from .checks import evaluate, health_score, worst_level
from .collectors import collect
from .fmt import color, days_label, human_bytes, human_duration


def _print_check(snap: dict, findings: list, score: int) -> None:
    s = snap["system"]
    tone = "ok" if score >= 85 else "warn" if score >= 60 else "critical"
    print(color(f"macpulse · {s.get('chip')} · macOS {s.get('macos')} · "
                f"{human_bytes(s['memory_total'])} RAM · up {human_duration(snap['timestamp'] - s['boot_time'])}", "dim"))
    print(f"\nHealth score: {color(str(score), tone)} / 100\n")
    for f in findings:
        print(f"  {color(f.level.upper().ljust(8), f.level)} {f.area.ljust(8)} {f.message}")
        if f.advice and f.level != "ok":
            print(color(f"  {'':8} {'':8} → {f.advice}", "dim"))
    def procs(a: dict) -> str:
        n = a.get("processes", 1)
        return color(f"  ({n} processes)", "dim") if n > 1 else ""

    print(color("\n  Top CPU", "bold"))
    for a in insights.apps_in(snap, "cpu_percent")[:3]:
        print(f"    {a['cpu_percent']:6.1f}%  {a['name']}{procs(a)}")
    print(color("  Top memory", "bold"))
    for a in insights.apps_in(snap, "rss")[:3]:
        print(f"    {human_bytes(a['rss']):>9}  {a['name']}{procs(a)}")
    print()


def cmd_setup(args) -> int:
    """Everything a new user needs, in one command."""
    print("Setting up macpulse on this Mac...\n")
    if args.no_notify:
        print(color("  -", "dim"), "Alerts turned off (--no-notify).")
    else:
        try:
            notifier.install()
            print(color("  ✓", "ok"), "Built the macpulse alert app (clicking an alert opens your report).")
        except RuntimeError as e:
            print(color("  -", "dim"), f"Skipped clickable alerts: {e}")
            print(color("    ", "dim"), "Alerts still work; they just open Script Editor when clicked.")
    scheduler.install(args.every, notify=not args.no_notify)
    shown_as = '"macpulse"' if notifier.is_installed() else '"python3"'
    print(color("  ✓", "ok"), f"Recording in the background every {args.every} minutes "
          f"(listed as {shown_as} in System Settings > General > Login Items).")
    if not args.no_notify:
        if notifier.is_installed():
            status = notifier.send("macpulse is set up", "You'll only hear from macpulse when something is "
                                   "critical. Click to see your report.", notifier.report_command(), timeout=60)
            if status == "ok":
                print(color("  ✓", "ok"), "Sent a test alert, and macOS is allowing alerts from macpulse.")
            else:
                print(color("  !", "warn"), f"The test alert didn't go through ({status}).")
                print("    Allow alerts in System Settings > Notifications > macpulse, then run `macpulse notify-test`.")
        else:
            scheduler.notify("macpulse is set up", "You'll only hear from macpulse when something is critical.",
                             open_report=False)
            print(color("  ✓", "ok"), "Sent a test alert.")
    print("\nDone. Try these:")
    print("  macpulse            check your Mac right now")
    print("  macpulse insights   why it was slow, RAM verdict, battery forecast (best after a few days)")
    print("  macpulse report     everything in your browser")
    print("  macpulse uninstall  stop and remove the background parts")
    return 0


def cmd_uninstall(args) -> int:
    agent = scheduler.uninstall()
    app = notifier.uninstall()
    print("Stopped background recording." if agent else "Background recording wasn't on.")
    if app:
        print("Removed the macpulse alert app.")
    print(f"Your history is kept in {storage.data_dir()}; delete that folder to remove it too.")
    print("To remove the command itself: brew uninstall macpulse, pipx uninstall macpulse, "
          "or uv tool uninstall macpulse (whichever you used).")
    return 0


def cmd_check(args) -> int:
    snap = collect(args.interval)
    findings = evaluate(snap)
    score = health_score(findings)
    if args.json:
        print(json.dumps({"score": score, "findings": [f.to_dict() for f in findings], "snapshot": snap}, indent=2))
    else:
        _print_check(snap, findings, score)
        if not scheduler.status()["installed"]:
            print(color("Tip: run `macpulse setup` to record in the background and unlock `macpulse insights`.\n",
                        "dim"))
    if args.save:
        with storage.connect() as conn:
            storage.save(conn, snap, findings, score)
    return 2 if worst_level(findings) == "critical" else 0


def cmd_record(args) -> int:
    snap = collect(args.interval)
    findings = evaluate(snap)
    score = health_score(findings)
    with storage.connect() as conn:
        storage.save(conn, snap, findings, score)
        storage.prune(conn, args.keep_days)
    if args.notify:
        bad = [f for f in findings if f.level in ("warn", "critical")]
        if any(f.level == "critical" for f in bad):
            scheduler.notify(f"macpulse: health {score}/100", "; ".join(f.message for f in bad[:2]))
    if not args.quiet:
        print(f"recorded: score {score}, cpu {snap['cpu']['percent']:.0f}%, "
              f"pressure {snap['memory']['pressure']['level']}, swap {human_bytes(snap['memory']['swap_used'])}")
    return 0


INSIGHT_STYLE = {"good": "ok", "ok": "info", "attention": "warn"}


def cmd_insights(args) -> int:
    with storage.connect() as conn:
        rows = storage.load(conn, args.days, full=True)
    if not rows:
        print("No history yet. Run `macpulse schedule install` and check back in a day or two.")
        return 0
    memory_total = rows[-1]["snapshot"]["system"]["memory_total"]
    model = insights.hardware_model()
    found = insights.build(rows, memory_total, model)
    if args.json:
        print(json.dumps([i.to_dict() for i in found], indent=2))
        return 0
    print(color(f"macpulse insights · {model or 'Mac'} · {human_bytes(memory_total)} RAM · "
                f"last {days_label(args.days)}", "dim"))
    for i in found:
        style = INSIGHT_STYLE[i.level]
        print(f"\n{color('●', style)} {color(i.title, 'bold')}")
        print(f"  {color(i.headline, style)}")
        for d in i.details:
            print(f"    · {d}")
        if i.confidence:
            print(color(f"    {i.confidence}", "dim"))
    print()
    return 0


def cmd_report(args) -> int:
    snap = collect(args.interval)
    findings = evaluate(snap)
    with storage.connect() as conn:
        storage.save(conn, snap, findings, health_score(findings))
        rows = storage.load(conn, args.days, full=True)
    found = insights.build(rows, snap["system"]["memory_total"], insights.hardware_model())
    out = Path(args.out) if args.out else storage.data_dir() / "report.html"
    out.write_text(report.build(rows, snap, findings, args.days, found))
    print(f"Report written to {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_history(args) -> int:
    with storage.connect() as conn:
        rows = storage.load(conn, args.days)
    if not rows:
        print("No history yet. Run `macpulse record`, or `macpulse schedule install` to record automatically.")
        return 0
    metrics = [("score", "Health score", ""), ("cpu_percent", "CPU %", ""), ("mem_percent", "Memory %", ""),
               ("swap_used", "Swap", "bytes"), ("disk_free_percent", "Disk free %", ""),
               ("battery_temp_c", "Battery °C", "")]
    print(f"{len(rows)} samples over the last {days_label(args.days)}\n")
    print(f"  {'metric':<14}{'min':>10}{'avg':>10}{'max':>10}")
    for key, label, kind in metrics:
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            continue
        lo, avg, hi = min(vals), sum(vals) / len(vals), max(vals)
        f = human_bytes if kind == "bytes" else (lambda v: f"{v:.1f}")
        print(f"  {label:<14}{f(lo):>10}{f(avg):>10}{f(hi):>10}")
    pressures = [r["pressure"] for r in rows]
    elevated = sum(p in ("warn", "critical") for p in pressures)
    print(f"\n  Memory pressure elevated in {elevated}/{len(rows)} samples; "
          f"throttled in {sum(r['throttled'] or 0 for r in rows)}/{len(rows)}.")
    return 0


def cmd_schedule(args) -> int:
    if args.action == "install":
        path = scheduler.install(args.every, notify=not args.no_notify)
        print(f"Installed launchd agent ({path}); recording every {args.every} min.")
        print(f"Data: {storage.data_dir()}")
        if not args.no_notify and not notifier.is_installed():
            print("Tip: run `macpulse notifier install` so alerts open your report when clicked.")
    elif args.action == "remove":
        print("Removed launchd agent." if scheduler.uninstall() else "No agent was installed.")
    else:
        st = scheduler.status()
        if not st["installed"]:
            print("Not scheduled. Run `macpulse schedule install`.")
        else:
            print(f"Scheduled every {st['every_minutes']} min · loaded: {st['loaded']} · {st['plist']}")
    return 0


def cmd_notify_test(args) -> int:
    if not notifier.is_installed():
        scheduler.notify("macpulse test", "Notifications work.", open_report=False)
        print("Sent with osascript (shown as Script Editor; clicking opens Script Editor).\n"
              "For banners that open your report when clicked: macpulse notifier install\n"
              "No banner? Allow notifications for Script Editor in System Settings > Notifications.")
        return 0
    status = notifier.send("macpulse test", "Notifications work. Click to open your report.",
                           notifier.report_command(), timeout=60)
    if status == "ok":
        print("Sent. Click the banner to open your macpulse report.")
        return 0
    print(f"The macpulse notifier couldn't post ({status}).\n"
          "Allow it in System Settings > Notifications > macpulse, then run this again.")
    return 1


def cmd_notifier(args) -> int:
    if args.action == "install":
        try:
            path = notifier.install()
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Built {path}.")
        if scheduler.refresh():
            print("Background agent now runs as \"macpulse\" in System Settings > General > Login Items.")
        print("Run `macpulse notify-test` and click Allow if macOS asks.")
    elif args.action == "remove":
        print("Removed the notifier." if notifier.uninstall() else "The notifier wasn't installed.")
        scheduler.refresh()
    else:
        print(f"Installed at {notifier.app_path()}" if notifier.is_installed()
              else "Not installed. Run `macpulse notifier install` (needs Xcode Command Line Tools).")
    return 0


def cmd_export(args) -> int:
    with storage.connect() as conn:
        rows = storage.load(conn, args.days)
    storage.export_csv(rows, Path(args.out))
    print(f"Exported {len(rows)} rows to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="macpulse", description="Performance diagnostics and history for your Mac.")
    p.add_argument("--version", action="version", version=f"macpulse {__version__}")
    sub = p.add_subparsers(dest="command")

    def add(name, func, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(func=func)
        sp.add_argument("--interval", type=float, default=1.0, help="seconds to sample CPU/IO rates (default 1)")
        return sp

    st = sub.add_parser("setup", help="one-step setup: background recording and alerts")
    st.add_argument("--every", type=int, default=15, help="minutes between samples (default 15)")
    st.add_argument("--no-notify", action="store_true", help="never show alerts")
    st.set_defaults(func=cmd_setup)

    un = sub.add_parser("uninstall", help="stop background recording and remove the alert app")
    un.set_defaults(func=cmd_uninstall)

    c = add("check", cmd_check, "diagnose this Mac right now (default)")
    c.add_argument("--json", action="store_true", help="print machine-readable JSON")
    c.add_argument("--save", action="store_true", help="also store this sample in history")

    r = add("record", cmd_record, "take a sample and store it (used by the scheduler)")
    r.add_argument("--notify", action="store_true", help="show a macOS notification on critical findings")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--keep-days", type=float, default=90, help="delete samples older than this (default 90)")

    rp = add("report", cmd_report, "build an HTML report with trends and open it")
    rp.add_argument("--days", type=float, default=7)
    rp.add_argument("--out", help="output path (default: data folder/report.html)")
    rp.add_argument("--no-open", action="store_true")

    ins = sub.add_parser("insights", help="why your Mac was slow, RAM verdict, battery forecast, throttling")
    ins.add_argument("--days", type=float, default=30)
    ins.add_argument("--json", action="store_true")
    ins.set_defaults(func=cmd_insights)

    h = sub.add_parser("history", help="summarise stored samples in the terminal")
    h.add_argument("--days", type=float, default=7)
    h.set_defaults(func=cmd_history)

    s = sub.add_parser("schedule", help="record automatically with launchd")
    s.add_argument("action", choices=["install", "remove", "status"])
    s.add_argument("--every", type=int, default=15, help="minutes between samples (default 15)")
    s.add_argument("--no-notify", action="store_true", help="never show notifications")
    s.set_defaults(func=cmd_schedule)

    n = sub.add_parser("notify-test", help="send a test notification")
    n.set_defaults(func=cmd_notify_test)

    nt = sub.add_parser("notifier", help="build macpulse's own notification app (clickable alerts)")
    nt.add_argument("action", choices=["install", "remove", "status"])
    nt.set_defaults(func=cmd_notifier)

    e = sub.add_parser("export", help="export history to CSV")
    e.add_argument("out")
    e.add_argument("--days", type=float, default=90)
    e.set_defaults(func=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print("macpulse only supports macOS.", file=sys.stderr)
        return 1
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"check", "record", "report", "history", "schedule", "export", "notify-test", "notifier", "insights", "setup", "uninstall"}
    # `macpulse` and `macpulse --json` mean `macpulse check ...`.
    if not argv or (argv[0] not in commands and argv[0] not in ("-h", "--help", "--version")
                    and argv[0].startswith("-")):
        argv.insert(0, "check")
    args = parser.parse_args(argv)
    return args.func(args)
