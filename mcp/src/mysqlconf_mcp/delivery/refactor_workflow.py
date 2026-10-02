"""Atomic file inboxes for the Refactor workflow."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}__[0-9a-f]{64}$")
_SAFE_RESULT_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}__[0-9a-f-]{36}$")


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=".record.", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_refactor_request(
    request: dict[str, Any], directory: Path | None = None
) -> dict[str, Any]:
    target = directory or (
        _root() / "agents" / "refactor" / "query_refactor" / "advisor" / "results"
    )
    fingerprint = request["query_fingerprint"].removeprefix("sha256:")
    record_id = f"{request['query_id']}__{fingerprint}"
    if not _SAFE_ID.fullmatch(record_id):
        raise ValueError("refactor_record_id_invalid")
    record = target.resolve() / record_id / "request.json"
    if record.is_file():
        return {
            "target": "refactor",
            "status": "duplicate",
            "recorded": False,
            "record_id": record_id,
        }
    _atomic_json(record, request)
    return {
        "target": "refactor",
        "status": "recorded",
        "recorded": True,
        "record_id": record_id,
    }


def record_dba_result(
    result: dict[str, Any], directory: Path | None = None
) -> dict[str, Any]:
    target = directory or (
        _root() / "agents" / "dba" / "refactor-results" / "runtime" / "inbox"
    )
    record_id = f"{result['query_id']}__{result['request_id']}"
    if not _SAFE_RESULT_ID.fullmatch(record_id):
        raise ValueError("dba_refactor_record_id_invalid")
    record = target.resolve() / record_id / "result.json"
    if record.is_file():
        return {
            "target": "dba",
            "status": "duplicate",
            "recorded": False,
            "record_id": record_id,
        }
    _atomic_json(record, result)
    return {
        "target": "dba",
        "status": "recorded",
        "recorded": True,
        "record_id": record_id,
    }
