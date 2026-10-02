#!/usr/bin/env python3
"""Gerador open-loop: oferece uma taxa fixa de transacoes ao Sakila."""

from __future__ import annotations

import argparse
import json
import queue
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import sakila_heavy_load as heavy

base = heavy.base

CONFIRMATION = "OPEN_LOOP_SAKILA"
DEFAULT_TARGET_TPS = 1_000.0
DEFAULT_DURATION_SECONDS = 600
DEFAULT_WORKERS = 64
DEFAULT_QUEUE_SIZE = 100_000


@dataclass(frozen=True)
class Job:
    sequence: int
    attempts: int = 0


@dataclass(frozen=True)
class InventoryLease:
    inventory_id: int
    customer_id: int
    staff_id: int
    baseline_rental_id: int


class NoInventoryAvailable(RuntimeError):
    pass


@dataclass
class OpenStats:
    started_at: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    offered_transactions: int = 0
    enqueued_transactions: int = 0
    rejected_transactions: int = 0
    completed_transactions: int = 0
    failed_batches: int = 0
    retry_attempts: int = 0
    operations: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    cleanup_failures: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.operations = {"read": 0, "insert": 0, "update": 0, "delete": 0}
        self.errors = []
        self.cleanup_failures = []

    def add_offered(self, transactions: int) -> None:
        with self.lock:
            self.offered_transactions += transactions

    def add_enqueued(self, transactions: int) -> None:
        with self.lock:
            self.enqueued_transactions += transactions

    def add_rejected(self, transactions: int) -> None:
        with self.lock:
            self.rejected_transactions += transactions

    def record_batch(self) -> None:
        with self.lock:
            self.completed_transactions += 10
            for operation, count in (
                ("insert", 4),
                ("read", 4),
                ("update", 1),
                ("delete", 1),
            ):
                self.operations[operation] += count

    def record_failure(self, error: BaseException) -> None:
        with self.lock:
            self.failed_batches += 1
            if len(self.errors) < 50:
                self.errors.append(base.sanitize_error(str(error)))

    def record_retry(self, error: BaseException) -> None:
        with self.lock:
            self.retry_attempts += 1
            if len(self.errors) < 50:
                self.errors.append(f"retry: {base.sanitize_error(str(error))}")

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "offered_transactions": self.offered_transactions,
                "enqueued_transactions": self.enqueued_transactions,
                "rejected_transactions": self.rejected_transactions,
                "completed_transactions": self.completed_transactions,
                "failed_batches": self.failed_batches,
                "retry_attempts": self.retry_attempts,
                "operations": dict(self.operations),
                "errors": list(self.errors),
                "cleanup_failures": list(self.cleanup_failures),
            }


def direct_insert_sql(leases: list[InventoryLease]) -> str:
    """Insere quatro leases em uma transacao e devolve as chaves por inventario.

    A identificacao por ``inventory_id`` evita depender de consecutividade do
    AUTO_INCREMENT quando varias conexoes fazem INSERT simultaneamente.
    """
    inventory_ids = ",".join(str(lease.inventory_id) for lease in leases)
    rental_values = ",\n".join(
        f"(NOW(), {lease.inventory_id}, {lease.customer_id}, NULL, {lease.staff_id})"
        for lease in leases
    )
    return f"""
USE sakila;
START TRANSACTION;
INSERT INTO sakila.rental
       (rental_date, inventory_id, customer_id, return_date, staff_id)
VALUES {rental_values};
INSERT INTO sakila.payment
       (customer_id, staff_id, rental_id, amount, payment_date)
SELECT r.customer_id, r.staff_id, r.rental_id,
       ROUND(1.00 + RAND() * 9.00, 2), NOW()
  FROM sakila.rental AS r
 WHERE r.inventory_id IN ({inventory_ids})
   AND r.return_date IS NULL;
COMMIT;
SELECT CONCAT(r.inventory_id, '\\t', r.rental_id, '\\t', p.payment_id)
  FROM sakila.rental AS r
  JOIN sakila.payment AS p ON p.rental_id = r.rental_id
 WHERE r.inventory_id IN ({inventory_ids})
   AND r.return_date IS NULL
 ORDER BY r.inventory_id;
""".strip()


def direct_read_sql(records: list[base.SyntheticRecord]) -> str:
    rental_ids = ",".join(str(record.rental_id) for record in records)
    return f"""
USE sakila;
SELECT r.rental_id, r.rental_date, r.return_date, p.amount, p.payment_date
  FROM sakila.rental AS r
  LEFT JOIN sakila.payment AS p ON p.rental_id = r.rental_id
 WHERE r.rental_id IN ({rental_ids});
""".strip()


