from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

REFACTOR_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REFACTOR_ROOT))

from query_refactor.advisor import agent as worker

PROPOSED_SQL = """WITH daily_payment AS (
  SELECT DATE(payment_date) AS payment_day, customer_id, SUM(amount) AS daily_revenue
  FROM payment
  GROUP BY DATE(payment_date), customer_id
),
running_payment AS (
  SELECT payment_day, customer_id,
         SUM(daily_revenue) OVER (
           PARTITION BY customer_id ORDER BY payment_day ROWS UNBOUNDED PRECEDING
         ) AS cumulative_revenue
  FROM daily_payment
)
SELECT c.customer_id, p.payment_date, p.amount AS revenue, r.cumulative_revenue
FROM customer c
JOIN payment p ON p.customer_id = c.customer_id
JOIN running_payment r
  ON r.customer_id = p.customer_id AND r.payment_day = DATE(p.payment_date)
WHERE DATE_FORMAT(p.payment_date, '%Y-%m') >= '2005-01';"""


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, sql: str, timeout_seconds: float) -> worker.QueryExecution:
        self.calls += 1
        seconds = 86.0 if self.calls == 1 else 1.0
        return worker.QueryExecution(
            exit_code=0,
            seconds=seconds,
            header="customer_id\tpayment_date\trevenue\tcumulative_revenue",
            row_count=10,
            result_sha256="a" * 64,
        )


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, request: dict) -> dict:
        self.calls += 1
        return {
            "contract_version": "refactor_advisor_proposal.v1",
            "proposed_sql": PROPOSED_SQL,
            "change_summary": (
                "Remove a subconsulta correlacionada e usa agregação com janela."
            ),
            "limitations": ["O plano depende das estatísticas atuais das tabelas."],
        }


class FakePublisher:
    def __init__(self) -> None:
        self.calls = 0

    def publish(self, result: dict) -> dict:
        self.calls += 1
        return {
            "accepted": True,
            "status": "validated",
            "dba": {"status": "recorded", "recorded": True},
        }


def request_payload() -> dict:
    catalog = worker.load_original_catalog(
        worker.REPOSITORY_ROOT / worker.CATALOG_RELATIVE
    )
    original = catalog["correlated_running_total"]
    return {
        "contract_version": "query_refactor_request.v1",
        "request_id": str(uuid.uuid4()),
        "query_id": "correlated_running_total",
        "query_fingerprint": "sha256:" + "b" * 64,
        "schema": "sakila",
        "catalog_source": str(worker.CATALOG_RELATIVE),
        "original_sql": original,
        "original_sql_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "evidence": {"max_query_time_seconds": 86.0},
    }


