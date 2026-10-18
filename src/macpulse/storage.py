"""SQLite history of snapshots, stored under ~/Library/Application Support/macpulse."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

COLUMNS = (
    "ts", "score", "cpu_percent", "load5", "mem_percent", "pressure", "swap_used",
    "disk_free_percent", "battery_percent", "battery_health", "battery_temp_c", "throttled",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL PRIMARY KEY,
    score INTEGER,
    cpu_percent REAL,
    load5 REAL,
    mem_percent REAL,
    pressure TEXT,
    swap_used INTEGER,
    disk_free_percent REAL,
    battery_percent REAL,
    battery_health REAL,
    battery_temp_c REAL,
    throttled INTEGER,
    snapshot TEXT,
    findings TEXT
);
"""


def data_dir() -> Path:
    override = os.environ.get("MACPULSE_HOME")
    path = Path(override) if override else Path.home() / "Library" / "Application Support" / "macpulse"
    path.mkdir(parents=True, exist_ok=True)
    return path


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or data_dir() / "history.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def flatten(snapshot: dict, score: int) -> dict[str, Any]:
    b = snapshot.get("battery") or {}
    m = snapshot["memory"]
    return {
        "ts": snapshot["timestamp"],
        "score": score,
        "cpu_percent": snapshot["cpu"]["percent"],
        "load5": snapshot["cpu"]["load_avg"][1],
        "mem_percent": m["percent"],
        "pressure": (m.get("pressure") or {}).get("level"),
        "swap_used": m["swap_used"],
        "disk_free_percent": snapshot["disk"]["free_percent"],
        "battery_percent": b.get("percent"),
        "battery_health": b.get("max_capacity_percent") or b.get("health_percent"),
        "battery_temp_c": b.get("temperature_c"),
        "throttled": int(snapshot["thermal"]["throttled"]),
    }


def save(conn: sqlite3.Connection, snapshot: dict, findings: list, score: int) -> None:
    row = flatten(snapshot, score)
    row["snapshot"] = json.dumps(snapshot)
    row["findings"] = json.dumps([f.to_dict() for f in findings])
    cols = ", ".join(row)
    conn.execute(f"INSERT OR REPLACE INTO samples ({cols}) VALUES ({', '.join('?' * len(row))})",
                 tuple(row.values()))
    conn.commit()


def load(conn: sqlite3.Connection, days: float = 7, full: bool = False) -> list[dict[str, Any]]:
    since = time.time() - days * 86400
    cols = "*" if full else ", ".join(COLUMNS)
    rows = conn.execute(f"SELECT {cols} FROM samples WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ("snapshot", "findings"):
            if key in d and d[key]:
                d[key] = json.loads(d[key])
        out.append(d)
    return out


def prune(conn: sqlite3.Connection, keep_days: float = 90) -> int:
    cur = conn.execute("DELETE FROM samples WHERE ts < ?", (time.time() - keep_days * 86400,))
    conn.commit()
    return cur.rowcount


def export_csv(rows: Iterable[dict], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=("time", *COLUMNS), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({"time": datetime.fromtimestamp(r["ts"]).isoformat(timespec="seconds"), **r})
