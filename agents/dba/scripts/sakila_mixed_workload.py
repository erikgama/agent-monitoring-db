#!/usr/bin/env python3
"""Carga mista controlada de 10 minutos para o Sakila no MySQL HeatWave."""

from __future__ import annotations

import argparse
import json
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
DEFAULT_THINK_TIME_MS = 0
DEFAULT_MIN_TRANSACTIONS = 10000
DEFAULT_MAX_TRANSACTIONS = 15000
TRANSACTION_TIMEOUT_SECONDS = 45

# Cada transacao cria apenas linhas sinteticas e remove exatamente essas linhas
# antes do COMMIT. Se a conexao cair antes do COMMIT, o MySQL faz rollback.
TRANSACTION_SQL_TEMPLATE = r"""
USE sakila;
SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
START TRANSACTION;

SELECT i.inventory_id, i.store_id, i.film_id
  INTO @workload_inventory_id, @workload_store_id, @workload_film_id
 FROM sakila.inventory AS i
 WHERE NOT EXISTS (
       SELECT 1
         FROM sakila.rental AS open_rental
        WHERE open_rental.inventory_id = i.inventory_id
          AND open_rental.return_date IS NULL
 )
   AND MOD(i.inventory_id, {worker_count}) = {worker_slot}
 ORDER BY RAND()
 LIMIT 1
 FOR UPDATE SKIP LOCKED;

SELECT c.customer_id
  INTO @workload_customer_id
  FROM sakila.customer AS c
 WHERE c.store_id = @workload_store_id
   AND c.active = 1
 ORDER BY RAND()
 LIMIT 1;

SELECT s.staff_id
  INTO @workload_staff_id
  FROM sakila.staff AS s
 WHERE s.store_id = @workload_store_id
   AND s.active = 1
 ORDER BY s.staff_id
 LIMIT 1;

-- Leituras que simulam catalogo e historico do cliente.
SELECT COUNT(*)
  INTO @workload_catalog_matches
  FROM sakila.film AS f
 WHERE f.film_id = @workload_film_id
   AND f.rental_duration > 0;

SELECT COUNT(*), COALESCE(SUM(p.amount), 0)
  INTO @workload_previous_payments, @workload_previous_total
  FROM sakila.payment AS p
 WHERE p.customer_id = @workload_customer_id;

-- Escrita real, limitada a registros sinteticos desta transacao.
INSERT INTO sakila.rental
       (rental_date, inventory_id, customer_id, return_date, staff_id)
VALUES (NOW(), @workload_inventory_id, @workload_customer_id, NULL,
        @workload_staff_id);
SET @workload_rental_id = LAST_INSERT_ID();

INSERT INTO sakila.payment
       (customer_id, staff_id, rental_id, amount, payment_date)
VALUES (@workload_customer_id, @workload_staff_id, @workload_rental_id,
        ROUND(1.00 + RAND() * 9.00, 2), NOW());
SET @workload_payment_id = LAST_INSERT_ID();

-- Leitura do fluxo recem-criado.
SELECT COUNT(*), COALESCE(SUM(p.amount), 0)
  INTO @workload_created_rows, @workload_created_total
  FROM sakila.rental AS r
  JOIN sakila.payment AS p ON p.rental_id = r.rental_id
 WHERE r.rental_id = @workload_rental_id
   AND p.payment_id = @workload_payment_id;

UPDATE sakila.rental
   SET return_date = NOW()
 WHERE rental_id = @workload_rental_id;

UPDATE sakila.payment
   SET amount = LEAST(amount + 0.01, 999.99)
 WHERE payment_id = @workload_payment_id;

-- Cleanup: DELETE atinge somente as chaves geradas acima.
DELETE FROM sakila.payment
 WHERE payment_id = @workload_payment_id
   AND rental_id = @workload_rental_id;

DELETE FROM sakila.rental
 WHERE rental_id = @workload_rental_id
   AND customer_id = @workload_customer_id;

COMMIT;
SELECT 1 AS workload_transaction_committed;
""".strip()


@dataclass
class Stats:
    started_at: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    attempts: int = 0
    succeeded: int = 0
    failed: int = 0
    timed_out: int = 0
    latency_seconds_sum: float = 0.0
    latency_seconds_min: float | None = None
    latency_seconds_max: float | None = None
    errors: list[str] = field(default_factory=list)

    def reserve(self, maximum: int) -> bool:
        with self.lock:
            if self.attempts >= maximum:
                return False
            self.attempts += 1
            return True

    def target_met(self, minimum: int) -> bool:
        with self.lock:
            return self.succeeded >= minimum

    def record(self, duration: float, status: str, error: str | None = None) -> None:
        with self.lock:
            self.latency_seconds_sum += duration
            self.latency_seconds_min = (
                duration
                if self.latency_seconds_min is None
                else min(self.latency_seconds_min, duration)
            )
            self.latency_seconds_max = (
                duration
                if self.latency_seconds_max is None
                else max(self.latency_seconds_max, duration)
            )
            if status == "success":
                self.succeeded += 1
            else:
                self.failed += 1
                if status == "timeout":
                    self.timed_out += 1
                if error and len(self.errors) < 10:
                    self.errors.append(error)


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize_error(value: str) -> str:
    compact = " ".join(value.split())[-500:]
    compact = re.sub(
        r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+", r"\1=[REDACTED]", compact
    )
    return compact or "erro sem detalhe do cliente MySQL"


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


