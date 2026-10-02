#!/usr/bin/env python3
"""Teste de carga pesada, controlado e balanceado para o schema Sakila."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DBA_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(DBA_DIR / "scripts"))

import sakila_realistic_workload as base

DEFAULT_DURATION_SECONDS = 600
DEFAULT_WORKERS = 64
DEFAULT_RAMP_UP_SECONDS = 60
DEFAULT_TARGET_TPS = 1_000.0
DEFAULT_MAX_TRANSACTIONS = 1_000_000
DEFAULT_PROGRESS_INTERVAL_SECONDS = 10
# O HeatWave de destino tem 2.000 max_connections. O preflight ainda reserva
# cinco conexoes administrativas antes de aceitar uma carga de alta
# concorrencia; o teste deve escolher explicitamente quantos workers usar.
MAX_WORKERS = 1_000
CONFIRMATION = "HEAVY_LOAD_SAKILA"


@dataclass
class HeavyStats:
    started_at: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    attempts: int = 0
    succeeded: int = 0
    failed: int = 0
    operations: dict[str, int] = field(
        default_factory=lambda: {"read": 0, "insert": 0, "update": 0, "delete": 0}
    )
    cycle_latencies: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cleanup_failures: list[str] = field(default_factory=list)

    def reserve_cycle(self, maximum: int) -> bool:
        return self.reserve(maximum, 4)

    def reserve(self, maximum: int, amount: int = 1) -> bool:
        with self.lock:
            if self.attempts + amount > maximum:
                return False
            self.attempts += amount
            return True

    def unreserve_cycle(self) -> None:
        with self.lock:
            self.attempts -= 4

    def unreserve(self, amount: int = 1) -> None:
        with self.lock:
            self.attempts -= amount

    def record_cycle(self, elapsed: float) -> None:
        with self.lock:
            self.succeeded += 4
            for operation in self.operations:
                self.operations[operation] += 1
            self.cycle_latencies.append(elapsed)

    def record_operation(self, operation: str) -> None:
        with self.lock:
            self.succeeded += 1
            self.operations[operation] += 1

    def record_batch_latency(self, elapsed: float) -> None:
        with self.lock:
            self.cycle_latencies.append(elapsed)

    def record_failure(self, error: BaseException) -> None:
        with self.lock:
            self.failed += 1
            if len(self.errors) < 50:
                self.errors.append(base.sanitize_error(str(error)))

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "attempts": self.attempts,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "operations": dict(self.operations),
            }


@dataclass
class WorkerState:
    worker_id: int
    pending: list[base.SyntheticRecord] = field(default_factory=list)
    # O open-loop usa este mapa para devolver ao pool apenas o lease cujo
    # registro foi efetivamente removido. Mantido como object para não acoplar
    # o executor pesado ao tipo InventoryLease do gerador.
    leases: dict[int, object] = field(default_factory=dict)
    # Leases cujo INSERT pode ter sido confirmado antes de a conexao perder
    # a resposta. O open-loop recupera esses registros por inventory_id.
    orphan_leases: list[object] = field(default_factory=list)
    inflight: list[base.SyntheticRecord] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percent * len(ordered)) - 1))
    return ordered[index]


def capacity_preflight(workers: int) -> dict[str, int | str]:
    identity = base.preflight()
    result = base.run_sql(
        "SELECT @@global.max_connections, "
        "SUM(USER = 'admin') FROM information_schema.processlist;"
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"preflight de capacidade falhou: {base.sanitize_error(result.stderr)}"
        )
    fields = result.stdout.strip().split("\t")
    if len(fields) != 2:
        raise RuntimeError("preflight de capacidade retornou formato inesperado")
    max_connections, current_admin_connections = (int(value) for value in fields)
    connection_headroom = max_connections - current_admin_connections
    if workers + 5 > connection_headroom:
        raise RuntimeError(
            f"workers={workers} excede a margem segura: max_connections={max_connections}, "
            f"conexoes_admin_atuais={current_admin_connections}, reserva=5"
        )
    return {
        **identity,
        "max_connections": max_connections,
        "admin_connections_before": current_admin_connections,
    }


def cleanup_worker(state: WorkerState, stats: HeavyStats) -> None:
    with state.lock:
        records = list(state.pending)
        state.pending.clear()
    if not records:
        return
    session: base.PersistentMysql | None = None
    try:
        session = base.PersistentMysql()
        for start in range(0, len(records), 200):
            session.execute(base.delete_sql(records[start : start + 200]))
    except (OSError, RuntimeError, base.PersistentQueryTimeout) as error:
        with stats.lock:
            stats.cleanup_failures.append(base.sanitize_error(str(error)))
    finally:
        if session is not None:
            session.close()


def bulk_update_sql(records: list[base.SyntheticRecord]) -> str:
    rental_ids = ",".join(str(record.rental_id) for record in records)
    payment_ids = ",".join(str(record.payment_id) for record in records)
    return f"""
