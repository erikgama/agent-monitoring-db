from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.normalizer import (
    auto_increment_rows,
    normalize_digest_rows,
    normalize_sys_rows,
    numeric_rows,
    safe_replication_rows,
    scalar,
)


class NormalizerTests(unittest.TestCase):
    def test_decimal_and_digest_normalization(self) -> None:
        self.assertEqual(scalar(Decimal("12")), 12)
        self.assertEqual(scalar(Decimal("1.25")), 1.25)
        result = normalize_digest_rows(
            [{"DIGEST": "abc", "DIGEST_TEXT": "SELECT  \n  ?", "executions": 2}]
        )
        self.assertEqual(result[0]["digest_text"], "SELECT ?")
        self.assertNotIn("query_sample_text", result[0])

    def test_auto_increment_unsigned_limit(self) -> None:
        result = auto_increment_rows(
            [
                {
                    "TABLE_SCHEMA": "sakila",
                    "TABLE_NAME": "example",
                    "AUTO_INCREMENT": 255,
                    "COLUMN_NAME": "id",
                    "DATA_TYPE": "tinyint",
                    "COLUMN_TYPE": "tinyint unsigned",
                }
            ]
        )
        self.assertEqual(result[0]["max_value"], 255)
        self.assertEqual(result[0]["usage_pct"], 100.0)

    def test_sys_latency_is_normalized_to_seconds(self) -> None:
        result = normalize_sys_rows(
            [{"statement_latency": "2.50 min", "lock_latency": "3.00 us"}]
        )
        self.assertEqual(result[0]["statement_latency_seconds"], 150.0)
        self.assertEqual(result[0]["lock_latency_seconds"], 0.000003)

    def test_numeric_strings_are_typed(self) -> None:
        result = numeric_rows([{"count": "42", "ratio": "99.25"}], {"count"}, {"ratio"})
        self.assertEqual(result, [{"count": 42, "ratio": 99.25}])

    def test_replication_host_is_masked(self) -> None:
        result = safe_replication_rows(
            [{"member_host": "192.0.2.10", "member_state": "ONLINE"}]
        )
        self.assertEqual(result[0]["member_host"], "masked")


if __name__ == "__main__":
    unittest.main()
