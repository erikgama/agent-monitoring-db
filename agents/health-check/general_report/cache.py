"""Validated latest-only cache with atomic replacement and rollback."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

VALID_OVERALL = {"healthy", "attention", "critical", "unknown"}
VALID_DOMAIN = VALID_OVERALL | {"not_available"}


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "audit_id",
        "started_at",
        "finished_at",
        "collected_at",
        "duration_ms",
        "target",
        "scope",
        "overall_status",
        "capabilities",
        "instance",
        "domains",
        "findings",
        "data_retention",
    }
    missing = required - set(snapshot)
    if missing:
        raise ValueError("report_missing_fields:" + ",".join(sorted(missing)))
    if snapshot["overall_status"] not in VALID_OVERALL:
        raise ValueError("invalid_overall_status")
    if not isinstance(snapshot["domains"], dict) or not snapshot["domains"]:
        raise ValueError("invalid_domains")
    scope = snapshot["scope"]
    if scope.get("schemas") != ["sakila"] or not isinstance(
        scope.get("domain_scopes"), dict
    ):
        raise ValueError("invalid_report_scope")
    instance_scope = (snapshot["instance"].get("scope") or {}).get("kind")
    if instance_scope != "instance":
        raise ValueError("invalid_instance_scope")
    for name, domain in snapshot["domains"].items():
        if not isinstance(name, str) or domain.get("status") not in VALID_DOMAIN:
            raise ValueError("invalid_domain_status")
        if (domain.get("scope") or {}).get("kind") not in {
            "schema",
            "mixed",
            "instance",
        }:
            raise ValueError("invalid_domain_scope")
    retention = snapshot["data_retention"]
    if retention.get("mode") != "latest_only":
        raise ValueError("invalid_retention_mode")
    if retention.get("raw_query_text_persisted") is not False:
        raise ValueError("raw_query_text_must_not_be_persisted")
    if retention.get("raw_logs_persisted") is not False:
        raise ValueError("raw_logs_must_not_be_persisted")


def _json_bytes(value: dict[str, Any]) -> bytes:
    text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return text.encode("utf-8")


def _stage(path: Path, content: bytes) -> Path:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        return
    rollback = path.with_name(path.name + ".rollback.tmp")
    with rollback.open("wb") as stream:
        stream.write(previous)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(rollback, path)


def replace_latest_snapshot(
    cache_dir: Path, snapshot: dict[str, Any], html_document: str
) -> None:
    validate_snapshot(snapshot)
    if snapshot["audit_id"] not in html_document:
        raise ValueError("html_report_audit_id_mismatch")
    if snapshot["collected_at"] not in html_document:
        raise ValueError("html_report_collected_at_mismatch")

    cache_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        cache_dir / "report.json": _json_bytes(snapshot),
        cache_dir / "report.html": html_document.encode("utf-8"),
    }
    previous = {path: path.read_bytes() if path.exists() else None for path in targets}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, content in targets.items():
            staged[path] = _stage(path, content)
        for path in targets:
            os.replace(staged[path], path)
            replaced.append(path)
        _fsync_directory(cache_dir)
    except Exception:
        for path in reversed(replaced):
            _restore(path, previous[path])
        _fsync_directory(cache_dir)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def read_latest_snapshot(cache_dir: Path) -> dict[str, Any]:
    snapshot = json.loads((cache_dir / "report.json").read_text(encoding="utf-8"))
    validate_snapshot(snapshot)
    return snapshot
