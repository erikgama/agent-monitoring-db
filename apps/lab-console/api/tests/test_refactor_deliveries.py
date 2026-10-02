import json
from pathlib import Path

import pytest

from labconsole.refactor_deliveries import RefactorDeliveries

FINGERPRINT = "sha256:" + "b" * 64


def prepare_delivery(root: Path, *, with_runtime: bool = False) -> None:
    refactor = root / "agents/refactor"
    job = refactor / "query_refactor/advisor/results/example"
    job.mkdir(parents=True)
    (job / "original.sql").write_text("SELECT SLEEP(2), actor_id FROM sakila.actor;\n")
    (job / "proposed.sql").write_text("SELECT 0, actor_id FROM sakila.actor;\n")
    (job / "report.md").write_text(
        """# Handoff: Consulta de atores — primeira passada

- ID: `example`
- Data/hora: `2026-09-21T10:00:00-03:00`
- Origem: Refactor
- Destino: DBA
- Status: **APROVADA SOMENTE NO LABORATÓRIO sakila_dev**
- Escopo: remover espera artificial.

## SQL e mudança

- Original literal: `query_refactor/advisor/results/example/original.sql`
- Proposta canônica: `query_refactor/advisor/results/example/proposed.sql`
- Mudança única: remove a espera artificial.

## Evidências

- Execução serial final: original `2.500000 s`, proposta `0.250000 s`.
- Ganho de laboratório: `2.250000 s` (`90.000000%`, `10.000000x`).
"""
    )
    dba_record = (
        root / "agents/dba/refactor-results/runtime/inbox/example-result/result.json"
    )
    dba_record.parent.mkdir(parents=True)
    dba_record.write_text(
        json.dumps(
            {
                "request_id": "example",
                "query_fingerprint": FINGERPRINT,
            }
        )
    )
    if with_runtime:
        (job / "request.json").write_text(
            json.dumps(
                {
                    "request_id": "example",
                    "query_fingerprint": FINGERPRINT,
                }
            )
        )
        (job / "result.json").write_text("{}")
        state = (
            root
            / "agents/health-check/refactor_collector/results/runtime/refactor-state.json"
        )
        state.parent.mkdir(parents=True)
        state.write_text(
            json.dumps(
                {
                    "sent_fingerprints": {
                        FINGERPRINT: {
                            "query_id": "correlated_running_total",
                            "request_id": "example",
                        }
                    }
                }
            )
        )


def test_lists_refactor_handoff_with_sql_and_timings(tmp_path):
    prepare_delivery(tmp_path)

    deliveries = RefactorDeliveries(tmp_path).list()

    assert len(deliveries) == 1
    assert deliveries[0]["id"] == "example"
    assert deliveries[0]["before_seconds"] == 2.5
    assert deliveries[0]["after_seconds"] == 0.25
    assert deliveries[0]["production_ready"] is False
    assert "SLEEP(2)" in deliveries[0]["original_sql"]
    assert "SELECT 0" in deliveries[0]["proposed_sql"]


def test_ignores_report_without_refactor_to_dba_handoff(tmp_path):
    result = tmp_path / "agents/refactor/query_refactor/advisor/results/notes"
    result.mkdir(parents=True)
    (result / "report.md").write_text("# Notas internas\n")

    assert RefactorDeliveries(tmp_path).list() == []


def test_delete_removes_visible_delivery_and_dba_copy(tmp_path):
    prepare_delivery(tmp_path)
    service = RefactorDeliveries(tmp_path)

    result = service.delete("example")

    assert result == {"deleted": True, "id": "example"}
    assert service.list() == []
    job = tmp_path / "agents/refactor/query_refactor/advisor/results/example"
    assert not (job / "report.md").exists()
    assert not (job / "original.sql").exists()
    assert not (job / "proposed.sql").exists()
    assert not (
        tmp_path
        / "agents/dba/refactor-results/runtime/inbox/example-result/result.json"
    ).exists()
    with pytest.raises(ValueError, match="refactor_delivery_not_found"):
        service.delete("example")


def test_delete_releases_refactor_runtime_and_health_dedupe(tmp_path):
    prepare_delivery(tmp_path, with_runtime=True)
    service = RefactorDeliveries(tmp_path)

    service.delete("example")

    results = tmp_path / "agents/refactor/query_refactor/advisor/results"
    assert list(results.iterdir()) == []
    state = json.loads(
        (
            tmp_path
            / "agents/health-check/refactor_collector/results/runtime/refactor-state.json"
        ).read_text()
    )
    assert state == {"sent_fingerprints": {}}
