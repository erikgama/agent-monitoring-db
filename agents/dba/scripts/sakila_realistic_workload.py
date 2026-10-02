#!/usr/bin/env python3
"""Workload transacional realista e controlado para o schema Sakila."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import select
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_monitoring.config import database_settings  # noqa: E402

DATABASE_SETTINGS = database_settings("workload")
LOGIN_FILE = DATABASE_SETTINGS.login_file
LOGIN_PATH = DATABASE_SETTINGS.login_path
HOST = DATABASE_SETTINGS.target_label
PORT = DATABASE_SETTINGS.expected_port
SCHEMA = "sakila"
DEFAULT_DURATION_SECONDS = 600
DEFAULT_WORKERS = 8
DEFAULT_MIN_TRANSACTIONS = 10000
DEFAULT_MAX_TRANSACTIONS = 15000
TRANSACTION_TIMEOUT_SECONDS = 45


@dataclass
class SyntheticRecord:
    rental_id: int
    payment_id: int
    returned: bool = False


@dataclass
class WorkerState:
    worker_id: int
    records: list[SyntheticRecord] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Stats:
    started_at: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    attempts: int = 0
    succeeded: int = 0
    failed: int = 0
    timed_out: int = 0
    latency_sum: float = 0.0
    latency_min: float | None = None
    latency_max: float | None = None
    operations: dict[str, int] = field(
        default_factory=lambda: {"read": 0, "insert": 0, "update": 0, "delete": 0}
    )
    errors: list[str] = field(default_factory=list)
    cleanup_failures: list[str] = field(default_factory=list)

    def reserve(self, maximum: int, amount: int = 1) -> bool:
        with self.lock:
            if self.attempts + amount > maximum:
                return False
            self.attempts += amount
            return True

    def target_met(self, minimum: int) -> bool:
        with self.lock:
            return self.succeeded >= minimum

    def record(
        self,
        operation: str,
        elapsed: float,
        success: bool,
        error: str | None = None,
        timeout: bool = False,
    ) -> None:
        with self.lock:
            self.operations[operation] += 1
            self.latency_sum += elapsed
            self.latency_min = (
                elapsed if self.latency_min is None else min(self.latency_min, elapsed)
            )
            self.latency_max = (
                elapsed if self.latency_max is None else max(self.latency_max, elapsed)
            )
            if success:
                self.succeeded += 1
            else:
                self.failed += 1
                if timeout:
                    self.timed_out += 1
                if error and len(self.errors) < 20:
                    self.errors.append(error)


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_error(value: str) -> str:
    compact = " ".join(value.split())[-500:]
    return (
        re.sub(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+", r"\1=[REDACTED]", compact)
        or "erro sem detalhe do cliente MySQL"
    )


def mysql_command(sql: str | None = None) -> list[str]:
    command = [
        DATABASE_SETTINGS.mysql_binary,
        f"--login-path={LOGIN_PATH}",
        "--protocol=TCP",
        *DATABASE_SETTINGS.tls_flags(),
        f"--database={SCHEMA}",
        "--batch",
        "--raw",
        "--skip-column-names",
    ]
    if sql is not None:
        command.append(f"--execute={sql}")
    else:
        command.append("--unbuffered")
    return command


def mysql_environment() -> dict[str, str]:
    environment = DATABASE_SETTINGS.environment()
    environment["MYSQL_TEST_LOGIN_FILE"] = str(LOGIN_FILE)
    return environment


def run_sql(sql: str, timeout_seconds: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        mysql_command(sql),
        env=mysql_environment(),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )


class PersistentQueryTimeout(RuntimeError):
    pass


class NoInventoryAvailable(RuntimeError):
    pass


class PersistentMysql:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            mysql_command(),
            env=mysql_environment(),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        self.stderr_lines: list[str] = []
        self.stderr_lock = threading.Lock()
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.stderr_thread.start()
        self.sequence = 0

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            with self.stderr_lock:
                self.stderr_lines.append(line)

    def execute_collect(
        self, sql: str, timeout_seconds: int = TRANSACTION_TIMEOUT_SECONDS
    ) -> list[str]:
        if self.process.poll() is not None:
            with self.stderr_lock:
                detail = " ".join(self.stderr_lines[-10:])
            suffix = f": {sanitize_error(detail)}" if detail else ""
            raise RuntimeError(
                f"cliente MySQL persistente encerrou inesperadamente (rc={self.process.returncode}){suffix}"
            )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.sequence += 1
        marker = f"__SAKILA_REALISTIC_{self.sequence}__"
        with self.stderr_lock:
            error_start = len(self.stderr_lines)
        statement = sql.rstrip()
        if not statement.endswith(";"):
            statement += ";"
        self.process.stdin.write(f"{statement}\nSELECT '{marker}';\n")
        self.process.stdin.flush()
        output: list[str] = []
        deadline = time.monotonic() + timeout_seconds
        poller = select.poll()
        poller.register(self.process.stdout, select.POLLIN)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PersistentQueryTimeout(f"timeout apos {timeout_seconds}s")
            events = poller.poll(max(1, int(remaining * 1000)))
            if not events:
                raise PersistentQueryTimeout(f"timeout apos {timeout_seconds}s")
            line = self.process.stdout.readline()
            if not line:
                with self.stderr_lock:
                    detail = " ".join(self.stderr_lines[error_start:]) or " ".join(
                        self.stderr_lines[-10:]
                    )
                suffix = f": {sanitize_error(detail)}" if detail else ""
                raise RuntimeError(
                    f"cliente MySQL persistente fechou a saida (rc={self.process.poll()}){suffix}"
                )
            if line.strip() == marker:
                break
            output.append(line.rstrip("\n"))
        time.sleep(0.01)
        with self.stderr_lock:
            errors = self.stderr_lines[error_start:]
        if errors:
            raise RuntimeError(sanitize_error(" ".join(errors)))
        return output

    def execute(
        self, sql: str, timeout_seconds: int = TRANSACTION_TIMEOUT_SECONDS
    ) -> None:
        self.execute_collect(sql, timeout_seconds)

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def preflight() -> dict[str, str]:
    if not LOGIN_FILE.is_file():
        raise RuntimeError(f"perfil de conexao nao encontrado: {LOGIN_FILE}")
    sql = """
        SELECT DATABASE(), @@hostname, @@port, @@global.read_only,
               @@global.super_read_only, @@version, @@version_comment;
        SELECT COUNT(*)
          FROM information_schema.tables
         WHERE table_schema = 'sakila'
           AND table_name IN ('customer','film','inventory','staff','rental','payment');
    """
    result = run_sql(sql)
    if result.returncode != 0:
        raise RuntimeError(f"preflight falhou: {sanitize_error(result.stderr)}")
    lines = result.stdout.splitlines()
    if len(lines) != 2 or len(lines[0].split("\t")) != 7:
        raise RuntimeError("preflight retornou formato inesperado")
    database, hostname, port, read_only, super_read_only, version, version_comment = (
        lines[0].split("\t")
    )
    if (
        database != SCHEMA
        or port != str(PORT)
        or read_only != "0"
        or super_read_only != "0"
    ):
        raise RuntimeError("preflight recusou schema, porta ou servidor read_only")
    if (
        "cloud" not in version_comment.lower()
        and "heatwave" not in version_comment.lower()
    ):
        raise RuntimeError(
            "servidor nao foi identificado como MySQL HeatWave gerenciado"
        )
    if lines[1].strip() != "6":
        raise RuntimeError("tabelas obrigatorias de sakila ausentes")
    return {
        "database": database,
        "hostname": hostname,
        "port": port,
        "version": version,
        "version_comment": version_comment,
    }


def insert_sql(worker_id: int, workers: int) -> str:
    slot = (worker_id - 1) % workers
    return f"""
