# macverdict

**Stop watching graphs. macverdict tells you why your Mac was slow this week, and what to do about it.**

*Formerly called macpulse. If you installed it under that name, run `macverdict setup` once: it brings your history across and removes the old parts.*

Activity Monitor, Stats and iStat Menus show you numbers *right now*, and leave the diagnosis to you.
macverdict quietly samples your Mac in the background (about one second every 15 minutes) and turns
weeks of history into four plain answers:

1. **What's slowing me down?** It blames the app that was actually the biggest memory or CPU user
   each time your Mac struggled, grouping helper processes so Chrome's 60 processes count as Chrome.
2. **Do I need more RAM?** A verdict from weeks of memory pressure and swap, not the misleading
   "memory used" figure, including what to buy next time.
3. **How long will my battery last?** Your real wear rate vs. Apple's rating, and roughly when you'll
   hit 80% capacity.
4. **Is my Air overheating?** How often the fanless MacBook Air slowed itself down to cool off, and
   which apps were running when it did.

```
$ macverdict insights          # illustration: three weeks on an 8 GB MacBook Air
macverdict insights · MacBook Air · 8.0 GB RAM · last 30 days

● What's slowing you down
  Google Chrome was the biggest memory user in 85% of your 794 slowdown moments.
    · Google Chrome: the biggest memory user in 85% of slowdowns, typically 5.0 GB.
    · Final Cut Pro: the biggest CPU user in 15% of slowdowns.
    · Biggest win: quit or trim Google Chrome when your Mac feels slow.

● Do you need more RAM?
  8 GB isn't enough for how you work. Your next Mac should have 16 GB.
    · Memory pressure was elevated in 33% of samples.
    · Swap: typically under 9.0 GB, peak 13.5 GB.

● How long will your battery last?
  At your pace, it reaches 80% capacity around May 2027.
    · It's wearing faster than Apple's rating: 3.8% of capacity per 100 cycles, vs 2% expected.

● Is your Air overheating?
  Your Mac slowed itself down to cool off in 6% of samples.
    · Busiest apps when it ran hot: Final Cut Pro.
```

## See it on a real Mac

