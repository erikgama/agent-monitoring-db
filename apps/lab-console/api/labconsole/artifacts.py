import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_BYTES = 2_000_000
ROOTS = {
    "health": "agents/health-check/general_report/results",
    "latency": "agents/health-check/select_latency/results",
    "audit": "agents/audit/audit_security/results",
    "health-inbox": "agents/dba/health-check-alerts/runtime/inbox",
    "audit-inbox": "agents/dba/audit-security-alerts/runtime/inbox",
    "refactor": "agents/refactor/query_refactor/advisor/results",
}
FILES = {
    "latest.json",
    "latest.html",
    "report.json",
    "report.html",
    "alert.json",
    "alert-summary.json",
    "audit-event-summary.json",
}
PRIVATE = re.compile(
    r'(?i)(?:-----BEGIN .*PRIVATE KEY|smtp_password|"(?:password|token|secret|recipients)"\s*:|\b\d{1,3}(?:\.\d{1,3}){3}\b|[\w.+-]+@[\w.-]+\.[a-z]{2,})'
)


def _mysql_principals(data: Any) -> set[str]:
    """Return technical MySQL identities that are safe report evidence.

    Performance Schema represents an authenticated account as ``user@host``.
    That shape also looks like an e-mail address to the generic secret filter,
    so only values explicitly stored in a structured ``user`` field are
    exempted while the artifact is inspected.
    """
    principals: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if (
                key == "user"
                and isinstance(value, str)
                and value.count("@") == 1
                and not any(character.isspace() for character in value)
            ):
                principals.add(value)
            else:
                principals.update(_mysql_principals(value))
    elif isinstance(data, list):
        for value in data:
            principals.update(_mysql_principals(value))
    return principals


def _contains_private(content: str, data: Any) -> bool:
    inspected = content
    for principal in _mysql_principals(data):
        inspected = inspected.replace(principal, "[mysql-principal]")
    return bool(PRIVATE.search(inspected))


def safe_read(root: Path, path: Path) -> bytes:
    """Walk every component using directory descriptors; disallow symlinks/races."""
    relative = path.relative_to(root)
    if any(p in {"..", "."} for p in relative.parts):
        raise ValueError("path_not_allowed")
    descriptors: list[int] = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        for part in relative.parts[:-1]:
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            descriptors.append(directory)
        fd = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("artifact_size_or_type")
        chunks = bytearray()
        while len(chunks) <= MAX_BYTES:
            chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > MAX_BYTES:
            raise ValueError("artifact_too_large")
        return bytes(chunks)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


class Catalog:
    def __init__(self, repository: Path):
        self.repository = repository.resolve()
        self.index: dict[str, tuple[Path, Path, str]] = {}

    def list(self) -> list[dict[str, Any]]:
        self.index.clear()
        result = []
        for label, relative in ROOTS.items():
            root = self.repository / relative
            if not root.is_dir() or root.is_symlink():
                continue
            paths = (
                list(root.glob("*/*.json"))
                if "inbox" in label
                else list(root.glob("*.json"))
            )
            for path in sorted(paths, reverse=True)[:200]:
                if path.name not in FILES:
                    continue
                try:
                    raw = safe_read(self.repository, path)
                    content = raw.decode()
                    data = json.loads(content)
                    if _contains_private(content, data):
                        continue
                    audit_id = data.get("audit_id", data.get("source_audit_id"))
                    stamp = data.get(
                        "collected_at",
                        data.get("detected_at", data.get("generated_at")),
                    )
                    if not audit_id:
                        continue
                    pair = (
                        path.with_suffix(".html")
                        if path.name in {"latest.json", "report.json"}
                        else None
                    )
                    html_ok = False
                    html_hash = None
                    if pair and pair.is_file():
                        html_bytes = safe_read(self.repository, pair)
                        html = html_bytes.decode()
                        html_ok = (
                            str(audit_id) in html
                            and bool(stamp)
                            and str(stamp) in html
                            and not _contains_private(html, data)
                        )
                        if html_ok:
                            html_hash = hashlib.sha256(html_bytes).hexdigest()
                    identifier = hashlib.sha256(
                        f"{label}/{path.relative_to(root)}".encode()
                    ).hexdigest()
                    sha = hashlib.sha256(raw).hexdigest()
                    self.index[identifier] = (root, path, sha)
                    result.append(
                        {
                            "id": identifier,
                            "source": "health-check"
                            if label in {"health", "latency", "health-inbox"}
                            else "refactor"
                            if label == "refactor"
                            else "audit",
                            "location": label,
                            "name": f"{label} · {path.name}",
                            "audit_id": audit_id,
                            "alert_id": data.get("alert_id"),
                            "category": data.get(
                                "category",
                                "query_latency" if label == "latency" else "report",
                            ),
                            "severity": data.get(
                                "severity", data.get("overall_status", "info")
                            ),
                            "timestamp": stamp,
                            "retention": "historical"
                            if "inbox" in label
                            else "latest-only",
                            "html_available": bool(html_ok),
                            "sha256": sha,
                            "html_sha256": html_hash,
                            "size": len(raw),
                            "schema_version": data.get(
                                "schema_version", data.get("contract_version")
                            ),
                        }
                    )
                except (OSError, ValueError, UnicodeError, AttributeError):
                    continue
        return result

    def read(self, identifier: str, format: str, expected_sha: str) -> str:
        self.list()
        if identifier not in self.index or format not in {"json", "html"}:
            raise ValueError("artifact_not_available")
        root, path, current = self.index[identifier]
        if current != expected_sha:
            raise ValueError("artifact_replaced")
        raw = safe_read(self.repository, path)
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError("artifact_replaced")
        data = json.loads(raw)
        if format == "html":
            if path.name not in {"latest.json", "report.json"}:
                raise ValueError("html_not_retained")
            raw = safe_read(self.repository, path.with_suffix(".html"))
            html = raw.decode()
            stamp = data.get(
                "collected_at", data.get("detected_at", data.get("generated_at"))
            )
            if (
                not stamp
                or str(data.get("audit_id", data.get("source_audit_id"))) not in html
                or str(stamp) not in html
            ):
                raise ValueError("artifact_identity_mismatch")
        content = raw.decode()
        if _contains_private(content, data):
            raise ValueError("sensitive_artifact_blocked")
        return content