USE sakila;
START TRANSACTION;
SET @workload_inventory_id = NULL;
SET @workload_store_id = NULL;
SET @workload_film_id = NULL;
SET @workload_customer_id = NULL;
SET @workload_staff_id = NULL;
SET @workload_rental_id = NULL;
SET @workload_payment_id = NULL;
SELECT i.inventory_id, i.store_id, i.film_id
  INTO @workload_inventory_id, @workload_store_id, @workload_film_id
  FROM sakila.inventory AS i
 WHERE MOD(i.inventory_id, {workers}) = {slot}
   AND NOT EXISTS (
       SELECT 1 FROM sakila.rental AS open_rental
        WHERE open_rental.inventory_id = i.inventory_id
          AND open_rental.return_date IS NULL
   )
 ORDER BY i.inventory_id
 LIMIT 1
 FOR UPDATE SKIP LOCKED;
SELECT c.customer_id INTO @workload_customer_id
  FROM sakila.customer AS c
 WHERE c.store_id = @workload_store_id AND c.active = 1
 ORDER BY c.customer_id LIMIT 1;
SELECT s.staff_id INTO @workload_staff_id
  FROM sakila.staff AS s
 WHERE s.store_id = @workload_store_id AND s.active = 1
 ORDER BY s.staff_id LIMIT 1;