def recover_orphan_leases(
    session: base.PersistentMysql,
    state: heavy.WorkerState,
    inventory_pool: queue.Queue[InventoryLease],
    timeout_seconds: int,
) -> None:
    """Localiza e remove INSERTs confirmados sem resposta do cliente."""
    with state.lock:
        leases = [
            item for item in state.orphan_leases if isinstance(item, InventoryLease)
        ]
    if not leases:
        return
    predicates = " OR ".join(
        f"(r.inventory_id = {lease.inventory_id} AND r.rental_id > {lease.baseline_rental_id})"
        for lease in leases
    )
    rows = session.execute_collect(
        f"""
USE sakila;
SELECT r.rental_id, p.payment_id
  FROM sakila.rental AS r
  JOIN sakila.payment AS p ON p.rental_id = r.rental_id
 WHERE {predicates};
""".strip(),
        timeout_seconds,
    )
    records = []
    for row in rows:
        fields = row.split("\t")
        if len(fields) == 2:
            records.append(base.SyntheticRecord(int(fields[0]), int(fields[1])))
    if records:
        session.execute(heavy.bulk_delete_sql(records), timeout_seconds)
    with state.lock:
        for lease in leases:
            if lease in state.orphan_leases:
                state.orphan_leases.remove(lease)
    for lease in leases:
        inventory_pool.put(lease)


def cleanup_inflight(
    state: heavy.WorkerState,
    inventory_pool: queue.Queue[InventoryLease],
    timeout_seconds: int,
    stats: OpenStats,
) -> None:
    """Remove o lote conhecido que falhou antes de ser repetido."""
    with state.lock:
        records = list(state.inflight)
    if not records:
        # A conexao anterior pode ter sido encerrada entre COMMIT e a
        # resposta. Mantemos as leases retidas para uma nova consulta, em vez
        # de devolve-las ao pool e permitir outro INSERT no mesmo inventario.
        return
    session: base.PersistentMysql | None = None
    try:
        session = base.PersistentMysql()
        session.execute(heavy.bulk_delete_sql(records), timeout_seconds)
    except (OSError, RuntimeError, base.PersistentQueryTimeout) as error:
        with stats.lock:
            stats.cleanup_failures.append(base.sanitize_error(str(error)))
        return
    finally:
        if session is not None:
            session.close()
    released: list[InventoryLease] = []
    with state.lock:
        for record in records:
            if record in state.pending:
                state.pending.remove(record)
            lease = state.leases.pop(record.rental_id, None)
            if isinstance(lease, InventoryLease):
                released.append(lease)
            if record in state.inflight:
                state.inflight.remove(record)
    for lease in released:
        inventory_pool.put(lease)


def run_batch(
    session: base.PersistentMysql,
    state: heavy.WorkerState,
    arguments: argparse.Namespace,
    stats: OpenStats,
    inventory_pool: queue.Queue[InventoryLease],
) -> None:
    inserted: list[base.SyntheticRecord] = []
    leases: list[InventoryLease] = []
    for _ in range(4):
        try:
            leases.append(inventory_pool.get_nowait())
        except queue.Empty as error:
            for lease in leases:
                inventory_pool.put(lease)
            raise NoInventoryAvailable("pool global de inventario esgotado") from error
    try:
        rows = session.execute_collect(
            direct_insert_sql(leases), arguments.transaction_timeout_seconds
        )
    except (OSError, RuntimeError, ValueError, base.PersistentQueryTimeout):
        # O COMMIT pode ter ocorrido antes de o marcador chegar ao cliente.
        # A proxima sessao consulta essas leases e remove somente o aluguel
        # aberto associado ao inventario reservado.
        with state.lock:
            state.orphan_leases.extend(leases)
        raise
    parsed: dict[int, base.SyntheticRecord] = {}
    for row in rows:
        fields = row.split("\t")
        if len(fields) == 3:
            inventory_id, rental_id, payment_id = (int(value) for value in fields)
            parsed[inventory_id] = base.SyntheticRecord(rental_id, payment_id)
    if len(parsed) != len(leases):
        missing = [lease for lease in leases if lease.inventory_id not in parsed]
        with state.lock:
            state.orphan_leases.extend(missing)
        raise NoInventoryAvailable("lote open-loop nao retornou as quatro chaves")
    for lease in leases:
        record = parsed[lease.inventory_id]
        with state.lock:
            state.pending.append(record)
            state.leases[record.rental_id] = lease
        inserted.append(record)
    with state.lock:
        state.inflight = list(inserted)

    session.execute(direct_read_sql(inserted), arguments.transaction_timeout_seconds)

    candidates = inserted
    session.execute(
        heavy.bulk_update_sql(candidates), arguments.transaction_timeout_seconds
    )
    for candidate in candidates:
        candidate.returned = True

    session.execute(
        heavy.bulk_delete_sql(candidates), arguments.transaction_timeout_seconds
    )
    released_leases: list[InventoryLease] = []
    with state.lock:
        for candidate in candidates:
            if candidate in state.pending:
                state.pending.remove(candidate)
            lease = state.leases.pop(candidate.rental_id, None)
            if isinstance(lease, InventoryLease):
                released_leases.append(lease)
        state.inflight.clear()
    for lease in released_leases:
        inventory_pool.put(lease)
    stats.record_batch()


