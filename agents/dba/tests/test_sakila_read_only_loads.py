from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

DBA_DIR = Path(__file__).resolve().parents[1]
READ_LOAD_DIR = DBA_DIR / "load-tests" / "sakila-read-only"
sys.path.insert(0, str(READ_LOAD_DIR))

import sakila_read_common as common  # noqa: E402
import sakila_read_demo_35 as demo  # noqa: E402
import sakila_read_heavy as heavy  # noqa: E402
import sakila_read_steady as steady  # noqa: E402
from compare_read_reports import assess_demo, assess_demo_range, compare  # noqa: E402


class SakilaReadOnlyLoadTests(unittest.TestCase):
    def test_every_query_is_wrapped_as_read_only(self) -> None:
        for profile in (steady.QUERIES, heavy.QUERIES):
            for query in profile:
                with self.subTest(query=query.name):
                    sql = common.read_only_sql(query.build(7))
                    self.assertTrue(sql.startswith("USE sakila;"))
                    self.assertIn("START TRANSACTION READ ONLY;", sql)
                    self.assertTrue(sql.endswith("COMMIT;"))

    def test_query_catalog_has_unique_names(self) -> None:
        for profile in (steady.QUERIES, heavy.QUERIES):
            names = [query.name for query in profile]
            self.assertEqual(len(names), len(set(names)))

    def test_profiles_use_the_exact_same_query(self) -> None:
        self.assertEqual(len(steady.QUERIES), 1)
        self.assertEqual(len(heavy.QUERIES), 1)
        self.assertIs(steady.QUERIES[0], heavy.QUERIES[0])
        self.assertEqual(steady.QUERIES[0].build(0), heavy.QUERIES[0].build(0))

    def test_report_comparison_calculates_latency_difference(self) -> None:
        def report(profile: str, average: float, maximum: float) -> dict[str, object]:
            return {
                "started_at": "2030-01-01T00:00:00Z",
                "profile": profile,
                "results": {
                    "offered": 30,
                    "completed": 30,
                    "failed": 0,
                    "rejected": 0,
                    "completed_tps": 1.0,
                    "latency_avg_seconds": average,
                    "latency_max_seconds": maximum,
                    "per_query": {"actor_popularity": {"completed": 30}},
                },
            }

        result = compare(report("steady", 1.0, 2.0), report("heavy", 3.0, 8.0))
        self.assertEqual(result["difference"]["latency_avg_seconds"], 2.0)
        self.assertEqual(result["difference"]["latency_avg_percent"], 200.0)
        self.assertEqual(result["difference"]["latency_max_seconds"], 6.0)
        self.assertEqual(result["warnings"], [])

    def test_report_comparison_warns_about_censored_small_sample(self) -> None:
        def report(completed: int, failed: int) -> dict[str, object]:
            return {
                "started_at": "2030-01-01T00:00:00Z",
                "results": {
                    "offered": 100,
                    "completed": completed,
                    "failed": failed,
                    "rejected": 100 - completed - failed,
                    "completed_tps": 1.0,
                    "latency_avg_seconds": 2.0,
                    "latency_max_seconds": 3.0,
                    "per_query": {"actor_popularity": {"completed": completed}},
                },
            }

        result = compare(report(60, 0), report(2, 10))
        self.assertEqual(len(result["warnings"]), 2)

    def test_demo_requires_clean_samples_and_target_band(self) -> None:
        comparison = {
            "baseline": {
                "offered": 600,
                "completed": 600,
                "failed": 0,
                "rejected": 0,
                "retries": 0,
            },
            "loaded": {
                "offered": 654,
                "completed": 654,
                "failed": 0,
                "rejected": 0,
                "retries": 0,
            },
            "difference": {"latency_avg_percent": 35.2},
        }
        result = assess_demo(comparison, 35.0, 5.0, 300)
        self.assertTrue(result["passed"])

        comparison["loaded"]["failed"] = 1
        result = assess_demo(comparison, 35.0, 5.0, 300)
        self.assertFalse(result["passed"])

    def test_percentile_interpolates_latency_samples(self) -> None:
        self.assertEqual(common.percentile([1.0, 2.0, 3.0, 4.0], 0.50), 2.5)
        self.assertAlmostEqual(common.percentile([1.0, 2.0, 3.0, 4.0], 0.95), 3.85)

    def test_adaptive_tps_moves_toward_latency_target(self) -> None:
        self.assertEqual(demo.adjusted_tps(5.65, 35.0, 25.0, 5.3, 6.2, 0.01), 5.75)
        self.assertEqual(demo.adjusted_tps(5.65, 35.0, 45.0, 5.3, 6.2, 0.01), 5.55)
        self.assertEqual(demo.adjusted_tps(6.19, 35.0, 20.0, 5.3, 6.2, 0.01), 6.2)

    def test_adaptive_tps_interpolates_between_official_brackets(self) -> None:
        result = demo.interpolated_tps(
            (5.71, 21.049),
            (5.85, 65.293),
            42.5,
            5.3,
            6.2,
        )
        self.assertEqual(result, 5.778)

    def test_adaptive_demo_rejects_unclean_stage(self) -> None:
        report = {
            "results": {
                "offered": 300,
                "completed": 299,
                "failed": 1,
                "rejected": 0,
                "retries": 0,
            }
        }
        self.assertFalse(demo.is_clean(report))

    def test_demo_defaults_to_exactly_ten_warmup_queries(self) -> None:
        with patch.object(sys, "argv", ["sakila_read_demo_35.py"]):
            arguments = demo.parse_arguments()
        self.assertEqual(arguments.warmup_queries, 10)
        self.assertEqual(demo.warmup_duration_seconds(10, 5.0), 3)

        command = demo.build_stage_command(
            script=demo.STEADY,
            confirmation="READ_STEADY_SAKILA",
            tps=5.0,
            duration=3,
            workers=16,
            progress_interval=10,
            total_queries=arguments.warmup_queries,
        )
        position = command.index("--total-queries")
        self.assertEqual(command[position + 1], "10")

    def test_demo_range_accepts_35_through_50_percent(self) -> None:
        def comparison(observed: float) -> dict[str, object]:
            clean = {
                "offered": 300,
                "completed": 300,
                "failed": 0,
                "rejected": 0,
                "retries": 0,
            }
            return {
                "baseline": dict(clean),
                "loaded": dict(clean),
                "difference": {"latency_avg_percent": observed},
            }

        self.assertTrue(assess_demo_range(comparison(35.0), 35.0, 50.0, 300)["passed"])
        self.assertTrue(assess_demo_range(comparison(50.0), 35.0, 50.0, 300)["passed"])
        self.assertFalse(
            assess_demo_range(comparison(34.99), 35.0, 50.0, 300)["passed"]
        )
        self.assertFalse(
            assess_demo_range(comparison(50.01), 35.0, 50.0, 300)["passed"]
        )

    def test_guard_rejects_mutating_sql(self) -> None:
        for statement in (
            "INSERT INTO sakila.rental VALUES (1)",
            "UPDATE sakila.customer SET active = 0",
            "DELETE FROM sakila.payment",
            "ALTER TABLE sakila.film ADD COLUMN unsafe INT",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(ValueError):
                    common.read_only_sql(statement)

    def test_generated_queries_contain_no_mutating_keywords(self) -> None:
        forbidden = re.compile(
            r"\b(?:INSERT|UPDATE|DELETE|REPLACE|ALTER|CREATE|DROP|TRUNCATE|GRANT|REVOKE)\b",
            re.IGNORECASE,
        )
        for profile in (steady.QUERIES, heavy.QUERIES):
            for sequence in (0, 1, 7, 999):
                for query in profile:
                    with self.subTest(query=query.name, sequence=sequence):
                        self.assertIsNone(forbidden.search(query.build(sequence)))


if __name__ == "__main__":
    unittest.main()