SET @workload_has_inventory = @workload_inventory_id IS NOT NULL
                            AND @workload_customer_id IS NOT NULL
                            AND @workload_staff_id IS NOT NULL;
INSERT INTO sakila.rental
       (rental_date, inventory_id, customer_id, return_date, staff_id)
SELECT NOW(), @workload_inventory_id, @workload_customer_id, NULL, @workload_staff_id
 WHERE @workload_has_inventory;
SET @workload_rental_id = IF(@workload_has_inventory, LAST_INSERT_ID(), NULL);
INSERT INTO sakila.payment
       (customer_id, staff_id, rental_id, amount, payment_date)
SELECT @workload_customer_id, @workload_staff_id, @workload_rental_id,
       ROUND(1.00 + RAND() * 9.00, 2), NOW()
 WHERE @workload_has_inventory;
SET @workload_payment_id = IF(@workload_has_inventory, LAST_INSERT_ID(), NULL);
COMMIT;
SELECT IF(@workload_has_inventory,
          CONCAT(@workload_rental_id, '\\t', @workload_payment_id),
          'NO_INVENTORY');
""".strip()


def balanced_active_sql(worker_id: int, workers: int) -> str:
    """INSERT, READ and UPDATE stages in one persistent round-trip."""
    slot = (worker_id - 1) % workers
    return f"""
USE sakila;
START TRANSACTION;
SET @workload_inventory_id = NULL;
SET @workload_store_id = NULL;
SET @workload_film_id = NULL;
SET @workload_customer_id = NULL;
SET @workload_staff_id = NULL;
SET @workload_rental_id = NULL;
SET @workload_payment_id = NULL;
SELECT i.inventory_id, i.store_id, i.film_id
  INTO @workload_inventory_id, @workload_store_id, @workload_film_id
  FROM sakila.inventory AS i
 WHERE MOD(i.inventory_id, {workers}) = {slot}
   AND NOT EXISTS (
       SELECT 1 FROM sakila.rental AS open_rental
        WHERE open_rental.inventory_id = i.inventory_id
          AND open_rental.return_date IS NULL
   )
 ORDER BY i.inventory_id
 LIMIT 1
 FOR UPDATE SKIP LOCKED;
SELECT c.customer_id INTO @workload_customer_id
  FROM sakila.customer AS c
 WHERE c.store_id = @workload_store_id AND c.active = 1
 ORDER BY c.customer_id LIMIT 1;
SELECT s.staff_id INTO @workload_staff_id
  FROM sakila.staff AS s
 WHERE s.store_id = @workload_store_id AND s.active = 1
 ORDER BY s.staff_id LIMIT 1;
SET @workload_has_inventory = @workload_inventory_id IS NOT NULL
                            AND @workload_customer_id IS NOT NULL
                            AND @workload_staff_id IS NOT NULL;
INSERT INTO sakila.rental
       (rental_date, inventory_id, customer_id, return_date, staff_id)
SELECT NOW(), @workload_inventory_id, @workload_customer_id, NULL, @workload_staff_id
 WHERE @workload_has_inventory;