def worker(
    state: heavy.WorkerState,
    jobs: queue.Queue[Job | None],
    arguments: argparse.Namespace,
    stop_event: threading.Event,
    stats: OpenStats,
    inventory_pool: queue.Queue[InventoryLease],
) -> None:
    session: base.PersistentMysql | None = None
    try:
        while True:
            job = jobs.get()
            if job is None:
                jobs.task_done()
                break
            if stop_event.is_set():
                stats.add_rejected(10)
                jobs.task_done()
                continue
            try:
                if session is None:
                    session = base.PersistentMysql()
                recover_orphan_leases(
                    session,
                    state,
                    inventory_pool,
                    arguments.transaction_timeout_seconds,
                )
                run_batch(session, state, arguments, stats, inventory_pool)
            except NoInventoryAvailable:
                if job.attempts < arguments.max_retries:
                    stats.record_retry(
                        NoInventoryAvailable("pool de inventario indisponivel")
                    )
                    try:
                        jobs.put_nowait(Job(job.sequence, job.attempts + 1))
                    except queue.Full:
                        stats.record_failure(
                            NoInventoryAvailable("fila cheia ao repetir")
                        )
                else:
                    stats.record_failure(
                        NoInventoryAvailable("pool de inventario esgotado")
                    )
                with state.lock:
                    has_orphans = bool(state.orphan_leases)
                if has_orphans and session is not None:
                    session.close()
                    session = None
            except (
                OSError,
                RuntimeError,
                ValueError,
                base.PersistentQueryTimeout,
            ) as error:
                if job.attempts < arguments.max_retries:
                    stats.record_retry(error)
                    try:
                        jobs.put_nowait(Job(job.sequence, job.attempts + 1))
                    except queue.Full:
                        stats.record_failure(error)
                else:
                    stats.record_failure(error)
                if session is not None:
                    session.close()
                    session = None
                cleanup_inflight(
                    state, inventory_pool, arguments.transaction_timeout_seconds, stats
                )
            finally:
                jobs.task_done()
    except OSError as error:
        stats.record_failure(error)
    finally:
        with state.lock:
            has_orphans = bool(state.orphan_leases)
        if has_orphans:
            if session is None:
                try:
                    session = base.PersistentMysql()
                except OSError as error:
                    with stats.lock:
                        stats.cleanup_failures.append(base.sanitize_error(str(error)))
            if session is not None:
                for _ in range(3):
                    try:
                        recover_orphan_leases(
                            session,
                            state,
                            inventory_pool,
                            arguments.transaction_timeout_seconds,
                        )
                    except (
                        OSError,
                        RuntimeError,
                        base.PersistentQueryTimeout,
                    ) as error:
                        with stats.lock:
                            stats.cleanup_failures.append(
                                base.sanitize_error(str(error))
                            )
                        break
                    with state.lock:
                        if not state.orphan_leases:
                            break
                    time.sleep(1)
        if session is not None:
            with state.lock:
                pending = list(state.pending)
                state.pending.clear()
            try:
                for start in range(0, len(pending), 200):
                    session.execute(
                        heavy.bulk_delete_sql(pending[start : start + 200]),
                        arguments.transaction_timeout_seconds,
                    )
            except (OSError, RuntimeError, base.PersistentQueryTimeout) as error:
                with stats.lock:
                    stats.cleanup_failures.append(base.sanitize_error(str(error)))
            session.close()


