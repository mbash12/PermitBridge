#!/usr/bin/env python3
"""Download attendance logs from the past N days (default 14) in two forms:
  1. Raw punches  - every clock event from the device
  2. Grouped clocks - one clock_in/clock_out per employee per day (matches main.py logic)

Outputs CSV files and prints a summary to the console.
"""

import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
from zk import ZK
from rich.console import Console
from rich.table import Table

console = Console()

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "device_ip": "10.10.3.226",
    "device_port": 4370,
}

DAYS_BACK = 14
RAW_OUTPUT = "attendance_raw.csv"
GROUPED_OUTPUT = "attendance_grouped.csv"


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_CONFIG.copy()


def fetch_raw_punches(ip, port, days=DAYS_BACK):
    zk = ZK(ip, port=port, timeout=20, ommit_ping=False)
    conn = zk.connect()
    try:
        all_logs = conn.get_attendance()
    finally:
        conn.disconnect()

    cutoff = datetime.now() - timedelta(days=days)
    rows = []
    for a in all_logs:
        if a.timestamp >= cutoff:
            rows.append({
                "employee_id": int(a.user_id),
                "timestamp": a.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "status": int(a.status) if a.status is not None else None,
                "punch": int(a.punch) if a.punch is not None else None,
            })
    rows.sort(key=lambda r: (r["employee_id"], r["timestamp"]))
    return rows


def group_to_clocks(raw_rows):
    if not raw_rows:
        return []
    df = pd.DataFrame(raw_rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    grouped = df.groupby(["employee_id", df["timestamp"].dt.date]).agg(
        punch_count=("timestamp", "count"),
        clock_in=("timestamp", "min"),
        clock_out=("timestamp", "max"),
    ).reset_index()
    clocks = []
    for _, r in grouped.iterrows():
        clocks.append({
            "employee_id": int(r["employee_id"]),
            "date": r["timestamp"].strftime("%Y-%m-%d"),
            "punch_count": int(r["punch_count"]),
            "clock_in": r["clock_in"].strftime("%Y-%m-%d %H:%M:%S"),
            "clock_out": r["clock_out"].strftime("%Y-%m-%d %H:%M:%S"),
        })
    clocks.sort(key=lambda c: (c["employee_id"], c["date"]))
    return clocks


def main():
    cfg = load_config()
    days = DAYS_BACK
    raw_path = os.path.abspath(RAW_OUTPUT)
    grouped_path = os.path.abspath(GROUPED_OUTPUT)

    console.print(f"[cyan]Device:[/cyan] {cfg['device_ip']}:{cfg.get('device_port', 4370)}")
    console.print(f"[cyan]Range:[/cyan]  last {days} days (since "
                  f"{(datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')})")

    console.print("\n[cyan]Fetching attendance from device...")
    try:
        raw = fetch_raw_punches(cfg["device_ip"], cfg.get("device_port", 4370), days=days)
    except Exception as e:
        console.print(f"[red]FAILED to read device: {e}")
        sys.exit(1)

    console.print(f"[green]Raw punches fetched:[/green] {len(raw)}")

    if raw:
        pd.DataFrame(raw).to_csv(raw_path, index=False)
        console.print(f"  saved -> {raw_path}")
    else:
        console.print("  [yellow](no punches in range - skipping raw CSV)[/yellow]")

    grouped = group_to_clocks(raw)
    console.print(f"\n[green]Grouped clock_in/clock_out records:[/green] {len(grouped)}")
    if grouped:
        pd.DataFrame(grouped).to_csv(grouped_path, index=False)
        console.print(f"  saved -> {grouped_path}")

    console.print("\n[bold cyan]Per-day breakdown[/bold cyan]")
    if grouped:
        table = Table(show_header=True, header_style="bold")
        for col in ("employee_id", "date", "punch_count", "clock_in", "clock_out"):
            table.add_column(col)
        for r in grouped:
            table.add_row(
                str(r["employee_id"]),
                r["date"],
                str(r["punch_count"]),
                r["clock_in"],
                r["clock_out"],
            )
        console.print(table)
    else:
        console.print("  [yellow](no records)[/yellow]")

    multi_punch_days = [r for r in grouped if r["punch_count"] > 2] if grouped else []
    if multi_punch_days:
        console.print(
            f"\n[yellow]Note:[/yellow] {len(multi_punch_days)} employee-day(s) had more "
            f"than 2 punches (break-outs collapsed in grouped view)."
        )

    console.print("\n[bold]Summary[/bold]")
    console.print(f"  raw punches:     {len(raw)}")
    console.print(f"  grouped records: {len(grouped)}")
    console.print(f"  unique employees: {len({r['employee_id'] for r in grouped})}")
    console.print(f"  date range: "
                  f"{grouped[0]['date']} -> {grouped[-1]['date']}" if grouped else "  (no data)")


if __name__ == "__main__":
    main()