A real week on a MacBook Air (Apple M5, 16 GB). `macverdict report` turns the history into a page
like this. **[Open the full sample report](https://anirudhjayaraman.github.io/macverdict/sample-report.html)**.

![macverdict insights: Chrome behind every slowdown, 16 GB is enough, battery healthy, no throttling](https://raw.githubusercontent.com/anirudhjayaraman/macverdict/main/docs/insights.png)

The trends make the story visible: memory pressure (shaded) on Mon 19 and Tue 20, matching the
swap spike, then a quiet rest of the week.

![macverdict trends: a week of health score, CPU, memory with pressure shaded, swap, disk, battery](https://raw.githubusercontent.com/anirudhjayaraman/macverdict/main/docs/trends.png)

Free and open source, one dependency (`psutil`), no sudo, no cloud: nothing leaves your Mac.
Built for Apple-silicon MacBooks, works on any Mac running macOS 12+.

## How it compares

| | Live stats | Keeps history | Tells you *why* & what to do | Price |
|---|---|---|---|---|
| Activity Monitor (built in) | ✅ | ❌ | ❌ | Free |
| [Stats](https://github.com/exelban/stats) | ✅ menu bar | ❌ | ❌ | Free |
| [iStat Menus](https://bjango.com/mac/istatmenus/) | ✅ very detailed | Some | Rule-based alerts | Paid |
| [macmon](https://github.com/vladkens/macmon) / [mactop](https://github.com/anemll/mactop) | ✅ power & GPU | ❌ | ❌ | Free |
| [Glances](https://github.com/nicolargo/glances) | ✅ any OS | Short buffer / export | ❌ | Free |
| [Mole](https://github.com/tw93/mole) `mo status` | ✅ | ❌ | Live health score | Free |
| [coconutBattery](https://www.coconut-flavour.com/coconutbattery/) | Battery only | ✅ battery | ❌ | Free / paid |
| **macverdict** | Snapshot (`macverdict`) | ✅ everything | ✅ **blame, RAM verdict, battery forecast, throttling** | **Free** |

**Use macverdict alongside a live monitor, not instead of one.** If you want to watch GPU power or fan
speed in real time, Stats and macmon are excellent. macverdict answers the questions those tools can't:
what happened over the last few weeks, and what you should change.

## Everyday health check

```
$ macverdict
macverdict · Apple M5 · macOS 26.5.2 · 16.0 GB RAM · up 1d 17h

Health score: 100 / 100

  OK       cpu      CPU 6%, load 1.9 on 10 cores.
  OK       memory   Memory pressure normal, 66% used, swap 0 B.
  OK       disk     698.7 GB free (75%).
  OK       battery  52% charged, capacity 100%, 71 cycles, 30.6°C.
  OK       thermal  No thermal throttling.
```

## What it checks

| Area | Signals | Why it matters |
|---|---|---|
| CPU | usage, 5-min load vs. core count, runaway processes | sustained load means work is queuing |
| Memory | **macOS memory pressure** (the real signal, not "% used"), swap, biggest apps | the #1 cause of slowdowns on 8–16 GB Macs |
| Disk | free space on the Data volume | macOS needs room for swap and updates |
| Battery | maximum capacity, cycle count, temperature | <80% capacity = service territory; heat = throttling |
| Thermal | macOS's own thermal state, plus CPU speed limit | fanless Airs slow themselves down when hot |
| Uptime | days since restart | long uptimes accumulate swap and leaks |

Each finding comes with plain-English advice, and everything rolls up into a 0–100 health score.

## Install

```bash
brew install anirudhjayaraman/tap/macverdict
macverdict setup
```

That's it. `macverdict setup` starts background recording, builds the small alert app and sends a
test alert (click **Allow** if macOS asks). Then run `macverdict` any time.

To remove it: `macverdict uninstall`, then `brew uninstall macverdict`.

Homebrew also installs zsh tab completion: type `macverdict ` and press Tab to see every command.
If nothing appears, turn on zsh completion by adding `autoload -Uz compinit && compinit` to `~/.zshrc`.

<details>
<summary>Not using Homebrew?</summary>

Install with [uv](https://docs.astral.sh/uv/) or pipx instead, then run `macverdict setup`:

```bash
uv tool install git+https://github.com/anirudhjayaraman/macverdict
```

</details>

Needs macOS 12 or later. Clickable alerts need Apple's Command Line Tools (`xcode-select --install`);
without them, everything else still works.

## Use

```bash
macverdict                      # diagnose right now
macverdict insights             # blame, RAM verdict, battery forecast, throttling
macverdict report               # HTML report with insights and trends, opens in your browser
macverdict history --days 30    # min/avg/max summary in the terminal
macverdict export history.csv   # take your data to a spreadsheet or notebook
macverdict check --json         # machine-readable snapshot
macverdict setup --every 5      # record every 5 minutes instead of 15
macverdict notify-test          # check that alerts reach you
macverdict uninstall            # stop background recording and remove the alert app
```

The background agent shows up as **macverdict** in System Settings → General → Login Items &
Extensions (or as `python3` if the alert app couldn't be built).
macOS labels it "unidentified developer" because it's signed on your own Mac rather than with a
paid Apple Developer ID, which is normal for open-source command-line tools. Switching it off
there stops recording.

The background agent runs at low priority, survives reboots, and shows a macOS notification only
when something is critical (e.g. memory pressure critical, battery overheating, disk nearly full).

## Notifications

The background agent only alerts you when something is critical, and clicking an alert opens your
macverdict report. `macverdict setup` builds the small alert app that makes this work.

It's built from source on your machine (`src/macverdict/notifier/MacverdictNotifier.swift`, about
100 lines) and signed locally, so there's no downloaded binary and no Apple account involved.
It has no Dock icon and only runs for a few seconds when posting an alert or handling a click.

Without it, macverdict falls back to macOS's built-in `osascript`. Those alerts show as coming
from **Script Editor**, and clicking one opens Script Editor. If no banner appears at all, check
System Settings → Notifications (macverdict or Script Editor) and that no Focus mode is on.

## Use it from Python

```python
from macverdict import collect, evaluate, health_score, insights, storage

snap = collect()
findings = evaluate(snap)
print(health_score(findings), [f.message for f in findings if f.level != "ok"])

rows = storage.load(storage.connect(), days=30, full=True)
for i in insights.build(rows, snap["system"]["memory_total"], insights.hardware_model()):
    print(i.title, "→", i.headline)
```

## Where data lives

`~/Library/Application Support/macverdict/` holds `history.db` (SQLite), `report.html` and the agent
logs. Samples older than 90 days are pruned automatically. Set `MACVERDICT_HOME` to use another folder.

## How it works

- `psutil` for CPU, memory, swap, disk, network and processes
- `sysctl kern.memorystatus_vm_pressure_level` for macOS memory pressure
- `ioreg -rn AppleSmartBattery` for battery capacity, cycles and temperature
- `NSProcessInfo.thermalState` (read via ctypes, no PyObjC) and `pmset -g therm` for throttling
- a per-user `launchd` agent for scheduling
- a tiny Swift app (built locally) for clickable notifications, with `osascript` as a fallback

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest
```

## License

MIT