def write_report(
    arguments: argparse.Namespace,
    identity: dict[str, int | str],
    stats: OpenStats,
    elapsed: float,
) -> tuple[Path, Path]:
    snapshot = stats.snapshot()
    completed = int(snapshot["completed_transactions"])
    report = {
        "started_at": stats.started_at,
        "finished_at": base.timestamp(),
        "target": {
            "host": base.HOST,
            "port": base.PORT,
            "schema": base.SCHEMA,
            **identity,
        },
        "configuration": vars(arguments),
        "results": {
            **snapshot,
            "offered_tps": round(
                int(snapshot["offered_transactions"])
                / max(arguments.duration_seconds, 0.000001),
                3,
            ),
            "completed_tps": round(completed / max(elapsed, 0.000001), 3),
            "queue_backlog_transactions": int(snapshot["enqueued_transactions"])
            - completed
            - int(snapshot["failed_batches"]) * 10,
            "distribution_percentages": {
                key: round(value / max(completed, 1) * 100, 3)
                for key, value in dict(snapshot["operations"]).items()
            },
        },
        "semantics": "open-loop: a producer schedules 1000 transactions/s; completed_tps is measured separately",
    }
    report_dir = Path(__file__).resolve().parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    json_path = report_dir / f"sakila-open-loop-{stamp}.json"
    md_path = report_dir / f"sakila-open-loop-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(
        "\n".join(
            [
                "# Resultado do gerador open-loop Sakila",
                "",
                f"- Ofertado: `{snapshot['offered_transactions']}` transacoes",
                f"- Enfileirado: `{snapshot['enqueued_transactions']}` transacoes",
                f"- Descartado ao finalizar: `{snapshot['rejected_transactions']}` transacoes",
                f"- Concluido: `{snapshot['completed_transactions']}` transacoes",
                f"- Offered TPS: `{report['results']['offered_tps']}`",
                f"- Completed TPS: `{report['results']['completed_tps']}`",
                f"- Distribuicao: `{report['results']['distribution_percentages']}`",
                f"- Falhas de lote: `{snapshot['failed_batches']}`",
                f"- Tentativas de retry: `{snapshot['retry_attempts']}`",
                f"- Backlog ao finalizar: `{report['results']['queue_backlog_transactions']}` transacoes",
                "",
                "O valor de 1.000 TPS e a taxa oferecida pelo produtor; completed_tps mede o que o banco processou. Jobs descartados ao finalizar nao chegaram ao MySQL.",
                "",
                f"Evidencia JSON: `{json_path.name}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return json_path, md_path


def load_inventory_pool() -> queue.Queue[InventoryLease]:
    """Carrega leases livres uma vez para evitar scans concorrentes no hot path."""
    result = base.run_sql(
        """
        USE sakila;
        SELECT i.inventory_id,
               (SELECT c.customer_id
                  FROM sakila.customer AS c
                 WHERE c.store_id = i.store_id AND c.active = 1
                 ORDER BY c.customer_id LIMIT 1) AS customer_id,
               (SELECT s.staff_id
                  FROM sakila.staff AS s
                 WHERE s.store_id = i.store_id AND s.active = 1
                 ORDER BY s.staff_id LIMIT 1) AS staff_id,
               COALESCE((SELECT MAX(r.rental_id)
                           FROM sakila.rental AS r
                          WHERE r.inventory_id = i.inventory_id), 0) AS baseline_rental_id
          FROM sakila.inventory AS i
         WHERE NOT EXISTS (
                   SELECT 1 FROM sakila.rental AS open_rental
                    WHERE open_rental.inventory_id = i.inventory_id
                      AND open_rental.return_date IS NULL
               )
         ORDER BY i.inventory_id;
        """,
        timeout_seconds=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"preflight de inventario falhou: {base.sanitize_error(result.stderr)}"
        )
    pool: queue.Queue[InventoryLease] = queue.Queue()
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 4:
            continue
        pool.put(InventoryLease(*(int(value) for value in fields)))
    if pool.qsize() < 4:
        raise RuntimeError("pool global de inventario nao possui quatro leases livres")
    return pool


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-target", choices=[base.SCHEMA])
    parser.add_argument("--confirm-open-loop", choices=[CONFIRMATION])
    parser.add_argument("--target-tps", type=float, default=DEFAULT_TARGET_TPS)
    parser.add_argument(
        "--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS
    )
    parser.add_argument("--total-transactions", type=int, default=None)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--queue-size", type=int, default=DEFAULT_QUEUE_SIZE)
    parser.add_argument("--transaction-timeout-seconds", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--progress-interval-seconds", type=int, default=10)
    parser.add_argument("--drain-queue", action="store_true")
    arguments = parser.parse_args()
    if (
        arguments.target_tps <= 0
        or arguments.duration_seconds < 1
        or arguments.workers < 1
    ):
        parser.error("target-tps, duration-seconds e workers devem ser positivos")
    if arguments.total_transactions is not None and (
        arguments.total_transactions < 10 or arguments.total_transactions % 10 != 0
    ):
        parser.error("total-transactions deve ser multiplo de 10 e >=10")
    if arguments.max_retries < 0 or arguments.max_retries > 5:
        parser.error("max-retries deve ficar entre 0 e 5")
    if arguments.workers > heavy.MAX_WORKERS:
        parser.error(f"workers deve ficar entre 1 e {heavy.MAX_WORKERS}")
    if arguments.queue_size < 10 or arguments.progress_interval_seconds < 1:
        parser.error("queue-size deve ser >=10 e progress-interval-seconds >=1")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    logical_transactions_per_batch = 10
    if not arguments.execute:
        print("dry-run: nenhum acesso ao banco foi realizado")
        print(
            f"open-loop: oferece {arguments.target_tps:g} TPS por {arguments.duration_seconds}s; "
            f"workers={arguments.workers}; queue={arguments.queue_size}; cada job={logical_transactions_per_batch} transacoes"
        )
        print(
            f"para executar: --execute --confirm-target sakila --confirm-open-loop {CONFIRMATION}"
        )
        return 0
    if (
        arguments.confirm_target != base.SCHEMA
        or arguments.confirm_open_loop != CONFIRMATION
    ):
        print(
            f"erro: --execute exige --confirm-target sakila e --confirm-open-loop {CONFIRMATION}"
        )
        return 2
    try:
        identity = heavy.capacity_preflight(arguments.workers)
        inventory_pool = load_inventory_pool()
    except RuntimeError as error:
        print(f"erro: {error}")
        return 2
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    stats = OpenStats(started_at=base.timestamp())
    jobs: queue.Queue[Job | None] = queue.Queue(maxsize=arguments.queue_size)
    states = [
        heavy.WorkerState(worker_id) for worker_id in range(1, arguments.workers + 1)
    ]
    threads = [
        threading.Thread(
            target=worker,
            args=(state, jobs, arguments, stop_event, stats, inventory_pool),
            name=f"sakila-open-{state.worker_id}",
        )
        for state in states
    ]
    for thread in threads:
        thread.start()
    started = time.monotonic()
    period = logical_transactions_per_batch / arguments.target_tps
    next_release = started
    sequence = 0
    next_progress = started + arguments.progress_interval_seconds
    deadline = started + arguments.duration_seconds
    while time.monotonic() < deadline and not stop_event.is_set():
        now = time.monotonic()
        if now < next_release:
            time.sleep(min(next_release - now, 0.001))
            continue
        if arguments.total_transactions is not None:
            remaining = (
                arguments.total_transactions - stats.snapshot()["offered_transactions"]
            )
            if remaining <= 0:
                break
            offered_now = min(logical_transactions_per_batch, int(remaining))
        else:
            offered_now = logical_transactions_per_batch
        stats.add_offered(offered_now)
        try:
            jobs.put_nowait(Job(sequence))
            stats.add_enqueued(offered_now)
            sequence += 1
        except queue.Full:
            stats.add_rejected(offered_now)
        next_release += period
        if now >= next_progress:
            snapshot = stats.snapshot()
            print(
                f"elapsed={now - started:.1f}s offered={snapshot['offered_transactions']} "
                f"completed={snapshot['completed_transactions']} rejected={snapshot['rejected_transactions']}",
                flush=True,
            )
            next_progress += arguments.progress_interval_seconds
    if arguments.drain_queue:
        # Aguarda todas as tarefas, inclusive retries, antes de inserir os
        # sentinelas. Caso contrario um sentinel pode ultrapassar um retry.
        jobs.join()
    else:
        stop_event.set()
    for _ in threads:
        jobs.put(None)
    jobs.join()
    for thread in threads:
        thread.join()
    json_path, md_path = write_report(
        arguments, identity, stats, time.monotonic() - started
    )
    snapshot = stats.snapshot()
    print(
        f"offered={snapshot['offered_transactions']} enqueued={snapshot['enqueued_transactions']} "
        f"completed={snapshot['completed_transactions']} rejected={snapshot['rejected_transactions']}"
    )
    print(f"report={md_path}")
    print(f"json={json_path}")
    return 0 if not snapshot["failed_batches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
