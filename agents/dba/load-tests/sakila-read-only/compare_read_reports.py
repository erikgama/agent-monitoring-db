#!/usr/bin/env python3
"""Compara latência e throughput de dois relatórios read-only locais."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("results"), dict):
        raise ValueError(f"relatorio invalido: {path}")
    return data


def percentage_change(baseline: float, loaded: float) -> float | None:
    if baseline == 0:
        return None
    return round((loaded - baseline) / baseline * 100, 3)


def latency_values(results: dict[str, Any]) -> dict[str, float | None]:
    return {
        name: (float(results[key]) if results.get(key) is not None else None)
        for name, key in (
            ("avg", "latency_avg_seconds"),
            ("p50", "latency_p50_seconds"),
            ("p95", "latency_p95_seconds"),
            ("p99", "latency_p99_seconds"),
            ("max", "latency_max_seconds"),
        )
    }


def compare(baseline: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    baseline_results = baseline["results"]
    loaded_results = loaded["results"]
    baseline_queries = set(baseline_results.get("per_query", {}))
    loaded_queries = set(loaded_results.get("per_query", {}))
    if baseline_queries != loaded_queries or len(baseline_queries) != 1:
        raise ValueError("relatorios devem conter exatamente a mesma consulta")
    query_name = next(iter(baseline_queries))
    baseline_latency = latency_values(baseline_results)
    loaded_latency = latency_values(loaded_results)
    differences: dict[str, float | None] = {}
    for name in baseline_latency:
        baseline_value = baseline_latency[name]
        loaded_value = loaded_latency[name]
        if baseline_value is None or loaded_value is None:
            continue
        differences[f"latency_{name}_seconds"] = round(loaded_value - baseline_value, 6)
        differences[f"latency_{name}_percent"] = percentage_change(
            baseline_value, loaded_value
        )
    warnings: list[str] = []
    if int(loaded_results["failed"]):
        warnings.append(
            "A latencia pesada considera apenas consultas concluidas; falhas por timeout "
            "ficaram fora da media."
        )
    if int(loaded_results["completed"]) < 30:
        warnings.append(
            "A amostra pesada concluida tem menos de 30 observacoes; interprete a media "
            "como evidencia de saturacao, nao como estimativa estatistica estavel."
        )
    return {
        "query_name": query_name,
        "baseline": {
            "report_started_at": baseline["started_at"],
            "offered": int(baseline_results["offered"]),
            "completed": int(baseline_results["completed"]),
            "failed": int(baseline_results["failed"]),
            "rejected": int(baseline_results["rejected"]),
            "retries": int(baseline_results.get("retries", 0)),
            "completion_percent": round(
                int(baseline_results["completed"])
                / max(int(baseline_results["offered"]), 1)
                * 100,
                3,
            ),
            **{
                f"latency_{name}_seconds": value
                for name, value in baseline_latency.items()
            },
            "completed_tps": float(baseline_results["completed_tps"]),
        },
        "loaded": {
            "report_started_at": loaded["started_at"],
            "offered": int(loaded_results["offered"]),
            "completed": int(loaded_results["completed"]),
            "failed": int(loaded_results["failed"]),
            "rejected": int(loaded_results["rejected"]),
            "retries": int(loaded_results.get("retries", 0)),
            "completion_percent": round(
                int(loaded_results["completed"])
                / max(int(loaded_results["offered"]), 1)
                * 100,
                3,
            ),
            **{
                f"latency_{name}_seconds": value
                for name, value in loaded_latency.items()
            },
            "completed_tps": float(loaded_results["completed_tps"]),
        },
        "difference": differences,
        "warnings": warnings,
    }


def assess_demo(
    comparison: dict[str, Any],
    target_percent: float,
    tolerance_points: float,
    minimum_completed: int,
) -> dict[str, Any]:
    baseline = comparison["baseline"]
    loaded = comparison["loaded"]
    observed = comparison["difference"]["latency_avg_percent"]
    lower = target_percent - tolerance_points
    upper = target_percent + tolerance_points
    clean = all(
        sample["offered"] == sample["completed"]
        and sample["failed"] == 0
        and sample["rejected"] == 0
        and sample["retries"] == 0
        for sample in (baseline, loaded)
    )
    enough_samples = all(
        sample["completed"] >= minimum_completed for sample in (baseline, loaded)
    )
    within_target = observed is not None and lower <= observed <= upper
    return {
        "target_latency_avg_percent": target_percent,
        "tolerance_percentage_points": tolerance_points,
        "accepted_range_percent": [round(lower, 3), round(upper, 3)],
        "minimum_completed_per_load": minimum_completed,
        "observed_latency_avg_percent": observed,
        "checks": {
            "zero_errors_rejections_and_retries": clean,
            "minimum_samples": enough_samples,
            "latency_target": within_target,
        },
        "passed": clean and enough_samples and within_target,
    }


def assess_demo_range(
    comparison: dict[str, Any],
    minimum_percent: float,
    maximum_percent: float,
    minimum_completed: int,
) -> dict[str, Any]:
    baseline = comparison["baseline"]
    loaded = comparison["loaded"]
    observed = comparison["difference"]["latency_avg_percent"]
    clean = all(
        sample["offered"] == sample["completed"]
        and sample["failed"] == 0
        and sample["rejected"] == 0
        and sample["retries"] == 0
        for sample in (baseline, loaded)
    )
    enough_samples = all(
        sample["completed"] >= minimum_completed for sample in (baseline, loaded)
    )
    within_target = (
        observed is not None and minimum_percent <= observed <= maximum_percent
    )
    return {
        "minimum_latency_avg_percent": minimum_percent,
        "maximum_latency_avg_percent": maximum_percent,
        "accepted_range_percent": [minimum_percent, maximum_percent],
        "minimum_completed_per_load": minimum_completed,
        "observed_latency_avg_percent": observed,
        "checks": {
            "zero_errors_rejections_and_retries": clean,
            "minimum_samples": enough_samples,
            "latency_target": within_target,
        },
        "passed": clean and enough_samples and within_target,
    }


def write_comparison(comparison: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    json_path = output_dir / f"sakila-read-comparison-{stamp}.json"
    md_path = output_dir / f"sakila-read-comparison-{stamp}.md"
    json_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    baseline = comparison["baseline"]
    loaded = comparison["loaded"]
    difference = comparison["difference"]
    warning_lines = [f"- Ressalva: {warning}" for warning in comparison["warnings"]]
    percentile_lines: list[str] = []
    for name in ("p50", "p95", "p99"):
        if baseline.get(f"latency_{name}_seconds") is not None:
            percentile_lines.append(
                f"- {name.upper()}: `{baseline[f'latency_{name}_seconds']}` s -> "
                f"`{loaded[f'latency_{name}_seconds']}` s "
                f"(`{difference[f'latency_{name}_percent']}%`)"
            )
    validation = comparison.get("demo_validation")
    validation_lines = []
    if validation:
        if "target_latency_avg_percent" in validation:
            target_line = (
                f"- Meta da média: `{validation['target_latency_avg_percent']}%`"
            )
        else:
            target_line = (
                f"- Meta da média: entre `{validation['minimum_latency_avg_percent']}%` e "
                f"`{validation['maximum_latency_avg_percent']}%`"
            )
        validation_lines = [
            "## Validação da demo",
            "",
            target_line,
            f"- Faixa aceita: `{validation['accepted_range_percent'][0]}%` a "
            f"`{validation['accepted_range_percent'][1]}%`",
            f"- Resultado: `{'APROVADO' if validation['passed'] else 'REPROVADO'}`",
            f"- Zero erros/rejeições/retries: "
            f"`{validation['checks']['zero_errors_rejections_and_retries']}`",
            f"- Amostras mínimas: `{validation['checks']['minimum_samples']}`",
            "",
        ]
    md_path.write_text(
        "\n".join(
            [
                "# Comparação de latência read-only",
                "",
                f"- Query: `{comparison['query_name']}`",
                f"- Baseline média: `{baseline['latency_avg_seconds']}` s",
                f"- Carga média: `{loaded['latency_avg_seconds']}` s",
                f"- Diferença média: `{difference['latency_avg_seconds']}` s "
                f"(`{difference['latency_avg_percent']}%`)",
                *percentile_lines,
                f"- Baseline máxima: `{baseline['latency_max_seconds']}` s",
                f"- Carga máxima: `{loaded['latency_max_seconds']}` s",
                f"- Diferença máxima: `{difference['latency_max_seconds']}` s "
                f"(`{difference['latency_max_percent']}%`)",
                f"- Baseline concluídas: `{baseline['completed']}`",
                f"- Carga concluídas: `{loaded['completed']}`",
                f"- Carga rejeitadas: `{loaded['rejected']}`",
                f"- Carga falhas: `{loaded['failed']}`",
                "",
                *warning_lines,
                "",
                *validation_lines,
            ]
        ),
        encoding="utf-8",
    )
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("loaded", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "reports",
    )
    parser.add_argument("--target-increase-percent", type=float)
    parser.add_argument("--minimum-increase-percent", type=float)
    parser.add_argument("--maximum-increase-percent", type=float)
    parser.add_argument("--tolerance-percentage-points", type=float, default=5.0)
    parser.add_argument("--minimum-completed", type=int, default=300)
    arguments = parser.parse_args()
    if (arguments.minimum_increase_percent is None) != (
        arguments.maximum_increase_percent is None
    ):
        parser.error(
            "minimum-increase-percent e maximum-increase-percent devem ser usados juntos"
        )
    if (
        arguments.minimum_increase_percent is not None
        and arguments.minimum_increase_percent > arguments.maximum_increase_percent
    ):
        parser.error("minimum-increase-percent deve ser menor ou igual ao maximum")
    comparison = compare(load_report(arguments.baseline), load_report(arguments.loaded))
    if arguments.minimum_increase_percent is not None:
        comparison["demo_validation"] = assess_demo_range(
            comparison,
            arguments.minimum_increase_percent,
            arguments.maximum_increase_percent,
            arguments.minimum_completed,
        )
    elif arguments.target_increase_percent is not None:
        comparison["demo_validation"] = assess_demo(
            comparison,
            arguments.target_increase_percent,
            arguments.tolerance_percentage_points,
            arguments.minimum_completed,
        )
    json_path, md_path = write_comparison(comparison, arguments.output_dir)
    print(f"report={md_path}")
    print(f"json={json_path}")
    validation = comparison.get("demo_validation")
    return 0 if not validation or validation["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
