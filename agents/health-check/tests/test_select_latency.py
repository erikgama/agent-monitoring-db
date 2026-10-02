from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from select_latency.collector import (  # noqa: E402
    INACTIVITY_RESET_SECONDS,
    INITIAL_WINDOW_SECONDS,
    REFRESH_INTERVAL_SECONDS,
    TARGET_DIGEST,
    WINDOW_SECONDS,
    collect_select_latency,
    inspect_percentile_capabilities,
    monitor_select_latency,
)

DESCRIPTION = [
    ("schema_name",),
    ("digest",),
    ("digest_text",),
    ("executions",),
    ("total_latency_picoseconds",),
    ("total_lock_time_picoseconds",),
    ("rows_examined",),
    ("rows_sent",),
    ("tmp_tables",),
    ("tmp_disk_tables",),
    ("sort_rows",),
    ("no_index_used",),
    ("no_good_index_used",),
    ("errors",),
    ("warnings",),
    ("first_seen",),
    ("last_seen",),
]

HISTOGRAM_DESCRIPTION = [
    ("schema_name",),
    ("digest",),
    ("bucket_number",),
    ("bucket_timer_low",),
    ("bucket_timer_high",),
    ("count_bucket",),
]


def counter_row(
    executions: int,
    latency_picoseconds: int,
    *,
    digest: str = TARGET_DIGEST,
    digest_text: str = "SELECT `film_id` FROM `film` WHERE `film_id` = ?",
    lock_picoseconds: int = 0,
    rows_examined: int = 0,
    rows_sent: int = 0,
) -> tuple:
    return (
        "sakila",
        digest,
        digest_text,
        str(executions),
        str(latency_picoseconds),
        str(lock_picoseconds),
        str(rows_examined),
        str(rows_sent),
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "2026-09-14 20:00:00",
        "2026-09-14 20:01:00",
    )


def histogram_row(
    bucket_number: int,
    timer_high_picoseconds: int,
    count: int,
    *,
    digest: str = TARGET_DIGEST,
) -> tuple:
    return (
        "sakila",
        digest,
        str(bucket_number),
        "0",
        str(timer_high_picoseconds),
        str(count),
    )


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.description: list[tuple[str]] = []
        self.rows: list[tuple] = []

    def execute(self, sql: str) -> None:
        self.connection.executed_sql.append(sql)
        index = len(self.connection.executed_sql) - 1
        self.description = self.connection.descriptions[index]
        self.rows = self.connection.snapshots[index]

    def fetchall(self) -> list[tuple]:
        return self.rows

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(
        self,
        snapshots: list[list[tuple]],
        descriptions: list[list[tuple[str]]] | None = None,
    ) -> None:
        self.snapshots = snapshots
        self.descriptions = descriptions or [DESCRIPTION] * len(snapshots)
        self.executed_sql: list[str] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.started_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.elapsed

    def now(self) -> datetime:
        return self.started_at + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds


