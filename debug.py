#!/usr/bin/env python3

from datetime import datetime
import sys
import logging

from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler
from rich.panel import Panel
from rich import box

from zk import ZK

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True)]
)
logger = logging.getLogger("fingerprint-debug")

console = Console()


def get_settings():
    return {
        'IP_ADDRESS': '10.10.3.245',
        'PORT': 4370,
        'TIMEOUT': 20,
        'PASSWORD': 0,
    }


def test_connection(ip, port=4370, timeout=20, password=0):
    console.print(f"[cyan]Attempting to connect to [bold]{ip}:{port}[/bold]...")
    zk = ZK(ip, port=port, timeout=timeout, password=password, force_udp=False, ommit_ping=False)
    try:
        zk.connect()
        console.print("[green]✓ Connection successful![/green]")
        return zk
    except Exception as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        return None


def show_device_info(zk):
    console.print("\n[bold cyan]Device Information[/bold cyan]")

    rows = []
    for attr, label in [
        ('get_firmware', 'Firmware'),
        ('get_serialnumber', 'Serial Number'),
        ('get_device_name', 'Device Name'),
        ('get_user_count', 'User Count'),
        ('get_attendance_count', 'Attendance Count'),
    ]:
        method = getattr(zk, attr, None)
        if method:
            try:
                val = method()
                rows.append((label, str(val)))
            except Exception:
                rows.append((label, "[red]Error[/red]"))
        else:
            rows.append((label, "[dim]N/A[/dim]"))

    if rows:
        table = Table(box=box.ROUNDED)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        for label, val in rows:
            table.add_row(label, val)
        console.print(table)


def show_employees(zk):
    console.print("\n[bold cyan]Employee Data[/bold cyan]")
    try:
        users = zk.get_users()
        if not users:
            console.print("[yellow]No employees found on device.[/yellow]")
            return

        table = Table(box=box.ROUNDED)
        table.add_column("No", style="dim")
        table.add_column("User ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Card", style="yellow")
        table.add_column("Privilege", style="magenta")

        for i, user in enumerate(users, 1):
            table.add_row(
                str(i),
                str(user.user_id),
                user.name,
                str(user.card or '-'),
                str(user.privilege)
            )

        console.print(table)
        console.print(f"[dim]Total: {len(users)} employees[/dim]")
    except Exception as e:
        logger.error(f"Failed to retrieve employee data: {e}")


def show_attendance(zk, filter_today=False, filter_recent_days=None):
    console.print("\n[bold cyan]Attendance Logs[/bold cyan]")
    try:
        attendances = zk.get_attendance()
        if not attendances:
            console.print("[yellow]No attendance records found on device.[/yellow]")
            return

        now = datetime.now()
        filtered = []
        for att in attendances:
            ts = att.timestamp
            if filter_today and ts.date() != now.date():
                continue
            if filter_recent_days and (now - ts).days > filter_recent_days:
                continue
            filtered.append(att)

        if not filtered:
            console.print("[yellow]No matching attendance records.[/yellow]")
            return

        raw_table = Table(title=f"Raw Logs ({len(filtered)} records)", box=box.ROUNDED)
        raw_table.add_column("No", style="dim")
        raw_table.add_column("User ID", style="cyan")
        raw_table.add_column("Timestamp", style="green")
        raw_table.add_column("Status", style="yellow")

        for i, att in enumerate(filtered, 1):
            raw_table.add_row(
                str(i),
                str(att.user_id),
                att.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                str(att.status)
            )
        console.print(raw_table)

        grouped = {}
        for att in filtered:
            date_key = att.timestamp.strftime("%Y-%m-%d")
            emp_key = f"{att.user_id}"
            if emp_key not in grouped:
                grouped[emp_key] = {}
            if date_key not in grouped[emp_key]:
                grouped[emp_key][date_key] = []
            grouped[emp_key][date_key].append(att.timestamp)

        summary_table = Table(title="Daily Summary (Clock-in / Clock-out)", box=box.ROUNDED)
        summary_table.add_column("Employee ID", style="cyan")
        summary_table.add_column("Date", style="green")
        summary_table.add_column("Clock In", style="green")
        summary_table.add_column("Clock Out", style="red")

        for emp_id in sorted(grouped.keys()):
            for date_key in sorted(grouped[emp_id].keys()):
                timestamps = sorted(grouped[emp_id][date_key])
                clock_in = timestamps[0].strftime("%H:%M:%S")
                clock_out = timestamps[-1].strftime("%H:%M:%S") if len(timestamps) > 1 else "-"
                summary_table.add_row(emp_id, date_key, clock_in, clock_out)

        console.print(summary_table)

    except Exception as e:
        logger.error(f"Failed to retrieve attendance data: {e}")


def show_menu():
    console.print(Panel.fit(
        "[bold cyan]Fingerprint Device Debug Tool[/bold cyan]\n"
        "[dim]Inspect device data without uploading[/dim]",
        box=box.DOUBLE
    ))

    console.print("\n[bold]Options:[/bold]")
    console.print("  [cyan]1[/cyan]  Check Connection & Device Info")
    console.print("  [cyan]2[/cyan]  View All Employees")
    console.print("  [cyan]3[/cyan]  View All Attendance Logs")
    console.print("  [cyan]4[/cyan]  View Today's Attendance")
    console.print("  [cyan]5[/cyan]  View Last 7 Days Attendance")
    console.print("  [cyan]6[/cyan]  Run Full Diagnostics")
    console.print("  [cyan]0[/cyan]  Exit")

    while True:
        try:
            choice = input("\nEnter your choice (0-6): ")
            if choice in [str(i) for i in range(7)]:
                return choice
            console.print("[red]Invalid choice.[/red]")
        except (ValueError, KeyboardInterrupt):
            return '0'


def full_diagnostics(settings):
    console.print("\n[bold underline]Running Full Diagnostics[/bold underline]")
    zk = test_connection(
        settings['IP_ADDRESS'],
        settings['PORT'],
        settings['TIMEOUT'],
        settings['PASSWORD']
    )
    if not zk:
        return

    try:
        show_device_info(zk)
        show_employees(zk)
        show_attendance(zk, filter_recent_days=90)
    finally:
        zk.disconnect()
        console.print("[dim]Disconnected from device.[/dim]")


def main():
    settings = get_settings()

    while True:
        choice = show_menu()

        if choice == '0':
            console.print("[yellow]Exiting...[/yellow]")
            break

        zk = None
        if choice in ('1', '2', '3', '4', '5'):
            zk = test_connection(
                settings['IP_ADDRESS'],
                settings['PORT'],
                settings['TIMEOUT'],
                settings['PASSWORD']
            )
            if not zk:
                input("\nPress Enter to continue...")
                continue

        try:
            if choice == '1':
                show_device_info(zk)
            elif choice == '2':
                show_employees(zk)
            elif choice == '3':
                show_attendance(zk)
            elif choice == '4':
                show_attendance(zk, filter_today=True)
            elif choice == '5':
                show_attendance(zk, filter_recent_days=7)
            elif choice == '6':
                full_diagnostics(settings)
        finally:
            if zk:
                zk.disconnect()
                console.print("[dim]Disconnected from device.[/dim]")

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        sys.exit(1)