SET @workload_rental_id = IF(@workload_has_inventory, LAST_INSERT_ID(), NULL);
INSERT INTO sakila.payment
       (customer_id, staff_id, rental_id, amount, payment_date)
SELECT @workload_customer_id, @workload_staff_id, @workload_rental_id,
       ROUND(1.00 + RAND() * 9.00, 2), NOW()
 WHERE @workload_has_inventory;
SET @workload_payment_id = IF(@workload_has_inventory, LAST_INSERT_ID(), NULL);
COMMIT;

START TRANSACTION;
SELECT f.film_id, f.title, f.rental_rate
  FROM sakila.film AS f
 ORDER BY f.film_id LIMIT 10;
SELECT c.store_id, COUNT(*)
  FROM sakila.customer AS c
 WHERE c.active = 1 GROUP BY c.store_id;
SELECT r.rental_id, r.rental_date, r.return_date, p.amount, p.payment_date
  FROM sakila.rental AS r
  LEFT JOIN sakila.payment AS p ON p.rental_id = r.rental_id
 WHERE r.rental_id = @workload_rental_id;
COMMIT;

START TRANSACTION;
UPDATE sakila.rental
   SET return_date = COALESCE(return_date, NOW())
 WHERE rental_id = @workload_rental_id AND @workload_has_inventory;
UPDATE sakila.payment
   SET amount = LEAST(amount + 0.01, 999.99)
 WHERE payment_id = @workload_payment_id
   AND rental_id = @workload_rental_id
   AND @workload_has_inventory;
COMMIT;
SELECT IF(@workload_has_inventory,
          CONCAT(@workload_rental_id, '\\t', @workload_payment_id),
          'NO_INVENTORY');
""".strip()


def read_sql(record: SyntheticRecord | None) -> str:
    if record is None:
        return """
USE sakila;
SELECT f.film_id, f.title, f.rental_rate
  FROM sakila.film AS f
 ORDER BY f.film_id LIMIT 10;
SELECT c.store_id, COUNT(*)
  FROM sakila.customer AS c
 WHERE c.active = 1 GROUP BY c.store_id;
""".strip()
    return f"""
USE sakila;
SELECT r.rental_id, r.rental_date, r.return_date, p.amount, p.payment_date
  FROM sakila.rental AS r
  LEFT JOIN sakila.payment AS p ON p.rental_id = r.rental_id
 WHERE r.rental_id = {record.rental_id};
""".strip()


def update_sql(record: SyntheticRecord) -> str:
    return f"""
