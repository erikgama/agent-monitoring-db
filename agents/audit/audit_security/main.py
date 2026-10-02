"""Read-only Audit Security collector and continuous report loop."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .artifacts import write_artifacts
from .collector import AUDIT_REPORT_DIR, QueryClient, collect_snapshot
from .mysql_cli import MysqlCli
from .renderer import render_html

INTERVAL_SECONDS = 15.0
OUTPUT_DIRECTORY = AUDIT_REPORT_DIR / "results"


def collect_once(
    client: QueryClient,
    *,
    output_directory: Path = OUTPUT_DIRECTORY,
    window_minutes: int = 1,
    max_events: int = 5000,
    max_rows: int = 500,
) -> dict[str, Any]:
    """Collect facts and publish one report without making alert decisions."""
    report = collect_snapshot(
        client,
        window_minutes=window_minutes,
        max_events=max_events,
        max_rows=max_rows,
    )
    write_artifacts(output_directory, report, render_html(report))
    return report


def run_collector(
    *,
    client: QueryClient,
    output_directory: Path = OUTPUT_DIRECTORY,
    interval_seconds: float = INTERVAL_SECONDS,
    window_minutes: int = 1,
    max_events: int = 5000,
    max_rows: int = 500,
    max_cycles: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    if interval_seconds <= 0:
        raise ValueError("collector_interval_must_be_positive")
    cycles = 0
    next_tick = monotonic_fn()
    while max_cycles is None or cycles < max_cycles:
        remaining = next_tick - monotonic_fn()
        if remaining > 0:
            sleep_fn(remaining)
        try:
            report = collect_once(
                client,
                output_directory=output_directory,
                window_minutes=window_minutes,
                max_events=max_events,
                max_rows=max_rows,
            )
            event = {
                "status": "collected",
                "audit_id": report["audit_id"],
                "collected_at": report["collected_at"],
                "collection_status": report["overall_status"],
                "output_directory": str(output_directory.resolve()),
            }
        except Exception as error:
            event = {
                "status": "collection_failed",
                "error_type": type(error).__name__,
                "mysql_errno": getattr(error, "errno", None),
            }
        if on_event is not None:
            on_event(event)
        cycles += 1
        next_tick += interval_seconds
        if next_tick < monotonic_fn():
            next_tick = monotonic_fn() + interval_seconds
    return cycles


def _print_event(event: dict[str, Any]) -> None:
    json.dump(event, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "monitor"):
        command = commands.add_parser(name)
        command.add_argument("--window-minutes", type=int, default=1)
        command.add_argument("--max-events", type=int, default=5000)
        command.add_argument("--max-rows", type=int, default=500)
        command.add_argument("--timeout-seconds", type=int, default=30)
        command.add_argument("--output-dir", type=Path, default=OUTPUT_DIRECTORY)
        if name == "monitor":
            command.add_argument(
                "--interval-seconds", type=float, default=INTERVAL_SECONDS
            )
            command.add_argument("--max-cycles", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        client = MysqlCli.from_environment(args.timeout_seconds)
        if args.command == "collect":
            report = collect_once(
                client,
                output_directory=args.output_dir,
                window_minutes=args.window_minutes,
                max_events=args.max_events,
                max_rows=args.max_rows,
            )
            _print_event(
                {
                    "status": "collected",
                    "audit_id": report["audit_id"],
                    "collected_at": report["collected_at"],
                    "collection_status": report["overall_status"],
                    "output_directory": str(args.output_dir.resolve()),
                }
            )
            return 0
        run_collector(
            client=client,
            output_directory=args.output_dir,
            interval_seconds=args.interval_seconds,
            window_minutes=args.window_minutes,
            max_events=args.max_events,
            max_rows=args.max_rows,
            max_cycles=args.max_cycles,
            on_event=_print_event,
        )
    except KeyboardInterrupt:
        _print_event({"status": "stopped"})
        return 0
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        _print_event(
            {
                "status": "collector_failed",
                "error_type": type(error).__name__,
                "mysql_errno": getattr(error, "errno", None),
            }
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
