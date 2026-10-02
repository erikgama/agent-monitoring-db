#!/usr/bin/env python3
"""Infraestrutura comum para workloads exclusivamente de leitura no Sakila."""

from __future__ import annotations

import argparse
import json
import math
import queue
import re
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DBA_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DBA_DIR / "scripts"))

import sakila_realistic_workload as base

FORBIDDEN_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|MERGE|ALTER|CREATE|DROP|TRUNCATE|"
    r"GRANT|REVOKE|CALL|LOAD|LOCK|UNLOCK|HANDLER|SET)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuerySpec:
    name: str
    build: Callable[[int], str]


def canonical_actor_popularity(_sequence: int) -> str:
    """Consulta canônica usada igualmente nos dois perfis comparáveis."""
    return """
SELECT a.actor_id, a.first_name, a.last_name,
       COUNT(DISTINCT fa.film_id) AS films,
       COUNT(DISTINCT r.rental_id) AS rentals,
       COALESCE(SUM(p.amount), 0) AS revenue
  FROM sakila.actor AS a
  JOIN sakila.film_actor AS fa ON fa.actor_id = a.actor_id
  JOIN sakila.inventory AS i ON i.film_id = fa.film_id
  LEFT JOIN sakila.rental AS r ON r.inventory_id = i.inventory_id
  LEFT JOIN sakila.payment AS p ON p.rental_id = r.rental_id
 WHERE a.actor_id >= 1
 GROUP BY a.actor_id, a.first_name, a.last_name
 ORDER BY revenue DESC, rentals DESC, a.actor_id
 LIMIT 100
""".strip()


CANONICAL_QUERY = QuerySpec("actor_popularity", canonical_actor_popularity)


@dataclass(frozen=True)
class Job:
    sequence: int
    attempts: int = 0


@dataclass
class ReadStats:
    started_at: str
    query_names: Sequence[str]
    lock: threading.Lock = field(default_factory=threading.Lock)
    offered: int = 0
    enqueued: int = 0
    rejected: int = 0
    completed: int = 0
    failed: int = 0
    retries: int = 0
    latency_sum_seconds: float = 0.0
    latency_min_seconds: float | None = None
    latency_max_seconds: float | None = None
    latencies_seconds: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    per_query: dict[str, dict[str, float | int]] = field(init=False)

    def __post_init__(self) -> None:
        self.per_query = {
            name: {
                "completed": 0,
                "latency_sum_seconds": 0.0,
                "latency_max_seconds": 0.0,
            }
            for name in self.query_names
        }

    def add_offered(self) -> None:
        with self.lock:
            self.offered += 1

    def add_enqueued(self) -> None:
        with self.lock:
            self.enqueued += 1

    def add_rejected(self) -> None:
        with self.lock:
            self.rejected += 1

    def add_retry(self, error: BaseException) -> None:
        with self.lock:
            self.retries += 1
            if len(self.errors) < 30:
                self.errors.append(f"retry: {base.sanitize_error(str(error))}")

    def add_failure(self, error: BaseException) -> None:
        with self.lock:
            self.failed += 1
            if len(self.errors) < 30:
                self.errors.append(base.sanitize_error(str(error)))

    def add_success(self, query_name: str, elapsed: float) -> None:
        with self.lock:
            self.completed += 1
            self.latency_sum_seconds += elapsed
            self.latency_min_seconds = (
                elapsed
                if self.latency_min_seconds is None
                else min(self.latency_min_seconds, elapsed)
            )
            self.latency_max_seconds = (
                elapsed
                if self.latency_max_seconds is None
                else max(self.latency_max_seconds, elapsed)
            )
            self.latencies_seconds.append(elapsed)
            query_stats = self.per_query[query_name]
            query_stats["completed"] = int(query_stats["completed"]) + 1
            query_stats["latency_sum_seconds"] = (
                float(query_stats["latency_sum_seconds"]) + elapsed
            )
            query_stats["latency_max_seconds"] = max(
                float(query_stats["latency_max_seconds"]), elapsed
            )

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "offered": self.offered,
                "enqueued": self.enqueued,
                "rejected": self.rejected,
                "completed": self.completed,
                "failed": self.failed,
                "retries": self.retries,
                "latency_sum_seconds": self.latency_sum_seconds,
                "latency_min_seconds": self.latency_min_seconds,
                "latency_max_seconds": self.latency_max_seconds,
                "latencies_seconds": list(self.latencies_seconds),
                "errors": list(self.errors),
                "per_query": {
                    name: dict(values) for name, values in self.per_query.items()
                },
            }


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Percentil linear inclusivo, adequado aos relatórios compactos da demo."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def read_only_sql(select_sql: str) -> str:
    """Envelopa um SELECT em transação READ ONLY e rejeita comandos mutáveis."""
    statement = select_sql.strip().rstrip(";")
    if not statement.upper().startswith(("SELECT ", "WITH ")):
        raise ValueError("consulta deve iniciar com SELECT ou WITH")
    forbidden = FORBIDDEN_SQL.search(statement)
    if forbidden:
        raise ValueError(
            f"comando proibido no perfil read-only: {forbidden.group(0).upper()}"
        )
    return f"""
USE sakila;
START TRANSACTION READ ONLY;
{statement};
COMMIT;
""".strip()


