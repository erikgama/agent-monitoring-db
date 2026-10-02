#!/usr/bin/env python3
"""Carga concorrente read-only da mesma consulta canônica no Sakila."""

from __future__ import annotations

from sakila_read_common import CANONICAL_QUERY, run_profile

CONFIRMATION = "READ_HEAVY_SAKILA"
QUERIES = (CANONICAL_QUERY,)


if __name__ == "__main__":
    raise SystemExit(
        run_profile(
            profile="heavy",
            description=__doc__ or "Carga pesada de leitura no Sakila",
            confirmation=CONFIRMATION,
            queries=QUERIES,
            default_duration=60,
            default_tps=5.65,
            default_workers=20,
        )
    )
