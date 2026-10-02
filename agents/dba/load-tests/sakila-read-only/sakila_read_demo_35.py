#!/usr/bin/env python3
"""Executa e calibra a demo read-only para +35% de latência média."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STEADY = HERE / "sakila_read_steady.py"
HEAVY = HERE / "sakila_read_heavy.py"

sys.path.insert(0, str(HERE))
from compare_read_reports import (  # noqa: E402
    assess_demo_range,
    compare,
    write_comparison,
)


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def measured_increase(baseline: dict[str, Any], loaded: dict[str, Any]) -> float:
    baseline_avg = float(baseline["results"]["latency_avg_seconds"])
    loaded_avg = float(loaded["results"]["latency_avg_seconds"])
    return (loaded_avg - baseline_avg) / baseline_avg * 100


def is_clean(report: dict[str, Any]) -> bool:
    results = report["results"]
    return (
        int(results["offered"]) == int(results["completed"])
        and int(results["failed"]) == 0
        and int(results["rejected"]) == 0
    )


def adjusted_tps(
    current_tps: float,
    target_percent: float,
    observed_percent: float,
    minimum_tps: float,
    maximum_tps: float,
    gain: float,
) -> float:
    proposed = current_tps + (target_percent - observed_percent) * gain
    return round(min(max(proposed, minimum_tps), maximum_tps), 3)


def interpolated_tps(
    low_point: tuple[float, float],
    high_point: tuple[float, float],
    target_percent: float,
    minimum_tps: float,
    maximum_tps: float,
) -> float | None:
    """Interpola TPS entre medições oficiais abaixo e acima da faixa."""
    low_tps, low_percent = low_point
    high_tps, high_percent = high_point
    if high_tps <= low_tps or high_percent <= low_percent:
        return None
    fraction = (target_percent - low_percent) / (high_percent - low_percent)
    proposed = low_tps + fraction * (high_tps - low_tps)
    return round(min(max(proposed, minimum_tps), maximum_tps), 3)


def run_stage(
    *,
    script: Path,
    confirmation: str,
    tps: float,
    duration: int,
    workers: int,
    progress_interval: int,
    total_queries: int | None = None,
) -> Path:
    command = build_stage_command(
        script=script,
        confirmation=confirmation,
        tps=tps,
        duration=duration,
        workers=workers,
        progress_interval=progress_interval,
        total_queries=total_queries,
    )
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        command,
        cwd=HERE.parents[3],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    json_path: Path | None = None
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        if line.startswith("json="):
            json_path = Path(line.removeprefix("json=").strip())
    return_code = process.wait()
    if return_code != 0 or json_path is None:
        raise RuntimeError(f"etapa falhou com codigo {return_code}")
    report = load_report(json_path)
    if not is_clean(report):
        raise RuntimeError(f"etapa nao ficou limpa: {json_path}")
    return json_path


def build_stage_command(
    *,
    script: Path,
    confirmation: str,
    tps: float,
    duration: int,
    workers: int,
    progress_interval: int,
    total_queries: int | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(script),
        "--execute",
        "--confirm-target",
        "sakila",
        "--confirm-read-only",
        confirmation,
        "--target-tps",
        str(tps),
        "--duration-seconds",
        str(duration),
        "--workers",
        str(workers),
        "--queue-size",
        "10000",
        "--query-timeout-seconds",
        "30",
        "--max-retries",
        "2",
        "--progress-interval-seconds",
        str(progress_interval),
        "--drain-queue",
    ]
    if total_queries is not None:
        command.extend(("--total-queries", str(total_queries)))
    return command


def warmup_duration_seconds(query_count: int, tps: float) -> int:
    """Margem de tempo; o volume do warm-up e limitado por total-queries."""
    return max(1, math.ceil(query_count / tps) + 1)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-target", choices=["sakila"])
    parser.add_argument("--confirm-demo", choices=["LATENCY_35_SAKILA"])
    parser.add_argument("--warmup-queries", type=int, default=10)
    parser.add_argument("--measurement-seconds", type=int, default=60)
    parser.add_argument("--calibration-seconds", type=int, default=20)
    parser.add_argument("--baseline-tps", type=float, default=5.0)
    parser.add_argument("--initial-loaded-tps", type=float, default=5.65)
    parser.add_argument("--minimum-loaded-tps", type=float, default=5.3)
    parser.add_argument("--maximum-loaded-tps", type=float, default=6.2)
    parser.add_argument("--target-increase-percent", type=float, default=35.0)
    parser.add_argument("--minimum-accepted-increase-percent", type=float, default=35.0)
    parser.add_argument("--maximum-accepted-increase-percent", type=float, default=50.0)
    parser.add_argument("--calibration-attempts", type=int, default=4)
    parser.add_argument("--official-attempts", type=int, default=6)
    parser.add_argument("--tps-gain-per-percentage-point", type=float, default=0.01)
    parser.add_argument("--minimum-completed", type=int, default=300)
    parser.add_argument("--progress-interval-seconds", type=int, default=10)
    arguments = parser.parse_args()
    positive_values = (
        arguments.warmup_queries,
        arguments.measurement_seconds,
        arguments.calibration_seconds,
        arguments.baseline_tps,
        arguments.initial_loaded_tps,
        arguments.target_increase_percent,
        arguments.minimum_accepted_increase_percent,
        arguments.maximum_accepted_increase_percent,
        arguments.calibration_attempts,
        arguments.official_attempts,
        arguments.tps_gain_per_percentage_point,
        arguments.minimum_completed,
    )
    if any(value <= 0 for value in positive_values):
        parser.error("duracoes, taxas, tentativas e tolerancias devem ser positivas")
    if arguments.minimum_loaded_tps >= arguments.maximum_loaded_tps:
        parser.error("minimum-loaded-tps deve ser menor que maximum-loaded-tps")
    if (
        arguments.minimum_accepted_increase_percent
        > arguments.maximum_accepted_increase_percent
    ):
        parser.error("minimum-accepted-increase-percent deve ser <= maximum")
    return arguments


def main() -> int:
    arguments = parse_arguments()
    if not arguments.execute:
        print("dry-run: nenhum acesso ao banco foi realizado")
        print(
            "fluxo=warmup->baseline->calibracao->carga_oficial->validacao "
            f"warmup_queries={arguments.warmup_queries} "
            f"accepted={arguments.minimum_accepted_increase_percent:g}%.."
            f"{arguments.maximum_accepted_increase_percent:g}%"
        )
        print(
            "para executar: --execute --confirm-target sakila "
            "--confirm-demo LATENCY_35_SAKILA"
        )
        return 0
    if (
        arguments.confirm_target != "sakila"
        or arguments.confirm_demo != "LATENCY_35_SAKILA"
    ):
        print(
            "erro: --execute exige --confirm-target sakila e "
            "--confirm-demo LATENCY_35_SAKILA"
        )
        return 2

    try:
        print(
            f"stage=warmup measured=false total_queries={arguments.warmup_queries}",
            flush=True,
        )
        warmup_path = run_stage(
            script=STEADY,
            confirmation="READ_STEADY_SAKILA",
            tps=arguments.baseline_tps,
            duration=warmup_duration_seconds(
                arguments.warmup_queries, arguments.baseline_tps
            ),
            workers=16,
            progress_interval=arguments.progress_interval_seconds,
            total_queries=arguments.warmup_queries,
        )
        print("stage=baseline measured=true", flush=True)
        baseline_path = run_stage(
            script=STEADY,
            confirmation="READ_STEADY_SAKILA",
            tps=arguments.baseline_tps,
            duration=arguments.measurement_seconds,
            workers=16,
            progress_interval=arguments.progress_interval_seconds,
        )
        baseline = load_report(baseline_path)
        baseline_avg = float(baseline["results"]["latency_avg_seconds"])
        target_seconds = baseline_avg * (1 + arguments.target_increase_percent / 100)
        print(
            f"baseline_avg={baseline_avg:.6f}s target_avg={target_seconds:.6f}s",
            flush=True,
        )

        candidate_tps = arguments.initial_loaded_tps
        calibration: list[dict[str, Any]] = []
        for attempt in range(1, arguments.calibration_attempts + 1):
            print(
                f"stage=calibration attempt={attempt} tps={candidate_tps:g}",
                flush=True,
            )
            trial_path = run_stage(
                script=HEAVY,
                confirmation="READ_HEAVY_SAKILA",
                tps=candidate_tps,
                duration=arguments.calibration_seconds,
                workers=20,
                progress_interval=arguments.progress_interval_seconds,
            )
            trial = load_report(trial_path)
            observed = measured_increase(baseline, trial)
            calibration.append(
                {
                    "attempt": attempt,
                    "tps": candidate_tps,
                    "increase_percent": observed,
                    "report": str(trial_path),
                }
            )
            print(f"calibration_increase={observed:.3f}%", flush=True)
            if (
                arguments.minimum_accepted_increase_percent
                <= observed
                <= arguments.maximum_accepted_increase_percent
            ):
                break
            next_tps = adjusted_tps(
                candidate_tps,
                arguments.target_increase_percent,
                observed,
                arguments.minimum_loaded_tps,
                arguments.maximum_loaded_tps,
                arguments.tps_gain_per_percentage_point,
            )
            if next_tps == candidate_tps:
                break
            candidate_tps = next_tps

        final_comparison: dict[str, Any] | None = None
        final_json: Path | None = None
        final_md: Path | None = None
        official: list[dict[str, Any]] = []
        low_point: tuple[float, float] | None = None
        high_point: tuple[float, float] | None = None
        for attempt in range(1, arguments.official_attempts + 1):
            print(
                f"stage=official_loaded attempt={attempt} tps={candidate_tps:g}",
                flush=True,
            )
            loaded_path = run_stage(
                script=HEAVY,
                confirmation="READ_HEAVY_SAKILA",
                tps=candidate_tps,
                duration=arguments.measurement_seconds,
                workers=20,
                progress_interval=arguments.progress_interval_seconds,
            )
            loaded = load_report(loaded_path)
            final_comparison = compare(baseline, loaded)
            final_comparison["demo_validation"] = assess_demo_range(
                final_comparison,
                arguments.minimum_accepted_increase_percent,
                arguments.maximum_accepted_increase_percent,
                arguments.minimum_completed,
            )
            observed = float(final_comparison["difference"]["latency_avg_percent"])
            official.append(
                {
                    "attempt": attempt,
                    "tps": candidate_tps,
                    "increase_percent": observed,
                    "report": str(loaded_path),
                }
            )
            final_comparison["demo_execution"] = {
                "warmup_report": str(warmup_path),
                "baseline_report": str(baseline_path),
                "calibration": calibration,
                "official_attempts": list(official),
            }
            final_json, final_md = write_comparison(final_comparison, HERE / "reports")
            print(
                f"official_increase={observed:.3f}% "
                f"passed={final_comparison['demo_validation']['passed']}",
                flush=True,
            )
            if final_comparison["demo_validation"]["passed"]:
                print(f"report={final_md}")
                print(f"json={final_json}")
                return 0
            if observed < arguments.minimum_accepted_increase_percent:
                if low_point is None or candidate_tps > low_point[0]:
                    low_point = (candidate_tps, observed)
            elif observed > arguments.maximum_accepted_increase_percent:
                if high_point is None or candidate_tps < high_point[0]:
                    high_point = (candidate_tps, observed)
            adjustment_target = (
                arguments.target_increase_percent
                if observed < arguments.minimum_accepted_increase_percent
                else (
                    arguments.minimum_accepted_increase_percent
                    + arguments.maximum_accepted_increase_percent
                )
                / 2
            )
            interpolated = (
                interpolated_tps(
                    low_point,
                    high_point,
                    adjustment_target,
                    arguments.minimum_loaded_tps,
                    arguments.maximum_loaded_tps,
                )
                if low_point is not None and high_point is not None
                else None
            )
            candidate_tps = interpolated or adjusted_tps(
                candidate_tps,
                adjustment_target,
                observed,
                arguments.minimum_loaded_tps,
                arguments.maximum_loaded_tps,
                arguments.tps_gain_per_percentage_point,
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"erro: {error}")
        return 2

    print(f"report={final_md}")
    print(f"json={final_json}")
    print("resultado=REPROVADO apos todas as tentativas")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
