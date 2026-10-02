"""Rolling latency reports for completed SELECT digests in ``sakila``."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.db import DatabaseRunner
from src.models import QueryResult, SourceStatus
from src.mysql_cli import MysqlCliConnection
from src.normalizer import normalize_digest_text, numeric_rows

BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "select_latency_window.v9"
WINDOW_SECONDS = 30
# Kept as a public compatibility constant. Partial windows are now published
# at every refresh instead of waiting for this duration.
INITIAL_WINDOW_SECONDS = 30
REFRESH_INTERVAL_SECONDS = 7
INACTIVITY_RESET_SECONDS = 20
TARGET_DIGEST = "97eb2e3ec6c2ecefc310ed0c449384c04c59dc149f6cb31ca75e95a9a7836fe5"
_IGNORED_DIGEST_TEXTS = frozenset({"SELECT ?"})

_COUNTER_FIELDS = {
    "executions",
    "total_latency_picoseconds",
    "total_lock_time_picoseconds",
    "rows_examined",
    "rows_sent",
    "tmp_tables",
    "tmp_disk_tables",
    "sort_rows",
    "no_index_used",
    "no_good_index_used",
    "errors",
    "warnings",
}

_HISTOGRAM_COLUMNS = {
    "SCHEMA_NAME",
    "DIGEST",
    "BUCKET_NUMBER",
    "BUCKET_TIMER_LOW",
    "BUCKET_TIMER_HIGH",
    "COUNT_BUCKET",
}

_HISTORY_COLUMNS = {
    "THREAD_ID",
    "EVENT_ID",
    "END_EVENT_ID",
    "EVENT_NAME",
    "CURRENT_SCHEMA",
    "DIGEST",
    "TIMER_WAIT",
}


@dataclass(frozen=True)
class _Snapshot:
    observed_at: datetime
    monotonic_at: float
    counters: QueryResult
    histogram: QueryResult
    counter_rows: list[dict[str, Any]]
    histogram_rows: list[dict[str, Any]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _load_policy() -> dict[str, Any]:
    value = json.loads((BASE_DIR / "policy.json").read_text(encoding="utf-8"))
    if value.get("scope", {}).get("allowed_schemas") != ["sakila"]:
        raise ValueError("select_latency_schema_policy_must_be_sakila_only")
    return value


def _output_dir() -> Path:
    configured = os.environ.get("HEALTHCHECK_SELECT_LATENCY_DIR")
    if not configured:
        return BASE_DIR / "select_latency" / "results"
    path = Path(configured)
    return path if path.is_absolute() else BASE_DIR / path


def _normalize_counters(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = numeric_rows(values, _COUNTER_FIELDS)
    allowed = _COUNTER_FIELDS | {
        "schema_name",
        "digest",
        "digest_text",
        "first_seen",
        "last_seen",
    }
    output: list[dict[str, Any]] = []
    for row in normalized:
        if str(row.get("digest") or "") != TARGET_DIGEST:
            continue
        item = {key: row.get(key) for key in allowed}
        item["digest_text"] = normalize_digest_text(item.get("digest_text"))
        output.append(item)
    return output


def _normalize_histogram(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = numeric_rows(
        values,
        {
            "bucket_number",
            "bucket_timer_low",
            "bucket_timer_high",
            "count_bucket",
        },
    )
    return [row for row in normalized if str(row.get("digest") or "") == TARGET_DIGEST]


def _counter_delta(current: Any, previous: Any) -> int:
    current_value = int(current or 0)
    previous_value = int(previous or 0)
    return current_value - previous_value


def _histogram_percentile(
    buckets: list[tuple[int, int]],
    percentile: float,
) -> float | None:
    sample_count = sum(count for _, count in buckets)
    if sample_count <= 0:
        return None
    rank = max(1, math.ceil(sample_count * percentile))
    cumulative = 0
    for timer_high, count in sorted(buckets):
        cumulative += count
        if cumulative >= rank:
            return round(timer_high / 1_000_000_000_000, 6)
    return None


def _histogram_bucket_deltas(
    baseline_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[int, int]], list[str]]:
    baseline = {
        (str(row.get("digest") or ""), int(row.get("bucket_number") or 0)): row
        for row in baseline_rows
        if row.get("digest")
    }
    final = {
        (str(row.get("digest") or ""), int(row.get("bucket_number") or 0)): row
        for row in final_rows
        if row.get("digest")
    }
    buckets_by_digest: dict[str, dict[int, int]] = {}
    reset_digests: set[str] = set()

    for key in set(baseline) | set(final):
        digest, _ = key
        previous = baseline.get(key)
        current = final.get(key)
        count = _counter_delta(
            current.get("count_bucket") if current else None,
            previous.get("count_bucket") if previous else None,
        )
        if count < 0:
            reset_digests.add(digest)
            continue
        if count > 0 and current is not None:
            timer_high = int(current.get("bucket_timer_high") or 0)
            buckets_by_digest.setdefault(digest, {})[timer_high] = count

    return buckets_by_digest, sorted(reset_digests)


def _percentiles_for_histogram_intervals(
    intervals: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    aggregate_buckets: dict[str, dict[int, int]] = {}
    reset_digests: set[str] = set()
    for baseline_rows, final_rows in intervals:
        interval_buckets, interval_resets = _histogram_bucket_deltas(
            baseline_rows,
            final_rows,
        )
        reset_digests.update(interval_resets)
        for digest, buckets in interval_buckets.items():
            target = aggregate_buckets.setdefault(digest, {})
            for timer_high, count in buckets.items():
                target[timer_high] = target.get(timer_high, 0) + count

    output: dict[str, dict[str, Any]] = {}
    for digest, bucket_counts in aggregate_buckets.items():
        if digest in reset_digests:
            continue
        buckets = list(bucket_counts.items())
        output[digest] = {
            "percentile_sample_count": sum(count for _, count in buckets),
            "p95_latency_upper_bound_seconds": _histogram_percentile(buckets, 0.95),
            "p99_latency_upper_bound_seconds": _histogram_percentile(buckets, 0.99),
            "percentile_method": "performance_schema_histogram_bucket_upper_bound",
            "percentiles_are_estimates": True,
        }
    return output, sorted(reset_digests)


def _window_percentiles(
    baseline_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    return _percentiles_for_histogram_intervals([(baseline_rows, final_rows)])


def _rolling_window_percentiles(
    snapshots: list[_Snapshot],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    return _percentiles_for_histogram_intervals(
        [
            (baseline.histogram_rows, final.histogram_rows)
            for baseline, final in pairwise(snapshots)
        ]
    )


def _counter_delta_rows(
    baseline_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    baseline = {str(row["digest"]): row for row in baseline_rows if row.get("digest")}
    results: dict[str, dict[str, Any]] = {}
    reset_digests: list[str] = []

    for current in final_rows:
        digest = str(current.get("digest") or "")
        if not digest:
            continue
        digest_text = str(current.get("digest_text") or "").strip()
        if digest_text.upper() in _IGNORED_DIGEST_TEXTS:
            continue
        previous = baseline.get(digest)
        deltas = {
            field: _counter_delta(
                current.get(field), previous.get(field) if previous else None
            )
            for field in _COUNTER_FIELDS
        }
        if any(value < 0 for value in deltas.values()):
            reset_digests.append(digest)
            continue
        executions = deltas["executions"]
        if executions <= 0:
            continue
        results[digest] = {
            "schema_name": "sakila",
            "digest": digest,
            "digest_text": digest_text,
            "deltas": deltas,
            "first_seen": current.get("first_seen"),
            "last_seen": current.get("last_seen"),
        }

    return results, sorted(reset_digests)


def _render_counter_row(
    entry: dict[str, Any],
    percentiles: dict[str, dict[str, Any]],
    *,
    interval_samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    deltas = entry["deltas"]
    executions = deltas["executions"]
    total_seconds = deltas["total_latency_picoseconds"] / 1_000_000_000_000
    lock_seconds = deltas["total_lock_time_picoseconds"] / 1_000_000_000_000
    item = {
        "schema_name": entry["schema_name"],
        "digest": entry["digest"],
        "digest_text": entry["digest_text"],
        "executions_in_window": executions,
        "total_latency_seconds": round(total_seconds, 6),
        "avg_latency_seconds": round(total_seconds / executions, 6),
        "total_lock_time_seconds": round(lock_seconds, 6),
        "avg_lock_time_seconds": round(lock_seconds / executions, 6),
        "rows_examined_in_window": deltas["rows_examined"],
        "rows_sent_in_window": deltas["rows_sent"],
        "tmp_tables_in_window": deltas["tmp_tables"],
        "tmp_disk_tables_in_window": deltas["tmp_disk_tables"],
        "sort_rows_in_window": deltas["sort_rows"],
        "no_index_used_in_window": deltas["no_index_used"],
        "no_good_index_used_in_window": deltas["no_good_index_used"],
        "errors_in_window": deltas["errors"],
        "warnings_in_window": deltas["warnings"],
        "first_seen": entry.get("first_seen"),
        "last_seen": entry.get("last_seen"),
        "interval_samples": interval_samples or [],
    }
    item.update(
        percentiles.get(
            entry["digest"],
            {
                "percentile_sample_count": 0,
                "p95_latency_upper_bound_seconds": None,
                "p99_latency_upper_bound_seconds": None,
                "percentile_method": "performance_schema_histogram_bucket_upper_bound",
                "percentiles_are_estimates": True,
            },
        )
    )
    percentile_samples = int(item["percentile_sample_count"])
    item["percentile_sample_complete"] = percentile_samples == executions
    item["percentile_sample_coverage_pct"] = round(
        percentile_samples / executions * 100,
        2,
    )
    return item


def _window_rows(
    baseline_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
    percentiles: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    entries, reset_digests = _counter_delta_rows(baseline_rows, final_rows)
    results = [_render_counter_row(entry, percentiles) for entry in entries.values()]

    results.sort(
        key=lambda item: (
            -item["avg_latency_seconds"],
            -item["total_latency_seconds"],
            item["digest"],
        )
    )
    return results, reset_digests


def _rolling_window_rows(
    snapshots: list[_Snapshot],
    percentiles: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    interval_results: list[tuple[_Snapshot, _Snapshot, dict[str, dict[str, Any]]]] = []
    reset_digests: set[str] = set()

    for baseline, final in pairwise(snapshots):
        entries, interval_resets = _counter_delta_rows(
            baseline.counter_rows,
            final.counter_rows,
        )
        reset_digests.update(interval_resets)
        interval_results.append((baseline, final, entries))

    digests = {
        digest
        for _, _, entries in interval_results
        for digest in entries
        if digest not in reset_digests
    }
    aggregates: dict[str, dict[str, Any]] = {}
    for digest in digests:
        seed = next(
            entries[digest]
            for _, _, entries in reversed(interval_results)
            if digest in entries
        )
        aggregate = {
            **seed,
            "deltas": {field: 0 for field in _COUNTER_FIELDS},
            "interval_samples": [],
        }
        for baseline, final, entries in interval_results:
            entry = entries.get(digest)
            interval_deltas = (
                entry["deltas"]
                if entry is not None
                else {field: 0 for field in _COUNTER_FIELDS}
            )
            if entry is not None:
                aggregate["digest_text"] = entry["digest_text"]
                aggregate["first_seen"] = entry.get("first_seen")
                aggregate["last_seen"] = entry.get("last_seen")
            for field in _COUNTER_FIELDS:
                aggregate["deltas"][field] += interval_deltas[field]

            executions = interval_deltas["executions"]
            total_picoseconds = interval_deltas["total_latency_picoseconds"]
            aggregate["interval_samples"].append(
                {
                    "started_at": _iso(baseline.observed_at),
                    "ended_at": _iso(final.observed_at),
                    "elapsed_seconds": round(
                        final.monotonic_at - baseline.monotonic_at,
                        3,
                    ),
                    "executions": executions,
                    "total_latency_seconds": round(
                        total_picoseconds / 1_000_000_000_000,
                        6,
                    ),
                    "avg_latency_seconds": (
                        round(
                            total_picoseconds / executions / 1_000_000_000_000,
                            6,
                        )
                        if executions > 0
                        else None
                    ),
                }
            )
        aggregates[digest] = aggregate

    results = [
        _render_counter_row(
            entry,
            percentiles,
            interval_samples=entry["interval_samples"],
        )
        for entry in aggregates.values()
    ]
    results.sort(
        key=lambda item: (
            -item["avg_latency_seconds"],
            -item["total_latency_seconds"],
            item["digest"],
        )
    )
    return results, sorted(reset_digests)


def render_select_latency_html(report: dict[str, Any]) -> str:
    def escape(value: Any) -> str:
        return html.escape(str(value if value is not None else "—"), quote=True)

    def brazil_time(value: Any) -> str:
        """Format UTC evidence for the operator without changing its source."""
        try:
            observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return observed.astimezone(ZoneInfo("America/Sao_Paulo")).strftime(
                "%d/%m/%Y %H:%M:%S BRT"
            )
        except ValueError:
            return str(value)

    def timestamp(value: Any) -> str:
        # Keep the canonical UTC value in ``datetime`` for identity validation;
        # expose only the Brazil local time to the operator.
        return f'<time datetime="{escape(value)}">{escape(brazil_time(value))}</time>'

    rows = []
    for item in report["select_digests"]:
        rows.append(
            "<tr>"
            f"<td>{escape(item['avg_latency_seconds'])}</td>"
            f"<td>{escape(item['p95_latency_upper_bound_seconds'])}</td>"
            f"<td>{escape(item['p99_latency_upper_bound_seconds'])}</td>"
            f"<td>{escape(item['executions_in_window'])}</td>"
            f"<td>{escape(item['percentile_sample_count'])}</td>"
            f"<td>{escape(item['percentile_sample_coverage_pct'])}%</td>"
            f"<td>{escape(item['total_latency_seconds'])}</td>"
            f"<td>{escape(item['avg_lock_time_seconds'])}</td>"
            f"<td>{escape(item['rows_examined_in_window'])}</td>"
            f"<td>{escape(item['rows_sent_in_window'])}</td>"
            f"<td><code>{escape(item['digest'])}</code></td>"
            f"<td><code>{escape(item['digest_text'])}</code></td>"
            "</tr>"
        )
    if not rows:
        rows.append(
            "<tr><td colspan='12'>Nenhum SELECT concluído durante a janela.</td></tr>"
        )
    window_seconds = escape(report["window_target_seconds"])
    refresh = report.get("refresh_interval_seconds")
    refresh_note = (
        f" Atualizado a cada {escape(refresh)} segundos." if refresh is not None else ""
    )
    activity_status = (report.get("activity_session") or {}).get("status")
    idle = activity_status == "idle"
    window_full = bool(report.get("window_full"))
    title = (
        "Latência dos SELECTs — sem atividade"
        if idle
        else f"Latência dos SELECTs — janela de {window_seconds} segundos"
    )
    activity_label = "Sem atividade" if idle else "Ativa"
    window_label = (
        "Sem atividade"
        if idle
        else (
            "Completa — elegível para análise"
            if window_full
            else "Em formação — ainda não elegível para alerta"
        )
    )
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="mysqlconf-audit-id" content="{escape(report["audit_id"])}">
  <meta name="mysqlconf-collected-at" content="{escape(report["collected_at"])}">
  <meta name="mysqlconf-schema-version" content="{escape(report["schema_version"])}">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #17202a; }}
    .note {{ background: #eaf4ff; border-left: 4px solid #2874a6; padding: 1rem; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: .55rem;
      text-align: left; vertical-align: top; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>Audit ID: <code>{escape(report["audit_id"])}</code></p>
  <p>Janela: {timestamp(report["window_started_at"])} até
    {timestamp(report["window_ended_at"])}</p>
  <p>Estado: <strong>{activity_label}</strong></p>
  <p>Janela de análise: <strong>{window_label}</strong></p>
  <p>Digests executados: <strong>{report["select_digest_count"]}</strong></p>
  <p class="note">A média móvel é ponderada por execução: soma da latência
  dividida pela soma das execuções dos intervalos. P95 e P99 são apenas
  estimativas do limite superior do bucket do histograma. Este documento contém
  somente evidências da coleta; nenhuma decisão de alerta é tomada pelo coletor.
  O agrupamento usa o digest normalizado e não armazena parâmetros ou SQL
  literal.{refresh_note}</p>
  <table>
    <thead><tr><th>Média móvel (s)</th><th>P95 estimado (s)</th>
    <th>P99 estimado (s)</th>
    <th>Execuções</th><th>Amostras percentis</th><th>Cobertura</th><th>Total (s)</th>
    <th>Lock médio (s)</th><th>Linhas examinadas</th><th>Linhas enviadas</th>
    <th>Digest</th><th>SELECT normalizado</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</body>
</html>
"""


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_pair(directory: Path, report: dict[str, Any], html_document: str) -> None:
    if report["audit_id"] not in html_document:
        raise ValueError("select_latency_html_audit_id_mismatch")
    if report["window_ended_at"] not in html_document:
        raise ValueError("select_latency_html_window_mismatch")
    directory.mkdir(parents=True, exist_ok=True)
    targets = {
        directory / "latest.json": _json_bytes(report),
        directory / "latest.html": html_document.encode("utf-8"),
    }
    previous = {path: path.read_bytes() if path.exists() else None for path in targets}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, content in targets.items():
            temporary = path.with_name(path.name + ".tmp")
            with temporary.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged[path] = temporary
        for path in targets:
            os.replace(staged[path], path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            old = previous[path]
            if old is None:
                path.unlink(missing_ok=True)
            else:
                rollback = path.with_name(path.name + ".rollback.tmp")
                rollback.write_bytes(old)
                os.replace(rollback, path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _finalize_report(directory: Path, report: dict[str, Any]) -> None:
    report["overall_status"] = "unknown"
    report["domains"]["select_latency"]["status"] = "unknown"
    report["domains"]["select_latency"]["metrics"] = {
        "decision_owner": "health-check-luna",
    }
    report["findings"] = []
    report["domains"]["select_latency"]["findings"] = []
    html_document = render_select_latency_html(report)
    _write_pair(directory, report, html_document)


def _snapshot(runner: DatabaseRunner) -> tuple[QueryResult, QueryResult]:
    results = runner.execute_file(
        "110_select_latency.sql",
        {},
        ["select_digest_counters", "select_digest_histogram"],
        parallel=True,
    )
    return results["select_digest_counters"], results["select_digest_histogram"]


def _capture_snapshot(
    runner: DatabaseRunner,
    *,
    now_fn: Callable[[], datetime] = _utc_now,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> _Snapshot:
    wall_started = now_fn()
    monotonic_started = monotonic_fn()
    counters, histogram = _snapshot(runner)
    wall_finished = now_fn()
    monotonic_finished = monotonic_fn()
    observed_at = wall_started + (wall_finished - wall_started) / 2
    monotonic_at = monotonic_started + (monotonic_finished - monotonic_started) / 2
    return _Snapshot(
        observed_at=observed_at,
        monotonic_at=monotonic_at,
        counters=counters,
        histogram=histogram,
        counter_rows=(
            _normalize_counters(counters.rows)
            if counters.status == SourceStatus.AVAILABLE
            else []
        ),
        histogram_rows=(
            _normalize_histogram(histogram.rows)
            if histogram.status == SourceStatus.AVAILABLE
            else []
        ),
    )


def _snapshot_available(snapshot: _Snapshot) -> bool:
    return (
        snapshot.counters.status == SourceStatus.AVAILABLE
        and snapshot.histogram.status == SourceStatus.AVAILABLE
    )


def _snapshot_interval_activity(
    baseline: _Snapshot,
    final: _Snapshot,
) -> tuple[bool, bool]:
    """Return whether the target ran and whether its counters became invalid."""
    if not _snapshot_available(baseline) or not _snapshot_available(final):
        return False, True
    entries, reset_digests = _counter_delta_rows(
        baseline.counter_rows,
        final.counter_rows,
    )
    baseline_has_target = any(
        str(row.get("digest") or "") == TARGET_DIGEST for row in baseline.counter_rows
    )
    final_has_target = any(
        str(row.get("digest") or "") == TARGET_DIGEST for row in final.counter_rows
    )
    invalid = TARGET_DIGEST in reset_digests or (
        baseline_has_target and not final_has_target
    )
    return TARGET_DIGEST in entries, invalid


def _closest_window_snapshots(
    snapshots: list[_Snapshot],
    target_seconds: float,
) -> list[_Snapshot]:
    """Choose the sampled window closest to a target without interpolation."""
    final = snapshots[-1]
    baseline_index = min(
        range(len(snapshots) - 1),
        key=lambda index: abs(
            final.monotonic_at - snapshots[index].monotonic_at - target_seconds
        ),
    )
    return snapshots[baseline_index:]


def _build_window_report(
    baseline: _Snapshot,
    final: _Snapshot,
    *,
    window_seconds: float,
    refresh_interval_seconds: float | None,
    snapshot_count: int,
    snapshots: list[_Snapshot] | None = None,
    configured_window_seconds: float | None = None,
) -> dict[str, Any]:
    window_snapshots = snapshots or [baseline, final]
    sources_available = all(
        _snapshot_available(snapshot) for snapshot in window_snapshots
    )
    if sources_available:
        if snapshots is not None:
            percentiles, histogram_resets = _rolling_window_percentiles(snapshots)
            select_digests, counter_resets = _rolling_window_rows(
                snapshots,
                percentiles,
            )
        else:
            percentiles, histogram_resets = _window_percentiles(
                baseline.histogram_rows,
                final.histogram_rows,
            )
            select_digests, counter_resets = _window_rows(
                baseline.counter_rows,
                final.counter_rows,
                percentiles,
            )
        reset_digests = sorted(set(histogram_resets) | set(counter_resets))
    else:
        select_digests, reset_digests = [], []

    rolling = refresh_interval_seconds is not None
    configured_window = configured_window_seconds or window_seconds
    window_full = math.isclose(
        window_seconds,
        configured_window,
        rel_tol=0,
        abs_tol=1e-9,
    )
    window_elapsed_seconds = final.monotonic_at - baseline.monotonic_at
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_id": str(uuid.uuid4()),
        "started_at": _iso(baseline.observed_at),
        "finished_at": _iso(final.observed_at),
        "collected_at": _iso(final.observed_at),
        "duration_ms": round(window_elapsed_seconds * 1000),
        "window_started_at": _iso(baseline.observed_at),
        "window_ended_at": _iso(final.observed_at),
        "window_target_seconds": window_seconds,
        "configured_window_seconds": configured_window,
        "window_full": window_full,
        "window_phase": (
            "complete" if not rolling else ("rolling" if window_full else "growing")
        ),
        "window_elapsed_seconds": round(window_elapsed_seconds, 3),
        "window_alignment_error_seconds": round(
            abs(window_elapsed_seconds - window_seconds),
            3,
        ),
        "refresh_interval_seconds": refresh_interval_seconds,
        "snapshot_count": snapshot_count,
        "interval_count": max(0, snapshot_count - 1),
        "primary_latency_metric": "avg_latency_seconds",
        "target": {"engine": "MySQL", "service": "MySQL HeatWave"},
        "scope": {
            "schemas": ["sakila"],
            "statement_type": "SELECT",
            "query_scope": "single_digest",
            "target_digest": TARGET_DIGEST,
            "mode": (
                "rolling_interval_delta_weighted_average"
                if rolling
                else "digest_counter_delta"
            ),
            "ranking": "avg_latency_seconds_desc",
        },
        "status": (
            SourceStatus.AVAILABLE.value if sources_available else "not_available"
        ),
        "overall_status": "unknown",
        "capabilities": {
            "statement_digests": (
                "available" if sources_available else "not_available"
            ),
            "statement_histogram": (
                "available" if sources_available else "not_available"
            ),
        },
        "instance": {},
        "domains": {
            "select_latency": {
                "status": "unknown",
                "metrics": {},
                "findings": [],
            }
        },
        "findings": [],
        "data_retention": {
            "mode": "latest_only",
            "raw_query_text_persisted": False,
            "raw_logs_persisted": False,
        },
        "select_digest_count": len(select_digests),
        "select_digests": select_digests,
        "counter_reset_digests": reset_digests,
        "sources": {
            "baseline_counters": baseline.counters.metadata(),
            "baseline_histogram": baseline.histogram.metadata(),
            "final_counters": final.counters.metadata(),
            "final_histogram": final.histogram.metadata(),
        },
        "limitations": [
            "averages_are_grouped_by_normalized_digest",
            "window_is_measured_by_two_performance_schema_counter_snapshots",
            "counter_reset_is_excluded_but_digest_eviction_may_be_undetectable",
            "p95_and_p99_are_histogram_bucket_upper_bounds",
            "p95_and_p99_are_estimates_not_primary_alert_metrics",
            "percentile_sample_count_may_differ_from_statement_counter_delta",
            "window_boundary_is_aligned_to_the_nearest_periodic_snapshot",
            "only_digest_text_starting_with_select_is_included",
            "literal_only_select_digest_is_excluded",
            "no_raw_sql_or_literal_parameters",
        ],
    }


def _build_idle_report(
    snapshots: list[_Snapshot],
    *,
    inactivity_reset_seconds: float,
    configured_window_seconds: float,
    refresh_interval_seconds: float,
    last_activity_observed_at: datetime | None,
) -> dict[str, Any]:
    report = _build_window_report(
        snapshots[0],
        snapshots[-1],
        window_seconds=inactivity_reset_seconds,
        refresh_interval_seconds=refresh_interval_seconds,
        snapshot_count=len(snapshots),
        snapshots=snapshots,
        configured_window_seconds=configured_window_seconds,
    )
    report.update(
        {
            "status": "idle",
            "window_phase": "idle",
            "window_full": False,
            "select_digest_count": 0,
            "select_digests": [],
            "counter_reset_digests": [],
            "activity_session": {
                "status": "idle",
                "inactivity_reset_seconds": inactivity_reset_seconds,
                "last_activity_at": (
                    _iso(last_activity_observed_at)
                    if last_activity_observed_at is not None
                    else None
                ),
                "reset_at": _iso(snapshots[-1].observed_at),
            },
        }
    )
    return report


def inspect_percentile_capabilities(existing_connection: Any) -> dict[str, Any]:
    """Check read-only sources required for cumulative and window percentiles."""
    policy = _load_policy()
    timeout = int(policy["collection"]["query_timeout_seconds"])
    try:
        runner = DatabaseRunner(
            existing_connection, BASE_DIR / "select_latency" / "sql", timeout
        )
        runner.validate_all(("110_select_latency.sql",))
        sources = runner.execute_file(
            "110_select_latency.sql",
            {},
            ["percentile_capabilities", "history_consumer"],
            parallel=True,
        )
        source = sources["percentile_capabilities"]
        consumer_source = sources["history_consumer"]
    finally:
        try:
            existing_connection.close()
        except Exception:
            pass

    observed: dict[str, set[str]] = {}
    if source.status == SourceStatus.AVAILABLE:
        for row in source.rows:
            table = str(row.get("table_name") or "").lower()
            column = str(row.get("column_name") or "").upper()
            if table and column:
                observed.setdefault(table, set()).add(column)

    summary = observed.get("events_statements_summary_by_digest", set())
    histogram = observed.get("events_statements_histogram_by_digest", set())
    history = observed.get("events_statements_history_long", set())
    consumer_enabled = False
    if consumer_source.status == SourceStatus.AVAILABLE:
        consumer_enabled = any(
            str(row.get("consumer_name") or "").lower()
            == "events_statements_history_long"
            and str(row.get("enabled") or "").upper() == "YES"
            for row in consumer_source.rows
        )
    return {
        "status": source.status.value,
        "cumulative_p95_available": "QUANTILE_95" in summary,
        "cumulative_p99_available": "QUANTILE_99" in summary,
        "window_p95_p99_available": _HISTOGRAM_COLUMNS.issubset(histogram),
        "history_window_p95_p99_available": (
            _HISTORY_COLUMNS.issubset(history) and consumer_enabled
        ),
        "history_long_consumer_enabled": consumer_enabled,
        "summary_columns": sorted(summary),
        "histogram_columns": sorted(histogram),
        "history_columns": sorted(history),
        "sources": {
            "columns": source.metadata(),
            "history_consumer": consumer_source.metadata(),
        },
    }


def collect_select_latency(
    existing_connection: Any,
    *,
    output_dir: Path | None = None,
    window_seconds: float = WINDOW_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = _utc_now,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Produce one complete window from two Performance Schema snapshots."""
    if not 0 < window_seconds <= 3600:
        raise ValueError("select_latency_window_seconds_out_of_range")

    policy = _load_policy()
    timeout = int(policy["collection"]["query_timeout_seconds"])
    try:
        runner = DatabaseRunner(
            existing_connection, BASE_DIR / "select_latency" / "sql", timeout
        )
        runner.validate_all(("110_select_latency.sql",))
        started = monotonic_fn()
        baseline = _capture_snapshot(
            runner,
            now_fn=now_fn,
            monotonic_fn=monotonic_fn,
        )
        remaining = window_seconds - (monotonic_fn() - started)
        if remaining > 0:
            sleep_fn(remaining)
        final = _capture_snapshot(
            runner,
            now_fn=now_fn,
            monotonic_fn=monotonic_fn,
        )
    finally:
        try:
            existing_connection.close()
        except Exception:
            pass

    report = _build_window_report(
        baseline,
        final,
        window_seconds=window_seconds,
        refresh_interval_seconds=None,
        snapshot_count=2,
    )
    _finalize_report(output_dir or _output_dir(), report)
    return report


def monitor_select_latency(
    existing_connection: Any,
    *,
    output_dir: Path | None = None,
    window_seconds: float = WINDOW_SECONDS,
    refresh_interval_seconds: float = REFRESH_INTERVAL_SECONDS,
    inactivity_reset_seconds: float = INACTIVITY_RESET_SECONDS,
    max_reports: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = _utc_now,
    monotonic_fn: Callable[[], float] = time.monotonic,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Continuously collect and write a rolling window at a fixed cadence."""
    if not 0 < window_seconds <= 3600:
        raise ValueError("select_latency_window_seconds_out_of_range")
    if not 0 < refresh_interval_seconds <= window_seconds:
        raise ValueError("select_latency_refresh_interval_out_of_range")
    steps = math.ceil(window_seconds / refresh_interval_seconds)
    if max_reports is not None and max_reports <= 0:
        raise ValueError("select_latency_max_reports_must_be_positive")
    if inactivity_reset_seconds <= 0:
        raise ValueError("select_latency_inactivity_reset_must_be_positive")

    required_snapshots = steps + 1
    snapshots: deque[_Snapshot] = deque(maxlen=required_snapshots)
    previous_snapshot: _Snapshot | None = None
    last_activity_at: float | None = None
    last_activity_observed_at: datetime | None = None
    full_window_reached = False
    reports_generated = 0
    policy = _load_policy()
    timeout = int(policy["collection"]["query_timeout_seconds"])
    target_directory = output_dir or _output_dir()
    try:
        runner = DatabaseRunner(
            existing_connection, BASE_DIR / "select_latency" / "sql", timeout
        )
        runner.validate_all(("110_select_latency.sql",))
        next_tick = monotonic_fn()
        while max_reports is None or reports_generated < max_reports:
            remaining = next_tick - monotonic_fn()
            if remaining > 0:
                sleep_fn(remaining)
            current_snapshot = _capture_snapshot(
                runner,
                now_fn=now_fn,
                monotonic_fn=monotonic_fn,
            )
            next_tick += refresh_interval_seconds

            if previous_snapshot is None:
                previous_snapshot = current_snapshot
                if on_event is not None:
                    on_event({"status": "waiting_for_activity"})
                continue

            activity, interval_invalid = _snapshot_interval_activity(
                previous_snapshot,
                current_snapshot,
            )
            if interval_invalid:
                snapshots.clear()
                last_activity_at = None
                last_activity_observed_at = None
                full_window_reached = False
                previous_snapshot = (
                    current_snapshot if _snapshot_available(current_snapshot) else None
                )
                if on_event is not None:
                    on_event({"status": "source_or_counter_reset"})
                continue

            if not snapshots:
                if not activity:
                    # The Health Check is a continuous collector: publish a
                    # fresh idle snapshot on every cadence, not only after a
                    # prolonged absence of SELECT activity.
                    idle_report = _build_idle_report(
                        [previous_snapshot, current_snapshot],
                        inactivity_reset_seconds=inactivity_reset_seconds,
                        configured_window_seconds=window_seconds,
                        refresh_interval_seconds=refresh_interval_seconds,
                        last_activity_observed_at=None,
                    )
                    _finalize_report(target_directory, idle_report)
                    reports_generated += 1
                    if on_event is not None:
                        on_event(
                            {
                                "audit_id": idle_report["audit_id"],
                                "status": "idle",
                                "activity_status": "idle",
                                "select_digest_count": 0,
                            }
                        )
                    previous_snapshot = current_snapshot
                    continue
                snapshots.extend((previous_snapshot, current_snapshot))
            else:
                snapshots.append(current_snapshot)

            if activity:
                last_activity_at = current_snapshot.monotonic_at
                last_activity_observed_at = current_snapshot.observed_at
            elif (
                last_activity_at is not None
                and current_snapshot.monotonic_at - last_activity_at
                >= inactivity_reset_seconds
            ):
                idle_snapshots = [
                    snapshot
                    for snapshot in snapshots
                    if snapshot.monotonic_at >= last_activity_at
                ]
                idle_report = _build_idle_report(
                    idle_snapshots,
                    inactivity_reset_seconds=inactivity_reset_seconds,
                    configured_window_seconds=window_seconds,
                    refresh_interval_seconds=refresh_interval_seconds,
                    last_activity_observed_at=last_activity_observed_at,
                )
                _finalize_report(target_directory, idle_report)
                reports_generated += 1
                snapshots.clear()
                last_activity_at = None
                last_activity_observed_at = None
                full_window_reached = False
                previous_snapshot = current_snapshot
                if on_event is not None:
                    on_event(
                        {
                            "audit_id": idle_report["audit_id"],
                            "status": "activity_session_reset",
                            "activity_status": "idle",
                            "reason": "no_new_executions",
                            "inactivity_seconds": inactivity_reset_seconds,
                            "select_digest_count": 0,
                        }
                    )
                continue

            previous_snapshot = current_snapshot

            active_snapshots = list(snapshots)
            session_elapsed_seconds = (
                active_snapshots[-1].monotonic_at - active_snapshots[0].monotonic_at
            )
            # Publish evidence at every cadence.  Before the configured window
            # is complete this is explicitly marked as a growing window, so
            # Luna can return inconclusive rather than alerting prematurely.
            report_window_seconds: float
            if full_window_reached:
                report_window_seconds = window_seconds
                active_snapshots = _closest_window_snapshots(
                    active_snapshots,
                    window_seconds,
                )
            elif session_elapsed_seconds >= window_seconds:
                full_window_reached = True
                report_window_seconds = window_seconds
                active_snapshots = _closest_window_snapshots(
                    active_snapshots,
                    window_seconds,
                )
            else:
                report_window_seconds = round(session_elapsed_seconds, 3)
            report = _build_window_report(
                active_snapshots[0],
                active_snapshots[-1],
                window_seconds=report_window_seconds,
                refresh_interval_seconds=refresh_interval_seconds,
                snapshot_count=len(active_snapshots),
                snapshots=active_snapshots,
                configured_window_seconds=window_seconds,
            )
            report["activity_session"] = {
                "status": "active",
                "inactivity_reset_seconds": inactivity_reset_seconds,
                "last_activity_at": (
                    _iso(last_activity_observed_at)
                    if last_activity_observed_at is not None
                    else None
                ),
            }
            _finalize_report(target_directory, report)
            reports_generated += 1
            if on_event is not None:
                on_event(
                    {
                        "audit_id": report["audit_id"],
                        "window_started_at": report["window_started_at"],
                        "window_ended_at": report["window_ended_at"],
                        "window_target_seconds": report["window_target_seconds"],
                        "window_full": report["window_full"],
                        "status": report["status"],
                        "activity_status": "active",
                        "select_digest_count": report["select_digest_count"],
                    }
                )
    finally:
        try:
            existing_connection.close()
        except Exception:
            pass
    return reports_generated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure completed SELECT digest latency in sakila"
    )
    parser.add_argument("command", choices=["collect", "monitor", "capabilities"])
    parser.add_argument(
        "--max-reports",
        type=int,
        help="stop monitor after this many complete rolling reports",
    )
    arguments = parser.parse_args()
    if arguments.command != "monitor" and arguments.max_reports is not None:
        parser.error("--max-reports is valid only with monitor")
    if arguments.max_reports is not None and arguments.max_reports <= 0:
        parser.error("--max-reports must be positive")
    policy = _load_policy()
    try:
        connection = MysqlCliConnection.from_environment(
            int(policy["collection"]["query_timeout_seconds"])
        )
        if arguments.command == "capabilities":
            capabilities = inspect_percentile_capabilities(connection)
            json.dump(capabilities, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0 if capabilities["status"] == "available" else 1
        if arguments.command == "monitor":
            try:
                monitor_select_latency(
                    connection,
                    max_reports=arguments.max_reports,
                    on_event=lambda event: (
                        json.dump(event, sys.stdout, ensure_ascii=False),
                        sys.stdout.write("\n"),
                        sys.stdout.flush(),
                    ),
                )
            except KeyboardInterrupt:
                json.dump({"status": "stopped"}, sys.stdout)
                sys.stdout.write("\n")
            return 0
        report = collect_select_latency(connection)
    except (OSError, RuntimeError, ValueError) as error:
        print(
            f"select_latency_collection_failed:{type(error).__name__}",
            file=sys.stderr,
        )
        return 1
    json.dump(
        {
            "audit_id": report["audit_id"],
            "window_started_at": report["window_started_at"],
            "window_ended_at": report["window_ended_at"],
            "status": report["status"],
            "select_digest_count": report["select_digest_count"],
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0 if report["status"] == SourceStatus.AVAILABLE.value else 1


if __name__ == "__main__":
    raise SystemExit(main())