USE sakila;
START TRANSACTION;
UPDATE sakila.rental
   SET return_date = COALESCE(return_date, NOW())
 WHERE rental_id IN ({rental_ids});
UPDATE sakila.payment
   SET amount = LEAST(amount + 0.01, 999.99)
 WHERE payment_id IN ({payment_ids});
COMMIT;
SELECT 1;
""".strip()


def bulk_delete_sql(records: list[base.SyntheticRecord]) -> str:
    payment_ids = ",".join(str(record.payment_id) for record in records)
    rental_ids = ",".join(str(record.rental_id) for record in records)
    return f"""
USE sakila;
START TRANSACTION;
DELETE FROM sakila.payment WHERE payment_id IN ({payment_ids});
DELETE FROM sakila.rental WHERE rental_id IN ({rental_ids});
COMMIT;
SELECT 1;
""".strip()


def heavy_worker(
    state: WorkerState,
    arguments: argparse.Namespace,
    deadline: float,
    stop_event: threading.Event,
    stats: HeavyStats,
) -> None:
    session: base.PersistentMysql | None = None
    cycle_interval = 0.0
    if arguments.target_tps > 0:
        cycle_interval = 4.0 * arguments.workers / arguments.target_tps
    try:
        session = base.PersistentMysql()
        while not stop_event.is_set() and time.monotonic() < deadline:
            if not stats.reserve_cycle(arguments.max_transactions):
                stop_event.set()
                break
            cycle_started = time.monotonic()
            try:
                rows = session.execute_collect(
                    base.balanced_active_sql(state.worker_id, arguments.workers),
                    arguments.transaction_timeout_seconds,
                )
                if rows and rows[-1] == "NO_INVENTORY":
                    stats.unreserve_cycle()
                    stop_event.wait(0.05)
                    continue
                if not rows or "\t" not in rows[-1]:
                    raise RuntimeError("ciclo pesado nao retornou as chaves sinteticas")
                rental_id, payment_id = (int(value) for value in rows[-1].split("\t"))
                record = base.SyntheticRecord(rental_id, payment_id, returned=True)
                with state.lock:
                    state.pending.append(record)
                session.execute(
                    base.delete_sql([record]), arguments.transaction_timeout_seconds
                )
                with state.lock:
                    state.pending.remove(record)
                elapsed = time.monotonic() - cycle_started
                stats.record_cycle(elapsed)
                if cycle_interval > elapsed:
                    stop_event.wait(cycle_interval - elapsed)
            except (
                OSError,
                RuntimeError,
                ValueError,
                base.PersistentQueryTimeout,
            ) as error:
                stats.record_failure(error)
                stop_event.set()
    except OSError as error:
        stats.record_failure(error)
        stop_event.set()
    finally:
        if session is not None:
            session.close()
        cleanup_worker(state, stats)


def heavy_worker_insert_read_profile(
    state: WorkerState,
    arguments: argparse.Namespace,
    deadline: float,
    stop_event: threading.Event,
    stats: HeavyStats,
) -> None:
    """Favor INSERT/READ while retaining periodic UPDATE/DELETE maintenance."""
    session: base.PersistentMysql | None = None
    batch_operations = (
        arguments.insert_burst * (1 + arguments.reads_per_insert)
        + arguments.updates_per_batch
        + arguments.deletes_per_batch
    )
    batch_interval = (
        (arguments.workers * batch_operations) / arguments.target_tps
        if arguments.target_tps > 0
        else 0.0
    )
    try:
        session = base.PersistentMysql()
        while not stop_event.is_set() and time.monotonic() < deadline:
            batch_started = time.monotonic()
            inserted: list[base.SyntheticRecord] = []
            for _ in range(arguments.insert_burst):
                if not stats.reserve(arguments.max_transactions):
                    stop_event.set()
                    break
                try:
                    rows = session.execute_collect(
                        base.insert_sql(state.worker_id, arguments.workers),
                        arguments.transaction_timeout_seconds,
                    )
                    if rows and rows[-1] == "NO_INVENTORY":
                        stats.unreserve()
                        continue
                    if not rows or "\t" not in rows[-1]:
                        raise RuntimeError(
                            "INSERT pesado nao retornou as chaves sinteticas"
                        )
                    rental_id, payment_id = (
                        int(value) for value in rows[-1].split("\t")
                    )
                    record = base.SyntheticRecord(rental_id, payment_id)
                    with state.lock:
                        state.pending.append(record)
                    inserted.append(record)
                    stats.record_operation("insert")
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    base.PersistentQueryTimeout,
                ) as error:
                    stats.record_failure(error)
                    stop_event.set()
                    break
            if stop_event.is_set():
                break

            for record in inserted:
                for _ in range(arguments.reads_per_insert):
                    if not stats.reserve(arguments.max_transactions):
                        stop_event.set()
                        break
                    try:
                        session.execute(
                            base.read_sql(record), arguments.transaction_timeout_seconds
                        )
                        stats.record_operation("read")
                    except (
                        OSError,
                        RuntimeError,
                        ValueError,
                        base.PersistentQueryTimeout,
                    ) as error:
                        stats.record_failure(error)
                        stop_event.set()
                        break
                if stop_event.is_set():
                    break
            if stop_event.is_set():
                break

            for _ in range(arguments.updates_per_batch):
                with state.lock:
                    candidates = [item for item in state.pending if not item.returned][
                        : arguments.maintenance_batch_size
                    ]
                if not candidates:
                    break
                if not stats.reserve(arguments.max_transactions):
                    stop_event.set()
                    break
                try:
                    session.execute(
                        bulk_update_sql(candidates),
                        arguments.transaction_timeout_seconds,
                    )
                    for candidate in candidates:
                        candidate.returned = True
                    stats.record_operation("update")
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    base.PersistentQueryTimeout,
                ) as error:
                    stats.record_failure(error)
                    stop_event.set()
                    break
            if stop_event.is_set():
                break

            for _ in range(arguments.deletes_per_batch):
                with state.lock:
                    candidates = [item for item in state.pending if item.returned][
                        : arguments.maintenance_batch_size
                    ]
                if not candidates:
                    break
                if not stats.reserve(arguments.max_transactions):
                    stop_event.set()
                    break
                try:
                    session.execute(
                        bulk_delete_sql(candidates),
                        arguments.transaction_timeout_seconds,
                    )
                    with state.lock:
                        for candidate in candidates:
                            if candidate in state.pending:
                                state.pending.remove(candidate)
                    stats.record_operation("delete")
                except (
                    OSError,
                    RuntimeError,
                    ValueError,
                    base.PersistentQueryTimeout,
                ) as error:
                    stats.record_failure(error)
                    stop_event.set()
                    break
            stats.record_batch_latency(time.monotonic() - batch_started)
            if batch_interval > time.monotonic() - batch_started:
                stop_event.wait(batch_interval - (time.monotonic() - batch_started))
    except OSError as error:
        stats.record_failure(error)
        stop_event.set()
    finally:
        if session is not None:
            session.close()
        cleanup_worker(state, stats)


def write_report(
    arguments: argparse.Namespace,
    identity: dict[str, int | str],
    stats: HeavyStats,
    workload_started_monotonic: float,
) -> tuple[Path, Path]:
    finished_at = base.timestamp()
    elapsed = max(0.000001, time.monotonic() - workload_started_monotonic)
    snapshot = stats.snapshot()
    with stats.lock:
        latencies = list(stats.cycle_latencies)
        errors = list(stats.errors)
        cleanup_failures = list(stats.cleanup_failures)
    average = sum(latencies) / len(latencies) if latencies else None
    report = {
        "started_at": stats.started_at,
        "finished_at": finished_at,
        "target": {
            "host": base.HOST,
            "port": base.PORT,
            "schema": base.SCHEMA,
            **identity,
        },
        "configuration": vars(arguments),
        "results": {
            **snapshot,
            "elapsed_seconds": round(elapsed, 3),
            "throughput_tps": round(int(snapshot["succeeded"]) / elapsed, 3),
            "cycle_latency_seconds_average": None
            if average is None
            else round(average, 6),
            "cycle_latency_seconds_p95": (
                None
                if percentile(latencies, 0.95) is None
                else round(float(percentile(latencies, 0.95)), 6)
            ),
            "cycle_latency_seconds_max": None
            if not latencies
            else round(max(latencies), 6),
            "errors": errors,
            "cleanup_failures": cleanup_failures,
            "distribution_percentages": {
                key: round(value / max(1, int(snapshot["succeeded"])) * 100, 3)
                for key, value in dict(snapshot["operations"]).items()
            },
            "cycle_balanced": arguments.mode == "balanced-cycle",
        },
        "lifecycle": (
            "INSERT/READ em rajada com UPDATE/DELETE de manutencao"
            if arguments.mode == "insert-read-heavy"
            else "INSERT -> READ -> UPDATE -> DELETE; quatro transacoes por ciclo"
        ),
        "persistent_side_effect": "AUTO_INCREMENT de rental e payment avanca; linhas sinteticas sao removidas",
    }
    configured_report_dir = os.environ.get("SAKILA_REPORT_DIR")
    reports_dir = (
        Path(configured_report_dir)
        if configured_report_dir
        else Path(__file__).resolve().parent / "reports"
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    json_path = reports_dir / f"sakila-heavy-load-{stamp}.json"
    md_path = reports_dir / f"sakila-heavy-load-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(
        "\n".join(
            [
                "# Resultado do teste de carga pesada Sakila",
                "",
                f"- Inicio: `{stats.started_at}`",
                f"- Fim: `{finished_at}`",
                f"- Transacoes bem-sucedidas: `{snapshot['succeeded']}`",
                f"- Falhas: `{snapshot['failed']}`",
                f"- Distribuicao: `{snapshot['operations']}`",
                f"- Distribuicao percentual: `{report['results']['distribution_percentages']}`",
                f"- Throughput: `{report['results']['throughput_tps']}` TPS",
                f"- Latencia media por ciclo: `{report['results']['cycle_latency_seconds_average']}` segundos",
                f"- Latencia p95 por ciclo: `{report['results']['cycle_latency_seconds_p95']}` segundos",
                f"- Cleanup failures: `{cleanup_failures}`",
                "",
                (
                    "Perfil pesado: quatro INSERT, quatro READ, um UPDATE e um DELETE por lote."
                    if arguments.mode == "insert-read-heavy"
                    else "Cada ciclo executa quatro transacoes: INSERT, READ, UPDATE e DELETE."
                ),
                "Somente linhas sinteticas sao alteradas; AUTO_INCREMENT nao e revertido.",
                "",
                f"Evidencia JSON: `{json_path.name}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, md_path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-target", choices=[base.SCHEMA])
    parser.add_argument("--confirm-heavy-load", choices=[CONFIRMATION])
    parser.add_argument(
        "--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--ramp-up-seconds", type=int, default=DEFAULT_RAMP_UP_SECONDS)
    parser.add_argument("--target-tps", type=float, default=DEFAULT_TARGET_TPS)
    parser.add_argument(
        "--max-transactions", type=int, default=DEFAULT_MAX_TRANSACTIONS
    )
    parser.add_argument("--transaction-timeout-seconds", type=int, default=60)
    parser.add_argument(
        "--progress-interval-seconds",
        type=int,
        default=DEFAULT_PROGRESS_INTERVAL_SECONDS,
    )
    parser.add_argument(
        "--mode",
        choices=["insert-read-heavy", "balanced-cycle"],
        default="insert-read-heavy",
    )
    parser.add_argument("--insert-burst", type=int, default=4)
    parser.add_argument("--reads-per-insert", type=int, default=1)
    parser.add_argument("--updates-per-batch", type=int, default=1)
    parser.add_argument("--deletes-per-batch", type=int, default=1)
    parser.add_argument("--maintenance-batch-size", type=int, default=4)
    arguments = parser.parse_args()
    if not 10 <= arguments.duration_seconds <= 14_400:
        parser.error("duration-seconds deve ficar entre 10 e 14400")
    if not 1 <= arguments.workers <= MAX_WORKERS:
        parser.error(f"workers deve ficar entre 1 e {MAX_WORKERS}")
    if not 0 <= arguments.ramp_up_seconds <= 1_800:
        parser.error("ramp-up-seconds deve ficar entre 0 e 1800")
    if arguments.target_tps < 0:
        parser.error("target-tps deve ser zero (sem limite) ou positivo")
    if not 4 <= arguments.max_transactions <= 2_000_000:
        parser.error("max-transactions deve ficar entre 4 e 2000000")
    if not 10 <= arguments.transaction_timeout_seconds <= 300:
        parser.error("transaction-timeout-seconds deve ficar entre 10 e 300")
    if not 1 <= arguments.progress_interval_seconds <= 60:
        parser.error("progress-interval-seconds deve ficar entre 1 e 60")
    if not 1 <= arguments.insert_burst <= 32:
        parser.error("insert-burst deve ficar entre 1 e 32")
    if not 1 <= arguments.reads_per_insert <= 16:
        parser.error("reads-per-insert deve ficar entre 1 e 16")
    if (
        not 1 <= arguments.updates_per_batch <= 32
        or not 1 <= arguments.deletes_per_batch <= 32
    ):
        parser.error("updates-per-batch e deletes-per-batch devem ficar entre 1 e 32")
    if not 1 <= arguments.maintenance_batch_size <= 100:
        parser.error("maintenance-batch-size deve ficar entre 1 e 100")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    if not arguments.execute:
        print("dry-run: nenhum acesso ao banco foi realizado")
        print(
            f"alvo: {base.HOST}:{base.PORT}/{base.SCHEMA}; duracao sustentada: "
            f"{arguments.duration_seconds}s; workers: {arguments.workers}; "
            f"ramp-up: {arguments.ramp_up_seconds}s"
        )
        print(
            f"target_tps: {arguments.target_tps or 'unlimited'}; "
            f"max_transactions: {arguments.max_transactions}; mode: {arguments.mode}"
        )
        print(
            f"insert_burst={arguments.insert_burst}; reads_per_insert={arguments.reads_per_insert}; "
            f"updates_per_batch={arguments.updates_per_batch}; deletes_per_batch={arguments.deletes_per_batch}"
        )
        print(
            "para executar: --execute --confirm-target sakila "
            f"--confirm-heavy-load {CONFIRMATION}"
        )
        return 0
    if (
        arguments.confirm_target != base.SCHEMA
        or arguments.confirm_heavy_load != CONFIRMATION
    ):
        print(
            "erro: --execute exige --confirm-target sakila e "
            f"--confirm-heavy-load {CONFIRMATION}"
        )
        return 2
    try:
        identity = capacity_preflight(arguments.workers)
    except RuntimeError as error:
        print(f"erro: {error}")
        return 2

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    stats = HeavyStats(started_at=base.timestamp())
    states = [WorkerState(worker_id) for worker_id in range(1, arguments.workers + 1)]
    threads: list[threading.Thread] = []
    worker_target = (
        heavy_worker
        if arguments.mode == "balanced-cycle"
        else heavy_worker_insert_read_profile
    )
    workload_started_monotonic = time.monotonic()
    deadline = (
        workload_started_monotonic
        + arguments.ramp_up_seconds
        + arguments.duration_seconds
    )
    ramp_interval = (
        arguments.ramp_up_seconds / arguments.workers if arguments.workers else 0
    )

    for state in states:
        thread = threading.Thread(
            target=worker_target,
            args=(state, arguments, deadline, stop_event, stats),
            name=f"sakila-heavy-{state.worker_id}",
        )
        thread.start()
        threads.append(thread)
        if ramp_interval and stop_event.wait(ramp_interval):
            break

    last_success = 0
    last_check = time.monotonic()
    while any(thread.is_alive() for thread in threads):
        stop_event.wait(arguments.progress_interval_seconds)
        now = time.monotonic()
        snapshot = stats.snapshot()
        interval = max(0.000001, now - last_check)
        current_tps = (int(snapshot["succeeded"]) - last_success) / interval
        print(
            f"elapsed={now - workload_started_monotonic:.1f}s "
            f"success={snapshot['succeeded']} failed={snapshot['failed']} "
            f"current_tps={current_tps:.2f}",
            flush=True,
        )
        last_success = int(snapshot["succeeded"])
        last_check = now

    for thread in threads:
        thread.join()
    json_path, md_path = write_report(
        arguments, identity, stats, workload_started_monotonic
    )
    snapshot = stats.snapshot()
    print(
        f"attempts={snapshot['attempts']} success={snapshot['succeeded']} "
        f"failed={snapshot['failed']} operations={snapshot['operations']}"
    )
    print(f"report={md_path}")
    print(f"json={json_path}")
    return 0 if snapshot["failed"] == 0 and not stats.cleanup_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