def capacity_preflight(workers: int) -> dict[str, int | str]:
    identity = base.preflight()
    result = base.run_sql(
        "USE sakila; SELECT @@global.max_connections, COUNT(*) "
        "FROM information_schema.processlist WHERE USER = 'admin';"
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"preflight de capacidade falhou: {base.sanitize_error(result.stderr)}"
        )
    fields = result.stdout.strip().split("\t")
    if len(fields) != 2:
        raise RuntimeError("preflight de capacidade retornou formato inesperado")
    max_connections, current_connections = (int(value) for value in fields)
    if workers + current_connections + 5 > max_connections:
        raise RuntimeError(
            f"workers={workers} excede a margem: max_connections={max_connections}, "
            f"conexoes_admin={current_connections}, reserva=5"
        )
    return {
        **identity,
        "max_connections": max_connections,
        "admin_connections_before": current_connections,
    }


def worker(
    jobs: queue.Queue[Job | None],
    queries: Sequence[QuerySpec],
    arguments: argparse.Namespace,
    stop_event: threading.Event,
    stats: ReadStats,
) -> None:
    session: base.PersistentMysql | None = None
    try:
        while True:
            job = jobs.get()
            if job is None:
                jobs.task_done()
                break
            if stop_event.is_set():
                stats.add_rejected()
                jobs.task_done()
                continue
            query = queries[job.sequence % len(queries)]
            started = time.monotonic()
            try:
                if session is None:
                    session = base.PersistentMysql()
                session.execute(
                    read_only_sql(query.build(job.sequence)),
                    arguments.query_timeout_seconds,
                )
            except (
                OSError,
                RuntimeError,
                ValueError,
                base.PersistentQueryTimeout,
            ) as error:
                if session is not None:
                    session.close()
                    session = None
                if not stop_event.is_set() and job.attempts < arguments.max_retries:
                    stats.add_retry(error)
                    try:
                        jobs.put_nowait(Job(job.sequence, job.attempts + 1))
                    except queue.Full:
                        stats.add_failure(error)
                else:
                    stats.add_failure(error)
            else:
                stats.add_success(query.name, time.monotonic() - started)
            finally:
                jobs.task_done()
    finally:
        if session is not None:
            session.close()