def run_sql(sql: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
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


class PersistentMysql:
    """Mantem uma sessao mysql aberta e delimita cada rodada por sentinela."""

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
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        self._sequence = 0

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            with self._stderr_lock:
                self._stderr_lines.append(line)

    def execute(self, sql: str, timeout_seconds: int) -> None:
        if self.process.poll() is not None:
            raise RuntimeError("cliente MySQL persistente encerrou inesperadamente")
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self._sequence += 1
        marker = f"__SAKILA_WORKLOAD_{self._sequence}__"
        error_start = len(self._stderr_lines)
        statement = sql.rstrip()
        if not statement.endswith(";"):
            statement += ";"
        self.process.stdin.write(f"{statement}\nSELECT '{marker}';\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PersistentQueryTimeout(f"timeout apos {timeout_seconds}s")
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            if not ready:
                raise PersistentQueryTimeout(f"timeout apos {timeout_seconds}s")
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("cliente MySQL persistente fechou a saida")
            if line.strip() == marker:
                break
        time.sleep(0.01)
        with self._stderr_lock:
            errors = self._stderr_lines[error_start:]
        if errors:
            raise RuntimeError(sanitize_error(" ".join(errors)))

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
    completed = run_sql(sql, 20)
    if completed.returncode != 0:
        raise RuntimeError(f"preflight falhou: {sanitize_error(completed.stderr)}")
    lines = completed.stdout.splitlines()
    if len(lines) != 2:
        raise RuntimeError("preflight retornou formato inesperado")
    identity = lines[0].split("\t")
    if len(identity) != 7:
        raise RuntimeError("identidade do servidor incompleta")
    database, hostname, port, read_only, super_read_only, version, version_comment = (
        identity
    )
    if database != SCHEMA or port != str(PORT):
        raise RuntimeError("preflight recusou schema ou porta inesperados")
    if read_only != "0" or super_read_only != "0":
        raise RuntimeError("servidor esta read_only ou super_read_only")
    if (
        "cloud" not in version_comment.lower()
        and "heatwave" not in version_comment.lower()
    ):
        raise RuntimeError(
            "servidor nao foi identificado como MySQL HeatWave gerenciado"
        )
    if lines[1].strip() != "6":
        raise RuntimeError("uma ou mais tabelas obrigatorias de sakila nao existem")
    return {
        "database": database,
        "hostname": hostname,
        "port": port,
        "version": version,
        "version_comment": version_comment,
    }


def worker(
    worker_id: int,
    worker_count: int,
    deadline: float,
    think_time_ms: int,
    minimum: int,
    maximum: int,
    stop_event: threading.Event,
    stats: Stats,
) -> None:
    rng = random.Random(f"sakila-worker-{worker_id}-{time.time_ns()}")
    session: PersistentMysql | None = None
    try:
        session = PersistentMysql()
        while not stop_event.is_set() and (
            time.monotonic() < deadline or not stats.target_met(minimum)
        ):
            if not stats.reserve(maximum):
                return
            started = time.monotonic()
            try:
                session.execute(
                    TRANSACTION_SQL_TEMPLATE.format(
                        worker_count=worker_count,
                        worker_slot=(worker_id - 1) % worker_count,
                    ),
                    TRANSACTION_TIMEOUT_SECONDS,
                )
                stats.record(time.monotonic() - started, "success")
            except PersistentQueryTimeout as error:
                stats.record(time.monotonic() - started, "timeout", str(error))
                stop_event.set()
            except (OSError, RuntimeError) as error:
                stats.record(
                    time.monotonic() - started, "error", sanitize_error(str(error))
                )
                stop_event.set()
            delay = max(0.0, think_time_ms / 1000 * rng.uniform(0.75, 1.25))
            stop_event.wait(delay)
    except OSError as error:
        stats.record(0.0, "error", error.__class__.__name__)
    finally:
        if session is not None:
            session.close()


def write_report(
    arguments: argparse.Namespace, identity: dict[str, str], stats: Stats
) -> tuple[Path, Path]:
    finished_at = timestamp()
    average = stats.latency_seconds_sum / stats.attempts if stats.attempts else 0.0
    report = {
        "started_at": stats.started_at,
        "finished_at": finished_at,
        "target": {"schema": SCHEMA, "host": HOST, "port": PORT, **identity},
        "configuration": {
            "duration_seconds": arguments.duration_seconds,
            "workers": arguments.workers,
            "think_time_ms": arguments.think_time_ms,
            "max_transactions": arguments.max_transactions,
            "min_transactions": arguments.min_transactions,
            "transaction_timeout_seconds": TRANSACTION_TIMEOUT_SECONDS,
            "connection_mode": "one persistent mysql client per worker",
        },
        "results": {
            "attempts": stats.attempts,
            "succeeded": stats.succeeded,
            "failed": stats.failed,
            "timed_out": stats.timed_out,
            "latency_seconds_average": round(average, 6),
            "latency_seconds_min": None
            if stats.latency_seconds_min is None
            else round(stats.latency_seconds_min, 6),
            "latency_seconds_max": None
            if stats.latency_seconds_max is None
            else round(stats.latency_seconds_max, 6),
            "errors": stats.errors,
        },
        "cleanup": "cada transacao exclui somente as linhas sinteticas antes do COMMIT",
        "persistent_side_effect": "AUTO_INCREMENT de rental e payment avanca a cada transacao confirmada",
    }
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    reports_dir = Path(__file__).resolve().parents[1] / "reports"
    json_path = reports_dir / f"sakila-mixed-workload-{stamp}.json"
    md_path = reports_dir / f"sakila-mixed-workload-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown = [
        "# Resultado do workload misto Sakila",
        "",
        f"- Inicio: `{report['started_at']}`",
        f"- Fim: `{report['finished_at']}`",
        f"- Duracao configurada: `{arguments.duration_seconds}` segundos",
        f"- Workers: `{arguments.workers}`",
        f"- Minimo de transacoes: `{arguments.min_transactions}`",
        f"- Maximo de tentativas: `{arguments.max_transactions}`",
        f"- Tentativas: `{stats.attempts}`",
        f"- Sucesso: `{stats.succeeded}`",
        f"- Falhas: `{stats.failed}`",
        f"- Timeouts: `{stats.timed_out}`",
        f"- Latencia media por transacao: `{average:.3f}` segundos",
        "- Conexoes: uma sessao MySQL persistente por worker",
        "",
        "Cada transacao realizou SELECT, INSERT, UPDATE e DELETE sobre dados sinteticos e confirmou o cleanup no mesmo COMMIT.",
        "O efeito persistente esperado e apenas o avanco dos AUTO_INCREMENT de rental e payment.",
        "",
        f"Evidencia estruturada: `{json_path.name}`",
        "",
    ]
    md_path.write_text("\n".join(markdown), encoding="utf-8")
    return json_path, md_path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="autoriza iniciar o workload"
    )
    parser.add_argument(
        "--confirm-target", choices=[SCHEMA], help="confirmacao obrigatoria do schema"
    )
    parser.add_argument(
        "--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--think-time-ms", type=int, default=DEFAULT_THINK_TIME_MS)
    parser.add_argument(
        "--max-transactions", type=int, default=DEFAULT_MAX_TRANSACTIONS
    )
    parser.add_argument(
        "--min-transactions", type=int, default=DEFAULT_MIN_TRANSACTIONS
    )
    arguments = parser.parse_args()
    if not 1 <= arguments.duration_seconds <= 3600:
        parser.error("duration-seconds deve ficar entre 1 e 3600")
    if not 1 <= arguments.workers <= 8:
        parser.error("workers deve ficar entre 1 e 8")
    if not 0 <= arguments.think_time_ms <= 60000:
        parser.error("think-time-ms deve ficar entre 0 e 60000")
    if not 10000 <= arguments.min_transactions <= 100000:
        parser.error("min-transactions deve ficar entre 10000 e 100000")
    if not arguments.min_transactions <= arguments.max_transactions <= 100000:
        parser.error("max-transactions deve ser >= min-transactions e <= 100000")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    if not arguments.execute:
        print("dry-run: nenhum acesso ao banco foi realizado")
        print(f"alvo fixo: {HOST}:{PORT}/{SCHEMA}")
        print(
            f"duracao: {arguments.duration_seconds}s; workers: {arguments.workers}; minimo: {arguments.min_transactions}; maximo: {arguments.max_transactions} transacoes"
        )
        print(
            "para executar, use --execute --confirm-target sakila apos aprovacao especifica"
        )
        return 0
    if arguments.confirm_target != SCHEMA:
        print("erro: --execute exige --confirm-target sakila", file=sys.stderr)
        return 2

    try:
        identity = preflight()
    except RuntimeError as error:
        print(f"erro: {error}", file=sys.stderr)
        return 2

    stop_event = threading.Event()

    def stop_handler(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    stats = Stats(started_at=timestamp())
    deadline = time.monotonic() + arguments.duration_seconds
    threads = [
        threading.Thread(
            target=worker,
            args=(
                worker_id,
                arguments.workers,
                deadline,
                arguments.think_time_ms,
                arguments.min_transactions,
                arguments.max_transactions,
                stop_event,
                stats,
            ),
            name=f"sakila-workload-{worker_id}",
        )
        for worker_id in range(1, arguments.workers + 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    json_path, md_path = write_report(arguments, identity, stats)
    print(
        f"attempts={stats.attempts} success={stats.succeeded} failed={stats.failed} timeouts={stats.timed_out}"
    )
    print(f"report={md_path}")
    print(f"json={json_path}")
    return (
        0 if stats.failed == 0 and stats.succeeded >= arguments.min_transactions else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