class RefactorWorkerTests(unittest.TestCase):
    def test_request_validation_rejects_scope_threshold_and_hash_changes(self) -> None:
        valid = request_payload()
        worker.validate_request(valid)

        for field, value, expected in (
            ("schema", "sakila_dev", "request_schema_invalid"),
            ("catalog_source", "other.sql", "request_catalog_invalid"),
            ("original_sql_sha256", "0" * 64, "request_original_hash_mismatch"),
        ):
            changed = dict(valid)
            changed[field] = value
            with self.assertRaisesRegex(ValueError, expected):
                worker.validate_request(changed)

        changed = dict(valid)
        changed["evidence"] = {"max_query_time_seconds": 80.0}
        with self.assertRaisesRegex(ValueError, "request_below_threshold"):
            worker.validate_request(changed)

    def test_catalog_loads_only_original_and_timeout_hint_is_runtime_only(self) -> None:
        catalog = worker.load_original_catalog(
            worker.REPOSITORY_ROOT / worker.CATALOG_RELATIVE
        )

        self.assertIn("correlated_running_total", catalog)
        self.assertIsInstance(catalog["correlated_running_total"], str)
        original = catalog["correlated_running_total"]
        self.assertNotIn("MAX_EXECUTION_TIME", original)
        protected = worker.add_timeout_hint(original, 2.5)
        self.assertIn("MAX_EXECUTION_TIME(2500)", protected)
        self.assertEqual(protected.count("MAX_EXECUTION_TIME"), 1)

    def test_executor_forces_sakila_dev_and_hashes_rows(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="id\tvalue\n2\tb\n1\ta\n",
            stderr="",
        )
        with patch.object(worker.subprocess, "run", return_value=completed) as run:
            result = worker.MysqlReadOnlyExecutor().execute("SELECT 1", 3.0)

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["MYSQL_DATABASE"], "sakila_dev")
        self.assertIn(str(worker.MYSQL_WRAPPER), command)
        self.assertIn("MAX_EXECUTION_TIME(3000)", command[-1])
        self.assertEqual(result.header, "id\tvalue")
        self.assertEqual(result.row_count, 2)
        self.assertEqual(
            result.result_sha256,
            hashlib.sha256(b"1\ta\n2\tb").hexdigest(),
        )

    def test_codex_sol_generates_structured_proposal_with_medium_effort(self) -> None:
        request = request_payload()
        proposed = PROPOSED_SQL

        def complete(
            command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess:
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(
                json.dumps(
                    {
                        "contract_version": "refactor_advisor_proposal.v1",
                        "proposed_sql": proposed,
                        "change_summary": (
                            "Substitui a correlação por agregação e janela cumulativa."
                        ),
                        "limitations": ["Validar somente no sakila_dev."],
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(worker.subprocess, "run", side_effect=complete) as run:
            proposal = worker.CodexSolRefactorAdvisor().propose(request)

        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-sol")
        self.assertIn('model_reasoning_effort="medium"', command)
        self.assertIn("--sandbox", command)
        self.assertEqual(proposal["proposed_sql"], proposed)

    def test_proposal_safety_rejects_mutation_and_multiple_statements(self) -> None:
        original = "SELECT customer_id FROM customer"
        for proposed, error in (
            ("DROP TABLE customer", "proposal_must_be_select"),
            (
                "SELECT customer_id FROM customer; DELETE FROM customer",
                "proposal_must_be_single_statement",
            ),
            (
                "SELECT customer_id FROM sakila.customer",
                "proposal_schema_not_allowed",
            ),
            (
                "SELECT customer_id FROM customer WHERE customer_id > 9000",
                "proposal_introduces_new_literal",
            ),
            (original, "proposal_unchanged"),
        ):
            with self.assertRaisesRegex(worker.RefactorAdvisorError, error):
                worker.validate_read_only_proposal(proposed, original)

    def test_proposal_rejects_runtime_validation_claims(self) -> None:
        proposal = {
            "contract_version": "refactor_advisor_proposal.v1",
            "proposed_sql": PROPOSED_SQL,
            "change_summary": "Substitui a correlação por uma janela cumulativa.",
            "limitations": ["A proposta ainda requer validação no laboratório."],
        }
        original = request_payload()["original_sql"]

        with self.assertRaisesRegex(
            worker.RefactorAdvisorError, "proposal_contains_runtime_claim"
        ):
            worker._validate_proposal(proposal, original)

    def test_pending_requests_skips_completed_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inbox = Path(temporary)
            pending = inbox / "a" / "request.json"
            completed = inbox / "b" / "request.json"
            pending.parent.mkdir()
            completed.parent.mkdir()
            pending.write_text("{}", encoding="utf-8")
            completed.write_text("{}", encoding="utf-8")
            (completed.parent / "state.json").write_text(
                json.dumps({"status": "completed"}), encoding="utf-8"
            )

            selected = worker.pending_requests(inbox)

        self.assertEqual(selected, [pending])

    def test_worker_validates_and_publishes_without_rerunning_sql(self) -> None:
        request = request_payload()
        analyzer = FakeAnalyzer()
        executor = FakeExecutor()
        publisher = FakePublisher()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "refactor"
            request_path = (
                root
                / "query_refactor"
                / "advisor"
                / "results"
                / "candidate"
                / "request.json"
            )
            request_path.parent.mkdir(parents=True)
            request_path.write_text(json.dumps(request), encoding="utf-8")
            contract = (
                REFACTOR_ROOT / "contracts" / "query_refactor_result.v1.schema.json"
            )
            with (
                patch.object(worker, "REFACTOR_ROOT", root),
                patch.object(worker, "RESULT_SCHEMA", contract),
            ):
                first = worker.process_request(
                    request_path,
                    analyzer=analyzer,
                    executor=executor,
                    publisher=publisher,
                )
                second = worker.process_request(
                    request_path,
                    analyzer=analyzer,
                    executor=executor,
                    publisher=publisher,
                )

            reports = list(request_path.parent.glob("report.md"))
            result = json.loads(
                (request_path.parent / "result.json").read_text(encoding="utf-8")
            )

        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(analyzer.calls, 1)
        self.assertEqual(executor.calls, 2)
        self.assertEqual(publisher.calls, 2)
        self.assertEqual(result["status"], "approved_lab")
        self.assertTrue(result["validation"]["equivalent"])
        self.assertEqual(len(reports), 1)

    def test_non_equivalent_result_requires_dba_validation(self) -> None:
        request = request_payload()
        first = worker.QueryExecution(0, 86.0, "a", 10, "1" * 64)
        second = worker.QueryExecution(0, 1.0, "a", 9, "2" * 64)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_path = root / "query_refactor/advisor/results/demo/original.sql"
            proposal_path = root / "query_refactor/advisor/results/demo/proposed.sql"
            report_path = root / "query_refactor/advisor/results/demo/report.md"
            with patch.object(worker, "REFACTOR_ROOT", root):
                result = worker.build_result(
                    request,
                    request["original_sql"],
                    "SELECT 1 FROM payment",
                    first,
                    second,
                    report_path,
                    original_path,
                    proposal_path,
                    "Troca a estratégia sem alterar o contrato do resultado.",
                    ["Validação limitada aos dados atuais do laboratório."],
                )

        self.assertEqual(result["status"], "requires_dba_validation")
        self.assertFalse(result["validation"]["equivalent"])


if __name__ == "__main__":
    unittest.main()