def parse_arguments(
    description: str,
    confirmation: str,
    default_duration: int,
    default_tps: float,
    default_workers: int,
) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-target", choices=[base.SCHEMA])
    parser.add_argument("--confirm-read-only", choices=[confirmation])
    parser.add_argument("--target-tps", type=float, default=default_tps)
    parser.add_argument("--duration-seconds", type=int, default=default_duration)
    parser.add_argument("--total-queries", type=int, default=None)
    parser.add_argument("--workers", type=int, default=default_workers)
    parser.add_argument("--queue-size", type=int, default=10_000)
    parser.add_argument("--query-timeout-seconds", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--progress-interval-seconds", type=int, default=10)
    parser.add_argument("--drain-queue", action="store_true")
    arguments = parser.parse_args()
    if arguments.target_tps <= 0 or arguments.duration_seconds < 1:
        parser.error("target-tps e duration-seconds devem ser positivos")
    if arguments.workers < 1 or arguments.workers > 1_000:
        parser.error("workers deve ficar entre 1 e 1000")
    if arguments.queue_size < 10 or arguments.query_timeout_seconds < 1:
        parser.error("queue-size deve ser >=10 e query-timeout-seconds >=1")
    if arguments.max_retries < 0 or arguments.max_retries > 5:
        parser.error("max-retries deve ficar entre 0 e 5")
    if arguments.progress_interval_seconds < 1:
        parser.error("progress-interval-seconds deve ser >=1")
    if arguments.total_queries is not None and arguments.total_queries < 1:
        parser.error("total-queries deve ser >=1")
    return arguments


def write_report(
    profile: str,
    arguments: argparse.Namespace,
    identity: dict[str, int | str],
    stats: ReadStats,
    producer_elapsed: float,
    elapsed: float,
) -> tuple[Path, Path]:
    snapshot = stats.snapshot()
    completed = int(snapshot["completed"])
    latencies = [float(value) for value in snapshot["latencies_seconds"]]
    per_query = dict(snapshot["per_query"])
    for values in per_query.values():
        count = int(values["completed"])
        values["latency_avg_seconds"] = (
            round(float(values["latency_sum_seconds"]) / count, 6) if count else None
        )
        values["latency_max_seconds"] = round(float(values["latency_max_seconds"]), 6)
        values.pop("latency_sum_seconds")
    results = {
        **snapshot,
        "offered_tps": round(
            int(snapshot["offered"]) / max(producer_elapsed, 0.000001), 3
        ),
        "completed_tps": round(completed / max(elapsed, 0.000001), 3),
        "producer_elapsed_seconds": round(producer_elapsed, 3),
        "total_elapsed_seconds": round(elapsed, 3),
        "latency_avg_seconds": (
            round(float(snapshot["latency_sum_seconds"]) / completed, 6)
            if completed
            else None
        ),
        "latency_max_seconds": (
            round(float(snapshot["latency_max_seconds"]), 6)
            if snapshot["latency_max_seconds"] is not None
            else None
        ),
        "latency_p50_seconds": (
            round(value, 6)
            if (value := percentile(latencies, 0.50)) is not None
            else None
        ),
        "latency_p95_seconds": (
            round(value, 6)
            if (value := percentile(latencies, 0.95)) is not None
            else None
        ),
        "latency_p99_seconds": (
            round(value, 6)
            if (value := percentile(latencies, 0.99)) is not None
            else None
        ),
        "queue_backlog": max(
            0,
            int(snapshot["offered"])
            - completed
            - int(snapshot["failed"])
            - int(snapshot["rejected"]),
        ),
        "per_query": per_query,
    }
    results.pop("latency_sum_seconds")
    results.pop("latencies_seconds")
    report = {
        "started_at": stats.started_at,
        "finished_at": base.timestamp(),
        "profile": profile,
        "target": {
            "host": base.HOST,
            "port": base.PORT,
            "schema": base.SCHEMA,
            **identity,
        },
        "configuration": vars(arguments),
        "results": results,
        "semantics": "open-loop read-only; offered and completed rates are reported separately",
    }
    report_dir = Path(__file__).resolve().parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    json_path = report_dir / f"sakila-read-{profile}-{stamp}.json"
    md_path = report_dir / f"sakila-read-{profile}-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(
        "\n".join(
            [
                f"# Resultado de leitura Sakila: {profile}",
                "",
                f"- Oferecidas: `{snapshot['offered']}`",
                f"- Concluidas: `{completed}`",
                f"- Rejeitadas: `{snapshot['rejected']}`",
                f"- Falhas finais: `{snapshot['failed']}`",
                f"- Retries: `{snapshot['retries']}`",
                f"- Offered TPS: `{results['offered_tps']}`",
                f"- Completed TPS: `{results['completed_tps']}`",
                f"- Janela de producao: `{results['producer_elapsed_seconds']}` segundos",
                f"- Tempo total: `{results['total_elapsed_seconds']}` segundos",
                f"- Latencia media: `{results['latency_avg_seconds']}` segundos",
                f"- Latencia p50: `{results['latency_p50_seconds']}` segundos",
                f"- Latencia p95: `{results['latency_p95_seconds']}` segundos",
                f"- Latencia p99: `{results['latency_p99_seconds']}` segundos",
                f"- Latencia maxima: `{results['latency_max_seconds']}` segundos",
                f"- Backlog final: `{results['queue_backlog']}`",
                "",
                "Este workload executa somente SELECT em transacoes READ ONLY no schema sakila.",
                "",
                f"Evidencia JSON: `{json_path.name}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, md_path


def run_profile(
    *,
    profile: str,
    description: str,
    confirmation: str,
    queries: Sequence[QuerySpec],
    default_duration: int,
    default_tps: float,
    default_workers: int,
) -> int:
    arguments = parse_arguments(
        description, confirmation, default_duration, default_tps, default_workers
    )
    if not arguments.execute:
        print("dry-run: nenhum acesso ao banco foi realizado")
        print(
            f"perfil={profile} read-only target_tps={arguments.target_tps:g} "
            f"duration={arguments.duration_seconds}s workers={arguments.workers}"
        )
        print(
            f"para executar: --execute --confirm-target sakila "
            f"--confirm-read-only {confirmation}"
        )
        return 0
    if (
        arguments.confirm_target != base.SCHEMA
        or arguments.confirm_read_only != confirmation
    ):
        print(
            f"erro: --execute exige --confirm-target sakila e "
            f"--confirm-read-only {confirmation}"
        )
        return 2

    try:
        for query in queries:
            read_only_sql(query.build(0))
        identity = capacity_preflight(arguments.workers)
    except (RuntimeError, ValueError) as error:
        print(f"erro: {error}")
        return 2

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    stats = ReadStats(base.timestamp(), [query.name for query in queries])
    jobs: queue.Queue[Job | None] = queue.Queue(maxsize=arguments.queue_size)
    threads = [
        threading.Thread(
            target=worker,
            args=(jobs, queries, arguments, stop_event, stats),
            name=f"sakila-read-{profile}-{worker_id}",
        )
        for worker_id in range(1, arguments.workers + 1)
    ]
    for thread in threads:
        thread.start()

    started = time.monotonic()
    deadline = started + arguments.duration_seconds
    next_release = started
    next_progress = started + arguments.progress_interval_seconds
    sequence = 0
    period = 1.0 / arguments.target_tps
    while time.monotonic() < deadline and not stop_event.is_set():
        if arguments.total_queries is not None and sequence >= arguments.total_queries:
            break
        now = time.monotonic()
        if now < next_release:
            time.sleep(min(next_release - now, 0.001))
            continue
        stats.add_offered()
        try:
            jobs.put_nowait(Job(sequence))
        except queue.Full:
            stats.add_rejected()
        else:
            stats.add_enqueued()
            sequence += 1
        next_release += period
        if now >= next_progress:
            snapshot = stats.snapshot()
            print(
                f"elapsed={now - started:.1f}s offered={snapshot['offered']} "
                f"completed={snapshot['completed']} rejected={snapshot['rejected']}",
                flush=True,
            )
            next_progress += arguments.progress_interval_seconds

    producer_elapsed = time.monotonic() - started
    if arguments.drain_queue:
        jobs.join()
    else:
        stop_event.set()
    for _ in threads:
        jobs.put(None)
    jobs.join()
    for thread in threads:
        thread.join()

    elapsed = time.monotonic() - started
    json_path, md_path = write_report(
        profile, arguments, identity, stats, producer_elapsed, elapsed
    )
    snapshot = stats.snapshot()
    print(
        f"offered={snapshot['offered']} completed={snapshot['completed']} "
        f"rejected={snapshot['rejected']} failed={snapshot['failed']} retries={snapshot['retries']}"
    )
    print(f"report={md_path}")
    print(f"json={json_path}")
    return 0 if not snapshot["failed"] else 1
