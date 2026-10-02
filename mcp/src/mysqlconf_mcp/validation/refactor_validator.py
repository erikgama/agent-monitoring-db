"""Validation for the two query-refactoring workflow contracts."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


@lru_cache(maxsize=2)
def _validator(contract: str) -> Draft202012Validator:
    path = files("mysqlconf_mcp.contracts").joinpath(f"{contract}.schema.json")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _errors(payload: Any, contract: str) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return [{"field": "payload", "code": "payload_invalid"}]
    errors = []
    for error in sorted(
        _validator(contract).iter_errors(payload),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    ):
        path = ".".join(str(part) for part in error.absolute_path)
        errors.append(
            {
                "field": f"payload.{path}" if path else "payload",
                "code": "schema_validation_error",
            }
        )
    return errors


def validate_refactor_request(payload: Any) -> list[dict[str, str]]:
    errors = _errors(payload, "query_refactor_request.v1")
    if errors or not isinstance(payload, dict):
        return errors
    digest = hashlib.sha256(payload["original_sql"].encode("utf-8")).hexdigest()
    if digest != payload["original_sql_sha256"]:
        errors.append({"field": "payload.original_sql_sha256", "code": "hash_mismatch"})
    expected_dedupe = f"sakila:{payload['query_id']}:{payload['query_fingerprint']}"
    if payload["dedupe_key"] != expected_dedupe:
        errors.append({"field": "payload.dedupe_key", "code": "dedupe_key_mismatch"})
    report = payload["report"]
    if report["json"].get("snapshot_id") != report["snapshot_id"]:
        errors.append(
            {"field": "payload.report.snapshot_id", "code": "snapshot_mismatch"}
        )
    if report["snapshot_id"] not in report["html"]:
        errors.append(
            {"field": "payload.report.html", "code": "html_snapshot_mismatch"}
        )
    return errors


def validate_refactor_result(payload: Any) -> list[dict[str, str]]:
    errors = _errors(payload, "query_refactor_result.v1")
    if errors or not isinstance(payload, dict):
        return errors
    for field in ("original", "proposed"):
        digest = hashlib.sha256(payload[f"{field}_sql"].encode("utf-8")).hexdigest()
        if digest != payload[f"{field}_sql_sha256"]:
            errors.append(
                {"field": f"payload.{field}_sql_sha256", "code": "hash_mismatch"}
            )
    if payload["status"] == "approved_lab":
        if not payload["validation"]["equivalent"]:
            errors.append(
                {
                    "field": "payload.validation.equivalent",
                    "code": "approval_without_equivalence",
                }
            )
        if payload["timings"]["after_seconds"] >= payload["timings"]["before_seconds"]:
            errors.append({"field": "payload.timings", "code": "approval_without_gain"})
    return errors
