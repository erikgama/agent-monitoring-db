#!/usr/bin/env python3
"""Collect sanitized Slow Query Log evidence for refactoring triage."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.db import DatabaseRunner
from src.models import QueryResult, SourceStatus
from src.mysql_cli import MysqlCliConnection

BASE_DIR = Path(__file__).resolve().parent
HEALTH_CHECK_DIR = BASE_DIR.parent
SQL_FILE = "120_slow_query_refactor.sql"
SQL_DIR = BASE_DIR / "sql"
POLICY_PATH = BASE_DIR / "rules" / "collector-policy.json"
RESULTS_DIR = BASE_DIR / "results"
SCHEMA_VERSION = "slow_query_refactor_snapshot.v1"
CATALOG_PATH = BASE_DIR / "bad_queries_with_llm_refactor.sql"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?:--|#)[^\r\n]*")
_SINGLE_QUOTED = re.compile(r"'(?:''|\\.|[^'\\])*'")
_DOUBLE_QUOTED = re.compile(r'"(?:""|\\.|[^"\\])*"')
_HEX_LITERAL = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_NUMBER_LITERAL = re.compile(r"(?<![\w$])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?", re.I)
_SPACE = re.compile(r"\s+")
_QUERY_MARKER = re.compile(r"/\*\s*query:\s*([a-z0-9_]+)\s*\*/", re.I)
_MAX_EXECUTION_HINT = re.compile(r"/\*\+\s*MAX_EXECUTION_TIME\([^)]*\)\s*\*/", re.I)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"config_must_be_object:{path.name}")
    return value


def _policy() -> dict[str, Any]:
    value = _read_object(POLICY_PATH)
    if value.get("scope", {}).get("allowed_schemas") != ["sakila"]:
        raise ValueError("refactor_collector_scope_must_be_sakila_only")
    source = value.get("source", {})
    if source != {"schema": "mysql", "table": "slow_log"}:
        raise ValueError("refactor_collector_source_must_be_mysql_slow_log")
    retention = value.get("retention", {})
    if retention.get("mode") != "latest_only":
        raise ValueError("refactor_collector_retention_must_be_latest_only")
    if retention.get("raw_query_text_persisted") is not False:
        raise ValueError("raw_query_text_must_not_be_persisted")
    if retention.get("raw_logs_persisted") is not False:
        raise ValueError("raw_logs_must_not_be_persisted")
    collection = value.get("collection", {})
    lookback = collection.get("lookback_minutes")
    max_rows = collection.get("max_rows")
    if isinstance(lookback, bool) or not isinstance(lookback, int):
        raise ValueError("slow_log_lookback_minutes_must_be_integer")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int):
        raise ValueError("slow_log_max_rows_must_be_integer")
    if not 1 <= lookback <= 1440:
        raise ValueError("slow_log_lookback_minutes_out_of_range")
    if not 1 <= max_rows <= 100:
        raise ValueError("slow_log_max_rows_out_of_range")
    return value


def sanitize_query_template(value: Any) -> str | None:
    """Remove comments and literal values before a query may be persisted."""
    if value in (None, ""):
        return None
    text = str(value)
    text = _SINGLE_QUOTED.sub("?", text)
    text = _DOUBLE_QUOTED.sub("?", text)
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _LINE_COMMENT.sub(" ", text)
    text = _HEX_LITERAL.sub("?", text)
    text = _NUMBER_LITERAL.sub("?", text)
    text = _SPACE.sub(" ", text).strip().rstrip(";")
    return text[:4096] or None


def load_query_catalog(path: Path = CATALOG_PATH) -> dict[str, str]:
    """Read the versioned original queries without executing them."""
    content = path.read_text(encoding="utf-8")
    matches = list(_QUERY_MARKER.finditer(content))
    queries: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        query_id = match.group(1).lower()
        sql = content[match.end() : end].strip()
        sql = _MAX_EXECUTION_HINT.sub("", sql).strip()
        if not sql:
            raise ValueError(f"catalog_query_empty:{query_id}")
        if query_id in queries:
            raise ValueError(f"catalog_query_duplicated:{query_id}")
        queries[query_id] = sql
    if not queries:
        raise ValueError("catalog_queries_missing")
    return queries


def _fingerprint(template: str) -> str:
    return "sha256:" + hashlib.sha256(template.encode("utf-8")).hexdigest()


def _as_float(value: Any) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _enabled(value: Any) -> bool:
    return str(value).strip().upper() in {"1", "ON", "TRUE", "YES"}


def _normalize_queries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog = load_query_catalog()
    known_fingerprints: dict[str, str] = {}
    for query_id, original_sql in catalog.items():
        template = sanitize_query_template(original_sql)
        if template:
            known_fingerprints[_fingerprint(template)] = query_id
    queries: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("schema_name", "")).lower() != "sakila":
            continue
        template = sanitize_query_template(row.get("sql_text"))
        if not template or not re.match(r"^(?:SELECT|WITH)\b", template, re.I):
            continue
        fingerprint = _fingerprint(template)
        query_id = known_fingerprints.get(fingerprint)
        queries.append(
            {
                "query_fingerprint": fingerprint,
                "query_template": template,
                "query_id": query_id,
                "known_query": query_id is not None,
                "catalog_source": (
                    "agents/health-check/refactor_collector/"
                    "bad_queries_with_llm_refactor.sql"
                    if query_id is not None
                    else None
                ),
                "schema": "sakila",
                "started_at_mysql": str(row.get("started_at_mysql") or ""),
                "query_time_seconds": _as_float(row.get("query_time_seconds")),
                "lock_time_seconds": _as_float(row.get("lock_time_seconds")),
                "rows_sent": _as_int(row.get("rows_sent")),
                "rows_examined": _as_int(row.get("rows_examined")),
            }
        )
    return queries


def build_snapshot(
    settings_result: QueryResult,
    queries_result: QueryResult,
    *,
    policy: dict[str, Any],
    collected_at: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    limitations: list[str] = []
    settings_row = settings_result.rows[0] if settings_result.rows else {}
    slow_log_enabled = _enabled(settings_row.get("slow_query_log_enabled"))
    log_output = str(settings_row.get("log_output") or "")
    table_output = "TABLE" in {item.strip().upper() for item in log_output.split(",")}

    if settings_result.status != SourceStatus.AVAILABLE:
        limitations.append("slow_log_settings_not_available")
    elif not slow_log_enabled:
        limitations.append("slow_query_log_disabled")
    elif not table_output:
        limitations.append("slow_query_log_table_output_not_available")

    if queries_result.status != SourceStatus.AVAILABLE:
        limitations.append(queries_result.reason or "mysql_slow_log_not_available")

    queries = (
        _normalize_queries(queries_result.rows)
        if queries_result.status == SourceStatus.AVAILABLE
        else []
    )
    available = not limitations
    if not available:
        status = "not_available"
    elif queries:
        status = "attention"
    else:
        status = "healthy"
    collected = collected_at or _utc_now()
    identifier = snapshot_id or str(uuid.uuid4())
    collection = policy["collection"]

    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": identifier,
        "collected_at": collected,
        "status": status,
        "scope": {
            "schemas": ["sakila"],
            "source": "mysql.slow_log",
            "statement_types": ["SELECT", "WITH"],
        },
        "collection": {
            "lookback_minutes": collection["lookback_minutes"],
            "max_rows": collection["max_rows"],
            "returned_rows": len(queries),
        },
        "slow_log": {
            "enabled": slow_log_enabled,
            "table_output_available": table_output,
            "long_query_time_seconds": _as_float(
                settings_row.get("long_query_time_seconds")
            ),
        },
        "data_quality": {
            "complete": available,
            "limitations": limitations,
        },
        "queries": queries,
        "retention": dict(policy["retention"]),
    }


def render_html(snapshot: dict[str, Any]) -> str:
    escaped_snapshot_id = html.escape(snapshot["snapshot_id"])
    escaped_collected_at = html.escape(snapshot["collected_at"])
    cards: list[str] = []
    for query in snapshot["queries"]:
        cards.append(
            "<article class='query'>"
            f"<p class='time'>{query['query_time_seconds']:.6f} s</p>"
            f"<code>{html.escape(query['query_template'])}</code>"
            "<dl>"
            f"<dt>Linhas examinadas</dt><dd>{query['rows_examined']}</dd>"
            f"<dt>Linhas retornadas</dt><dd>{query['rows_sent']}</dd>"
            f"<dt>Lock</dt><dd>{query['lock_time_seconds']:.6f} s</dd>"
            "</dl></article>"
        )
    content = "".join(cards) or (
        "<p class='empty'>Nenhuma SELECT lenta do schema sakila foi retornada "
        "na janela consultada.</p>"
    )
    limitations = "".join(
        f"<li>{html.escape(item)}</li>"
        for item in snapshot["data_quality"]["limitations"]
    )
    limitations_block = (
        f"<section><h2>Limitações</h2><ul>{limitations}</ul></section>"
        if limitations
        else ""
    )
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Slow Query Log · candidatos a refatoração</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0; background: #090d10; color: #dce5eb; }}
    header, main {{ max-width: 1100px; margin: auto; padding: 24px; }}
    header {{ border-bottom: 1px solid #25343d; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ color: #72d7f2; }}
    .meta {{ color: #93a6b2; }}
    .query {{ margin: 18px 0; padding: 18px; border: 1px solid #28424f;
      border-radius: 10px; background: #10181d; }}
    .time {{ color: #f0d56a; font-weight: 700; }}
    code {{ display: block; white-space: pre-wrap; color: #8bdcf2; }}
    dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; }}
    dt {{ color: #93a6b2; }} dd {{ margin: 0; }}
    .empty {{ padding: 24px; border: 1px dashed #35505d; border-radius: 10px; }}
  </style>
</head>
<body>
  <header>
    <h1>Slow Query Log · candidatos a refatoração</h1>
    <p class="meta">
      Snapshot {escaped_snapshot_id} · {escaped_collected_at}
    </p>
    <p class="meta">
      Somente sakila · SQL literal e valores sensíveis não são persistidos
    </p>
  </header>
  <main>
    <h2>Consultas observadas</h2>
    {content}
    {limitations_block}
  </main>
</body>
</html>
"""


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def publish_snapshot(
    output_directory: Path, snapshot: dict[str, Any], html_document: str
) -> None:
    if snapshot["snapshot_id"] not in html_document:
        raise ValueError("html_snapshot_id_mismatch")
    if snapshot["collected_at"] not in html_document:
        raise ValueError("html_collected_at_mismatch")
    output_directory.mkdir(parents=True, exist_ok=True)
    targets = {
        output_directory / "latest.json": _json_bytes(snapshot),
        output_directory / "latest.html": html_document.encode("utf-8"),
    }
    staged: dict[Path, Path] = {}
    try:
        for path, content in targets.items():
            temporary = path.with_name(path.name + ".tmp")
            with temporary.open("wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            staged[path] = temporary
        for path, temporary in staged.items():
            os.replace(temporary, path)
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def collect_snapshot(
    existing_connection: Any,
    *,
    output_directory: Path = RESULTS_DIR,
) -> dict[str, Any]:
    policy = _policy()
    runtime_policy = _read_object(HEALTH_CHECK_DIR / "policy.json")
    timeout = int(runtime_policy["collection"]["query_timeout_seconds"])
    collection = policy["collection"]
    runner = DatabaseRunner(existing_connection, SQL_DIR, timeout)
    started = time.monotonic()
    try:
        runner.validate_all((SQL_FILE,))
        results = runner.execute_file(
            SQL_FILE,
            {
                "slow_log_lookback_minutes": collection["lookback_minutes"],
                "slow_log_limit": collection["max_rows"],
            },
        )
    finally:
        existing_connection.close()
    snapshot = build_snapshot(
        results["slow_log_settings"],
        results["slow_queries"],
        policy=policy,
    )
    snapshot["duration_ms"] = round((time.monotonic() - started) * 1000)
    publish_snapshot(output_directory, snapshot, render_html(snapshot))
    return snapshot


def _read_latest(output_directory: Path) -> dict[str, Any]:
    value = _read_object(output_directory / "latest.json")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected_refactor_snapshot_schema")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", default="collect", choices=("collect", "read-latest")
    )
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()
    try:
        if args.command == "collect":
            runtime_policy = _read_object(HEALTH_CHECK_DIR / "policy.json")
            connection = MysqlCliConnection.from_environment(
                int(runtime_policy["collection"]["query_timeout_seconds"])
            )
            snapshot = collect_snapshot(connection, output_directory=args.output_dir)
        else:
            snapshot = _read_latest(args.output_dir)
    except (OSError, RuntimeError, ValueError) as error:
        print(
            f"refactor_collector_failed:{type(error).__name__}",
            file=sys.stderr,
        )
        return 1
    json.dump(
        {
            "snapshot_id": snapshot["snapshot_id"],
            "collected_at": snapshot["collected_at"],
            "status": snapshot["status"],
            "returned_rows": snapshot["collection"]["returned_rows"],
        },
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
