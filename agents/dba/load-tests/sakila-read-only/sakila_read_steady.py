#!/usr/bin/env python3
"""Baseline read-only da consulta canônica durante um minuto no Sakila."""

from __future__ import annotations

from sakila_read_common import CANONICAL_QUERY, run_profile

CONFIRMATION = "READ_STEADY_SAKILA"
QUERIES = (CANONICAL_QUERY,)


if __name__ == "__main__":
    raise SystemExit(
        run_profile(
            profile="steady",
            description=__doc__ or "Baseline de leitura no Sakila",
            confirmation=CONFIRMATION,
            queries=QUERIES,
            default_duration=60,
            default_tps=5.0,
            default_workers=16,
        )
    )