class SelectLatencyTests(unittest.TestCase):
    def test_default_scope_targets_actor_popularity_for_thirty_seconds(self) -> None:
        self.assertEqual(WINDOW_SECONDS, 30)
        self.assertEqual(REFRESH_INTERVAL_SECONDS, 7)
        self.assertEqual(INITIAL_WINDOW_SECONDS, 30)
        self.assertEqual(INACTIVITY_RESET_SECONDS, 20)
        self.assertEqual(
            TARGET_DIGEST,
            "97eb2e3ec6c2ecefc310ed0c449384c04c59dc149f6cb31ca75e95a9a7836fe5",
        )

    def test_detects_cumulative_and_window_percentile_sources(self) -> None:
        histogram_columns = [
            "SCHEMA_NAME",
            "DIGEST",
            "BUCKET_NUMBER",
            "BUCKET_TIMER_LOW",
            "BUCKET_TIMER_HIGH",
            "COUNT_BUCKET",
        ]
        rows = [
            ("events_statements_summary_by_digest", "QUANTILE_95"),
            ("events_statements_summary_by_digest", "QUANTILE_99"),
            *[
                ("events_statements_histogram_by_digest", column)
                for column in histogram_columns
            ],
            *[
                ("events_statements_history_long", column)
                for column in [
                    "THREAD_ID",
                    "EVENT_ID",
                    "END_EVENT_ID",
                    "EVENT_NAME",
                    "CURRENT_SCHEMA",
                    "DIGEST",
                    "TIMER_WAIT",
                ]
            ],
        ]
        connection = FakeConnection(
            [rows, [("events_statements_history_long", "YES")]],
            [
                [("table_name",), ("column_name",)],
                [("consumer_name",), ("enabled",)],
            ],
        )

        capabilities = inspect_percentile_capabilities(connection)

        self.assertTrue(connection.closed)
        self.assertTrue(capabilities["cumulative_p95_available"])
        self.assertTrue(capabilities["cumulative_p99_available"])
        self.assertTrue(capabilities["window_p95_p99_available"])
        self.assertTrue(capabilities["history_window_p95_p99_available"])
        self.assertTrue(capabilities["history_long_consumer_enabled"])
        sql = connection.executed_sql[0].lower()
        self.assertIn("quantile_95", sql)
        self.assertIn("quantile_99", sql)
        self.assertIn("events_statements_histogram_by_digest", sql)

    def test_calculates_average_from_15_second_counter_delta(self) -> None:
        connection = FakeConnection(
            [
                [
                    counter_row(
                        10,
                        10_000_000_000_000,
                        lock_picoseconds=1_000_000_000_000,
                        rows_examined=100,
                        rows_sent=10,
                    ),
                    counter_row(
                        10,
                        1_000_000_000,
                        digest="protocol-marker",
                        digest_text="SELECT ?",
                    ),
                ],
                [
                    histogram_row(1, 1_000_000_000_000, 10),
                    histogram_row(
                        1,
                        1_000_000_000,
                        10,
                        digest="protocol-marker",
                    ),
                ],
                [
                    counter_row(
                        14,
                        18_000_000_000_000,
                        lock_picoseconds=1_400_000_000_000,
                        rows_examined=500,
                        rows_sent=50,
                    ),
                    counter_row(
                        14,
                        1_400_000_000,
                        digest="protocol-marker",
                        digest_text="SELECT ?",
                    ),
                ],
                [
                    histogram_row(1, 1_000_000_000_000, 11),
                    histogram_row(2, 2_000_000_000_000, 2),
                    histogram_row(3, 3_000_000_000_000, 1),
                    histogram_row(
                        1,
                        1_000_000_000,
                        14,
                        digest="protocol-marker",
                    ),
                ],
            ],
            [
                DESCRIPTION,
                HISTOGRAM_DESCRIPTION,
                DESCRIPTION,
                HISTOGRAM_DESCRIPTION,
            ],
        )
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = collect_select_latency(
                connection,
                output_dir=directory,
                window_seconds=15,
                sleep_fn=sleeps.append,
            )
            persisted = json.loads(
                (directory / "latest.json").read_text(encoding="utf-8")
            )
            html = (directory / "latest.html").read_text(encoding="utf-8")

        row = report["select_digests"][0]
        self.assertTrue(connection.closed)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 15, places=2)
        self.assertEqual(report["schema_version"], "select_latency_window.v9")
        self.assertEqual(report["select_digest_count"], 1)
        self.assertEqual(row["executions_in_window"], 4)
        self.assertEqual(row["total_latency_seconds"], 8.0)
        self.assertEqual(row["avg_latency_seconds"], 2.0)
        self.assertEqual(row["avg_lock_time_seconds"], 0.1)
        self.assertEqual(row["p95_latency_upper_bound_seconds"], 3.0)
        self.assertEqual(row["p99_latency_upper_bound_seconds"], 3.0)
        self.assertTrue(row["percentiles_are_estimates"])
        self.assertEqual(row["percentile_sample_count"], 4)
        self.assertTrue(row["percentile_sample_complete"])
        self.assertEqual(row["percentile_sample_coverage_pct"], 100.0)
        self.assertEqual(row["rows_examined_in_window"], 400)
        self.assertEqual(row["rows_sent_in_window"], 40)
        self.assertEqual(persisted["audit_id"], report["audit_id"])
        self.assertIn(report["audit_id"], html)
        self.assertIn(report["window_ended_at"], html)
        self.assertIn("nenhuma decisão de alerta é tomada pelo coletor", html)
        self.assertNotIn("Inteligência determinística", html)
        self.assertEqual(len(connection.executed_sql), 4)
        sql = connection.executed_sql[0].lower()
        self.assertIn("events_statements_summary_by_digest", sql)
        self.assertIn("schema_name = 'sakila'", sql)
        self.assertIn("digest_text like 'select %'", sql)
        self.assertIn("digest_text <> 'select ?'", sql)
        self.assertIn(f"digest = '{TARGET_DIGEST}'", sql)
        self.assertIn(
            "events_statements_histogram_by_digest",
            connection.executed_sql[1].lower(),
        )
        self.assertIn(
            "s.digest_text <> 'select ?'",
            connection.executed_sql[1].lower(),
        )

    def test_rolling_minute_is_refreshed_from_three_30_second_snapshots(self) -> None:
        counters = [
            counter_row(10, 10_000_000_000_000),
            counter_row(12, 14_000_000_000_000),
            counter_row(18, 26_000_000_000_000),
            counter_row(20, 34_000_000_000_000),
        ]
        histograms = [
            [histogram_row(1, 1_000_000_000_000, 10)],
            [
                histogram_row(1, 1_000_000_000_000, 11),
                histogram_row(2, 2_000_000_000_000, 1),
            ],
            [
                histogram_row(1, 1_000_000_000_000, 14),
                histogram_row(2, 2_000_000_000_000, 3),
                histogram_row(3, 3_000_000_000_000, 1),
            ],
            [
                histogram_row(1, 1_000_000_000_000, 14),
                histogram_row(2, 2_000_000_000_000, 4),
                histogram_row(3, 3_000_000_000_000, 2),
            ],
        ]
        snapshots: list[list[tuple]] = []
        descriptions: list[list[tuple[str]]] = []
        for counter, histogram in zip(counters, histograms, strict=True):
            snapshots.extend([[counter], histogram])
            descriptions.extend([DESCRIPTION, HISTOGRAM_DESCRIPTION])
        connection = FakeConnection(snapshots, descriptions)
        clock = FakeClock()
        events: list[dict] = []

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            generated = monitor_select_latency(
                connection,
                output_dir=directory,
                window_seconds=60,
                refresh_interval_seconds=30,
                max_reports=3,
                sleep_fn=clock.sleep,
                now_fn=clock.now,
                monotonic_fn=clock.monotonic,
                on_event=events.append,
            )
            report = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
            html = (directory / "latest.html").read_text(encoding="utf-8")

        row = report["select_digests"][0]
        self.assertEqual(generated, 3)
        self.assertTrue(connection.closed)
        self.assertEqual(clock.sleeps, [30.0, 30.0, 30.0])
        self.assertEqual(len(connection.executed_sql), 8)
        self.assertEqual(
            [event["status"] for event in events[:1]],
            ["waiting_for_activity"],
        )
        self.assertEqual(len(events), 4)
        self.assertEqual(report["schema_version"], "select_latency_window.v9")
        self.assertEqual(report["window_target_seconds"], 60)
        self.assertEqual(report["window_elapsed_seconds"], 60.0)
        self.assertEqual(report["refresh_interval_seconds"], 30)
        self.assertEqual(report["snapshot_count"], 3)
        self.assertEqual(report["interval_count"], 2)
        self.assertEqual(report["primary_latency_metric"], "avg_latency_seconds")
        self.assertEqual(
            report["scope"]["mode"],
            "rolling_interval_delta_weighted_average",
        )
        self.assertEqual(row["executions_in_window"], 8)
        self.assertEqual(row["total_latency_seconds"], 20.0)
        self.assertEqual(row["avg_latency_seconds"], 2.5)
        self.assertEqual(row["p95_latency_upper_bound_seconds"], 3.0)
        self.assertEqual(row["p99_latency_upper_bound_seconds"], 3.0)
        self.assertTrue(row["percentiles_are_estimates"])
        self.assertEqual(row["percentile_sample_count"], 8)
        self.assertEqual(len(row["interval_samples"]), 2)
        self.assertEqual(
            sum(sample["executions"] for sample in row["interval_samples"]),
            row["executions_in_window"],
        )
        self.assertEqual(
            sum(sample["total_latency_seconds"] for sample in row["interval_samples"]),
            row["total_latency_seconds"],
        )
        self.assertIn("janela de 60 segundos", html)
        self.assertIn("Atualizado a cada 30 segundos", html)
        self.assertIn("P95 estimado", html)

    def test_rolling_average_is_weighted_by_execution_count(self) -> None:
        counters = [
            counter_row(0, 0),
            counter_row(1, 10_000_000_000_000),
            counter_row(40, 49_000_000_000_000),
        ]
        histograms = [
            [],
            [histogram_row(2, 10_000_000_000_000, 1)],
            [
                histogram_row(1, 1_000_000_000_000, 39),
                histogram_row(2, 10_000_000_000_000, 1),
            ],
        ]
        snapshots: list[list[tuple]] = []
        descriptions: list[list[tuple[str]]] = []
        for counter, histogram in zip(counters, histograms, strict=True):
            snapshots.extend([[counter], histogram])
            descriptions.extend([DESCRIPTION, HISTOGRAM_DESCRIPTION])

        connection = FakeConnection(snapshots, descriptions)
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary:
            monitor_select_latency(
                connection,
                output_dir=Path(temporary),
                refresh_interval_seconds=15,
                max_reports=2,
                sleep_fn=clock.sleep,
                now_fn=clock.now,
                monotonic_fn=clock.monotonic,
            )
            report = json.loads(
                (Path(temporary) / "latest.json").read_text(encoding="utf-8")
            )

        row = report["select_digests"][0]
        self.assertEqual(report["window_target_seconds"], 30)
        self.assertEqual(report["configured_window_seconds"], 30)
        self.assertTrue(report["window_full"])
        self.assertEqual(report["window_phase"], "rolling")
        self.assertEqual(row["executions_in_window"], 40)
        self.assertEqual(row["total_latency_seconds"], 49.0)
        self.assertEqual(row["avg_latency_seconds"], 1.225)
        self.assertEqual(
            [sample["avg_latency_seconds"] for sample in row["interval_samples"]],
            [10.0, 1.0],
        )
        self.assertNotEqual(row["avg_latency_seconds"], 5.5)
        self.assertEqual(row["p95_latency_upper_bound_seconds"], 1.0)
        self.assertEqual(row["p99_latency_upper_bound_seconds"], 10.0)

    def test_default_monitor_keeps_a_thirty_second_window(self) -> None:
        snapshots: list[list[tuple]] = []
        descriptions: list[list[tuple[str]]] = []
        for executions in range(19):
            snapshots.append([counter_row(executions, executions * 1_000_000_000_000)])
            snapshots.append(
                []
                if executions == 0
                else [histogram_row(1, 1_000_000_000_000, executions)]
            )
            descriptions.extend([DESCRIPTION, HISTOGRAM_DESCRIPTION])

        connection = FakeConnection(snapshots, descriptions)
        clock = FakeClock()
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as temporary:
            generated = monitor_select_latency(
                connection,
                output_dir=Path(temporary),
                max_reports=5,
                sleep_fn=clock.sleep,
                now_fn=clock.now,
                monotonic_fn=clock.monotonic,
                on_event=events.append,
            )
            report = json.loads(
                (Path(temporary) / "latest.json").read_text(encoding="utf-8")
            )

        published = [event for event in events if "audit_id" in event]
        self.assertEqual(generated, 5)
        self.assertEqual(
            [event["window_target_seconds"] for event in published],
            [7.0, 14.0, 21.0, 28.0, 30],
        )
        self.assertEqual(
            [event["window_full"] for event in published],
            [False, False, False, False, True],
        )
        self.assertEqual(report["window_target_seconds"], 30)
        self.assertEqual(report["configured_window_seconds"], 30)
        self.assertTrue(report["window_full"])
        self.assertEqual(report["window_phase"], "rolling")
        self.assertEqual(report["refresh_interval_seconds"], 7)
        self.assertEqual(report["window_elapsed_seconds"], 28.0)
        self.assertEqual(report["window_alignment_error_seconds"], 2.0)
        self.assertEqual(report["snapshot_count"], 5)
        self.assertEqual(report["interval_count"], 4)
        self.assertNotIn("intelligence", report)
        self.assertEqual(
            report["domains"]["select_latency"]["metrics"]["decision_owner"],
            "health-check-luna",
        )

    def test_inactivity_clears_latest_json_and_html(self) -> None:
        cumulative_executions = [0, 1, 2, 2, 2]
        snapshots: list[list[tuple]] = []
        descriptions: list[list[tuple[str]]] = []
        for executions in cumulative_executions:
            snapshots.append([counter_row(executions, executions * 1_000_000_000_000)])
            snapshots.append(
                []
                if executions == 0
                else [histogram_row(1, 1_000_000_000_000, executions)]
            )
            descriptions.extend([DESCRIPTION, HISTOGRAM_DESCRIPTION])

        connection = FakeConnection(snapshots, descriptions)
        clock = FakeClock()
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as temporary:
            generated = monitor_select_latency(
                connection,
                output_dir=Path(temporary),
                refresh_interval_seconds=15,
                inactivity_reset_seconds=30,
                max_reports=4,
                sleep_fn=clock.sleep,
                now_fn=clock.now,
                monotonic_fn=clock.monotonic,
                on_event=events.append,
            )
            report = json.loads(
                (Path(temporary) / "latest.json").read_text(encoding="utf-8")
            )
            html = (Path(temporary) / "latest.html").read_text(encoding="utf-8")

        self.assertEqual(generated, 4)
        self.assertIn("activity_session_reset", [event["status"] for event in events])
        self.assertEqual(report["status"], "idle")
        self.assertEqual(report["window_phase"], "idle")
        self.assertEqual(report["select_digest_count"], 0)
        self.assertEqual(report["select_digests"], [])
        self.assertEqual(report["activity_session"]["status"], "idle")
        self.assertNotIn("intelligence", report)
        self.assertIn("Latência dos SELECTs — sem atividade", html)
        self.assertIn("Estado: <strong>Sem atividade</strong>", html)
        self.assertIn("Nenhum SELECT concluído", html)

    def test_monitor_starting_idle_replaces_previous_latest(self) -> None:
        snapshots: list[list[tuple]] = []
        descriptions: list[list[tuple[str]]] = []
        for _ in range(4):
            snapshots.append([counter_row(10, 10_000_000_000_000)])
            snapshots.append([histogram_row(1, 1_000_000_000_000, 10)])
            descriptions.extend([DESCRIPTION, HISTOGRAM_DESCRIPTION])

        connection = FakeConnection(snapshots, descriptions)
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "latest.json").write_text('{"stale": true}', encoding="utf-8")
            (directory / "latest.html").write_text("stale", encoding="utf-8")
            generated = monitor_select_latency(
                connection,
                output_dir=directory,
                max_reports=1,
                sleep_fn=clock.sleep,
                now_fn=clock.now,
                monotonic_fn=clock.monotonic,
            )
            report = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
            html = (directory / "latest.html").read_text(encoding="utf-8")

        self.assertEqual(generated, 1)
        self.assertEqual(clock.elapsed, 7)
        self.assertEqual(report["status"], "idle")
        self.assertEqual(report["select_digests"], [])
        self.assertNotIn("stale", html)

    def test_new_activity_after_idle_restarts_at_30_seconds(self) -> None:
        cumulative_executions = [0, 1, 2, 2, 2, 3, 4]
        snapshots: list[list[tuple]] = []
        descriptions: list[list[tuple[str]]] = []
        for executions in cumulative_executions:
            snapshots.append([counter_row(executions, executions * 1_000_000_000_000)])
            snapshots.append(
                []
                if executions == 0
                else [histogram_row(1, 1_000_000_000_000, executions)]
            )
            descriptions.extend([DESCRIPTION, HISTOGRAM_DESCRIPTION])

        connection = FakeConnection(snapshots, descriptions)
        clock = FakeClock()
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as temporary:
            generated = monitor_select_latency(
                connection,
                output_dir=Path(temporary),
                refresh_interval_seconds=15,
                inactivity_reset_seconds=30,
                max_reports=6,
                sleep_fn=clock.sleep,
                now_fn=clock.now,
                monotonic_fn=clock.monotonic,
                on_event=events.append,
            )
            report = json.loads(
                (Path(temporary) / "latest.json").read_text(encoding="utf-8")
            )

        active_reports = [
            event
            for event in events
            if event.get("activity_status") == "active" and "audit_id" in event
        ]
        self.assertEqual(generated, 6)
        self.assertEqual(
            [event["window_target_seconds"] for event in active_reports],
            [15.0, 30, 30, 15.0, 30],
        )
        self.assertEqual(report["activity_session"]["status"], "active")
        self.assertEqual(report["window_target_seconds"], 30)
        self.assertEqual(report["select_digests"][0]["executions_in_window"], 2)
        self.assertEqual(
            [
                sample["executions"]
                for sample in report["select_digests"][0]["interval_samples"]
            ],
            [1, 1],
        )

    def test_unchanged_counters_mean_no_select_completed_in_window(self) -> None:
        snapshot = [counter_row(10, 10_000_000_000_000)]
        histogram = [histogram_row(1, 1_000_000_000_000, 10)]
        connection = FakeConnection(
            [snapshot, histogram, snapshot, histogram],
            [
                DESCRIPTION,
                HISTOGRAM_DESCRIPTION,
                DESCRIPTION,
                HISTOGRAM_DESCRIPTION,
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = collect_select_latency(
                connection,
                output_dir=directory,
                sleep_fn=lambda _: None,
            )
            html = (directory / "latest.html").read_text(encoding="utf-8")

        self.assertEqual(report["status"], "available")
        self.assertEqual(report["select_digest_count"], 0)
        self.assertIn("Nenhum SELECT concluído", html)

    def test_counter_reset_is_reported_and_not_used(self) -> None:
        connection = FakeConnection(
            [
                [[*counter_row(10, 10_000_000_000_000)]],
                [[*histogram_row(1, 1_000_000_000_000, 10)]],
                [[*counter_row(2, 1_000_000_000_000)]],
                [[*histogram_row(1, 1_000_000_000_000, 2)]],
            ],
            [
                DESCRIPTION,
                HISTOGRAM_DESCRIPTION,
                DESCRIPTION,
                HISTOGRAM_DESCRIPTION,
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            report = collect_select_latency(
                connection,
                output_dir=Path(temporary),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(report["select_digest_count"], 0)
        self.assertEqual(report["counter_reset_digests"], [TARGET_DIGEST])


if __name__ == "__main__":
    unittest.main()