USE sakila;
START TRANSACTION;
SELECT rental_id FROM sakila.rental WHERE rental_id = {record.rental_id} FOR UPDATE;
UPDATE sakila.rental SET return_date = COALESCE(return_date, NOW()) WHERE rental_id = {record.rental_id};
UPDATE sakila.payment SET amount = LEAST(amount + 0.01, 999.99) WHERE payment_id = {record.payment_id} AND rental_id = {record.rental_id};
COMMIT;
SELECT 1;
""".strip()


def delete_sql(records: list[SyntheticRecord]) -> str:
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


def cleanup(state: WorkerState, stats: Stats) -> None:
    with state.lock:
        records = list(state.records)
        state.records.clear()
    if not records:
        return
    session: PersistentMysql | None = None
    try:
        session = PersistentMysql()
        for start in range(0, len(records), 200):
            session.execute(delete_sql(records[start : start + 200]))
    except (OSError, RuntimeError, PersistentQueryTimeout) as error:
        with stats.lock:
            stats.cleanup_failures.append(sanitize_error(str(error)))
    finally:
        if session is not None:
            session.close()


def choose_operation(
    rng: random.Random,
    arguments: argparse.Namespace,
    stats: Stats,
    can_update: bool,
    can_delete: bool,
) -> str:
    """Choose the most under-represented feasible operation.

    A plain weighted random choice becomes read-heavy because update/delete are
    temporarily infeasible while the worker has no eligible synthetic row.
    Deficit scheduling keeps the observed mix close to the requested mix while
    still allowing the lifecycle to build and drain its own record queue.
    """
    weights = {
        "read": arguments.read_percent,
        "insert": arguments.insert_percent,
        "update": arguments.update_percent,
        "delete": arguments.delete_percent,
    }
    feasible = ["read", "insert"]
    if can_update:
        feasible.append("update")
    if can_delete:
        feasible.append("delete")
    with stats.lock:
        counts = dict(stats.operations)
        total = sum(counts.values())
    expected_total = total + 1
    deficits = {
        operation: (weights[operation] / 100.0) * expected_total - counts[operation]
        for operation in feasible
    }
    highest = max(deficits.values())
    candidates = [
        operation for operation in feasible if deficits[operation] >= highest - 0.5
    ]
    return rng.choice(candidates)


def worker_balanced(
    state: WorkerState,
    arguments: argparse.Namespace,
    deadline: float,
    stop_event: threading.Event,
    stats: Stats,
) -> None:
    """Run complete lifecycle cycles with exactly one transaction per stage."""
    session: PersistentMysql | None = None
    try:
        session = PersistentMysql()
        while not stop_event.is_set() and (
            time.monotonic() < deadline
            or not stats.target_met(arguments.min_transactions)
        ):
            if not stats.reserve(arguments.max_transactions, 3):
                return
            started = time.monotonic()
            try:
                rows = session.execute_collect(
                    balanced_active_sql(state.worker_id, arguments.workers)
                )
                if rows and rows[-1] == "NO_INVENTORY":
                    with stats.lock:
                        stats.attempts -= 3
                    continue
                if not rows or "\t" not in rows[-1]:
                    raise RuntimeError("ciclo nao retornou as chaves sinteticas")
                rental_id, payment_id = (int(value) for value in rows[-1].split("\t"))
                elapsed = (time.monotonic() - started) / 3
                for operation in ("insert", "read", "update"):
                    stats.record(operation, elapsed, True)
                record = SyntheticRecord(rental_id, payment_id, returned=True)
                with state.lock:
                    state.records.append(record)
                if not stats.reserve(arguments.max_transactions, 1):
                    return
                delete_started = time.monotonic()
                session.execute(delete_sql([record]))
                stats.record("delete", time.monotonic() - delete_started, True)
                with state.lock:
                    if record in state.records:
                        state.records.remove(record)
            except PersistentQueryTimeout as error:
                stats.record(
                    "insert",
                    time.monotonic() - started,
                    False,
                    str(error),
                    timeout=True,
                )
                stats.record("read", 0.0, False, str(error), timeout=True)
                stats.record("update", 0.0, False, str(error), timeout=True)
                stop_event.set()
            except (OSError, RuntimeError, ValueError) as error:
                stats.record(
                    "insert",
                    time.monotonic() - started,
                    False,
                    sanitize_error(str(error)),
                )
                stats.record("read", 0.0, False, sanitize_error(str(error)))
                stats.record("update", 0.0, False, sanitize_error(str(error)))
                stop_event.set()
    except OSError as error:
        stats.record("insert", 0.0, False, error.__class__.__name__)
        stats.record("read", 0.0, False, error.__class__.__name__)
        stats.record("update", 0.0, False, error.__class__.__name__)
        stop_event.set()
    finally:
        if session is not None:
            session.close()
        cleanup(state, stats)


def worker(
    state: WorkerState,
    arguments: argparse.Namespace,
    deadline: float,
    stop_event: threading.Event,
    stats: Stats,
) -> None:
    if arguments.mode == "balanced-cycle":
        worker_balanced(state, arguments, deadline, stop_event, stats)
        return
    rng = random.Random(f"sakila-realistic-{state.worker_id}-{time.time_ns()}")
    session: PersistentMysql | None = None
    try:
        session = PersistentMysql()
        while not stop_event.is_set() and (
            time.monotonic() < deadline
            or not stats.target_met(arguments.min_transactions)
        ):
            if not stats.reserve(arguments.max_transactions):
                return
            with state.lock:
                has_records = bool(state.records)
                readable = rng.choice(state.records) if has_records else None
                updatable = (
                    next(
                        (record for record in state.records if not record.returned),
                        None,
                    )
                    if has_records
                    else None
                )
                deletable = (
                    next((record for record in state.records if record.returned), None)
                    if has_records
                    else None
                )
            operation = choose_operation(
                rng, arguments, stats, updatable is not None, deletable is not None
            )
            started = time.monotonic()
            try:
                if operation == "insert":
                    rows = session.execute_collect(
                        insert_sql(state.worker_id, arguments.workers)
                    )
                    if rows and rows[-1] == "NO_INVENTORY":
                        raise NoInventoryAvailable(
                            "nenhum inventario livre para este worker"
                        )
                    if not rows or "\t" not in rows[-1]:
                        raise RuntimeError("INSERT nao retornou as chaves sinteticas")
                    rental_id, payment_id = (
                        int(value) for value in rows[-1].split("\t")
                    )
                    with state.lock:
                        state.records.append(SyntheticRecord(rental_id, payment_id))
                elif operation == "read":
                    session.execute(read_sql(readable))
                elif operation == "update":
                    assert updatable is not None
                    session.execute(update_sql(updatable))
                    with state.lock:
                        updatable.returned = True
                else:
                    assert deletable is not None
                    session.execute(delete_sql([deletable]))
                    with state.lock:
                        state.records.remove(deletable)
                stats.record(operation, time.monotonic() - started, True)
            except PersistentQueryTimeout as error:
                stats.record(
                    operation,
                    time.monotonic() - started,
                    False,
                    str(error),
                    timeout=True,
                )
                stop_event.set()
            except NoInventoryAvailable:
                with stats.lock:
                    stats.attempts -= 1
            except (OSError, RuntimeError, ValueError) as error:
                stats.record(
                    operation,
                    time.monotonic() - started,
                    False,
                    sanitize_error(str(error)),
                )
                stop_event.set()
            if arguments.think_time_ms:
                stop_event.wait(
                    arguments.think_time_ms / 1000 * rng.uniform(0.75, 1.25)
                )
    except OSError as error:
        stats.record("read", 0.0, False, error.__class__.__name__)
        stop_event.set()
    finally:
        if session is not None:
            session.close()
        cleanup(state, stats)


def write_report(
    arguments: argparse.Namespace, identity: dict[str, str], stats: Stats
) -> tuple[Path, Path]:
    finished_at = timestamp()
    average = stats.latency_sum / stats.attempts if stats.attempts else 0.0
    report = {
        "started_at": stats.started_at,
        "finished_at": finished_at,
        "target": {"host": HOST, "port": PORT, "schema": SCHEMA, **identity},
        "configuration": vars(arguments),
        "results": {
            "attempts": stats.attempts,
            "succeeded": stats.succeeded,
            "minimum_transactions": arguments.min_transactions,
            "minimum_met": stats.succeeded >= arguments.min_transactions,
            "failed": stats.failed,
            "timed_out": stats.timed_out,
            "operations": stats.operations,
            "latency_seconds_average": round(average, 6),
            "latency_seconds_min": None
            if stats.latency_min is None
            else round(stats.latency_min, 6),
            "latency_seconds_max": None
            if stats.latency_max is None
            else round(stats.latency_max, 6),
            "errors": stats.errors,
            "cleanup_failures": stats.cleanup_failures,
        },
        "lifecycle": "INSERT e COMMIT; leituras posteriores; UPDATE e COMMIT; DELETE em transacao posterior; cleanup final",
        "persistent_side_effect": "AUTO_INCREMENT de rental e payment avanca; registros sinteticos sao removidos ao final",
    }
    configured_report_dir = os.environ.get("SAKILA_REPORT_DIR")
    reports_dir = (
        Path(configured_report_dir)
        if configured_report_dir
        else Path(__file__).resolve().parents[1] / "reports"
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    json_path = reports_dir / f"sakila-realistic-workload-{stamp}.json"
    md_path = reports_dir / f"sakila-realistic-workload-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path.write_text(
        "\n".join(
            [
                "# Resultado do workload realista Sakila",
                "",
                f"- Inicio: `{stats.started_at}`",
                f"- Fim: `{finished_at}`",
                f"- Sucessos: `{stats.succeeded}`; falhas: `{stats.failed}`; timeouts: `{stats.timed_out}`",
                f"- Minimo solicitado: `{arguments.min_transactions}`; atingido: `{stats.succeeded >= arguments.min_transactions}`",
                f"- Operacoes: `{stats.operations}`",
                f"- Latencia media: `{average:.3f}` segundos",
                "",
                "As insercoes foram confirmadas antes de leituras/updates posteriores; deletes ocorreram em transacoes separadas.",
                "Os registros sinteticos foram removidos no cleanup final; AUTO_INCREMENT nao e revertido.",
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
    parser.add_argument("--confirm-target", choices=[SCHEMA])
    parser.add_argument(
        "--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--think-time-ms", type=int, default=0)
    parser.add_argument(
        "--mode", choices=["balanced-cycle", "scheduled"], default="balanced-cycle"
    )
    parser.add_argument(
        "--min-transactions", type=int, default=DEFAULT_MIN_TRANSACTIONS
    )
    parser.add_argument(
        "--max-transactions", type=int, default=DEFAULT_MAX_TRANSACTIONS
    )
    parser.add_argument("--read-percent", type=int, default=25)
    parser.add_argument("--insert-percent", type=int, default=25)
    parser.add_argument("--update-percent", type=int, default=25)
    parser.add_argument("--delete-percent", type=int, default=25)
    arguments = parser.parse_args()
    if not 1 <= arguments.duration_seconds <= 3600 or not 1 <= arguments.workers <= 8:
        parser.error("duracao deve ficar entre 1 e 3600 e workers entre 1 e 8")
    if not 10000 <= arguments.min_transactions <= 100000:
        parser.error("min-transactions deve ficar entre 10000 e 100000")
    if not arguments.min_transactions <= arguments.max_transactions <= 100000:
        parser.error("max-transactions deve ser >= min-transactions e <= 100000")
    percentages = [
        arguments.read_percent,
        arguments.insert_percent,
        arguments.update_percent,
        arguments.delete_percent,
    ]
    if any(value < 0 for value in percentages) or sum(percentages) != 100:
        parser.error("os quatro percentuais devem ser nao negativos e somar 100")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    if not arguments.execute:
        print("dry-run: nenhum acesso ao banco foi realizado")
        print(
            f"alvo: {HOST}:{PORT}/{SCHEMA}; duracao: {arguments.duration_seconds}s; workers: {arguments.workers}"
        )
        print(
            f"minimo: {arguments.min_transactions}; maximo: {arguments.max_transactions}; operacoes: read={arguments.read_percent}% insert={arguments.insert_percent}% update={arguments.update_percent}% delete={arguments.delete_percent}%"
        )
        print("para executar: --execute --confirm-target sakila")
        return 0
    if arguments.confirm_target != SCHEMA:
        print("erro: --execute exige --confirm-target sakila")
        return 2
    try:
        identity = preflight()
    except RuntimeError as error:
        print(f"erro: {error}")
        return 2
    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda _signum, _frame: stop_event.set())
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
    stats = Stats(started_at=timestamp())
    states = [WorkerState(worker_id) for worker_id in range(1, arguments.workers + 1)]
    deadline = time.monotonic() + arguments.duration_seconds
    threads = [
        threading.Thread(
            target=worker,
            args=(state, arguments, deadline, stop_event, stats),
            name=f"sakila-realistic-{state.worker_id}",
        )
        for state in states
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    json_path, md_path = write_report(arguments, identity, stats)
    print(
        f"attempts={stats.attempts} success={stats.succeeded} failed={stats.failed} timeouts={stats.timed_out}"
    )
    print(f"operations={stats.operations}")
    print(f"report={md_path}")
    print(f"json={json_path}")
    return (
        0
        if stats.failed == 0
        and stats.succeeded >= arguments.min_transactions
        and not stats.cleanup_failures
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
