"""Static guardrails for every SQL block used by the Audit collector."""

from __future__ import annotations

import re

_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*|#[^\n]*", re.DOTALL)
_QUOTED = re.compile(r"'(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"")
_FORBIDDEN = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|RENAME|"
    r"GRANT|REVOKE|CALL|DO|HANDLER|LOAD\s+DATA|INTO\s+(?:OUTFILE|DUMPFILE)|"
    r"SET\s+(?:GLOBAL|PERSIST|PERSIST_ONLY|SESSION)|KILL|FLUSH|RESET|"
    r"AUDIT_LOG_FILTER_SET_FILTER|AUDIT_LOG_FILTER_SET_USER|"
    r"AUDIT_LOG_FILTER_REMOVE_FILTER|AUDIT_LOG_FILTER_REMOVE_USER)\b",
    re.IGNORECASE,
)


class UnsafeSqlError(ValueError):
    """Raised before MySQL is contacted when a block is not read-only."""


def validate_read_only_sql(sql: str) -> None:
    cleaned = _COMMENTS.sub("", sql).strip()
    if not cleaned:
        raise UnsafeSqlError("empty_sql")
    token_stream = _QUOTED.sub("''", cleaned)
    if _FORBIDDEN.search(token_stream):
        raise UnsafeSqlError("forbidden_sql_token")
    statements = [part.strip() for part in cleaned.split(";") if part.strip()]
    for statement in statements:
        upper = statement.upper()
        if re.match(r"(?:SELECT|WITH)\b", upper):
            continue
        if re.match(r"SET\s+@[A-Z0-9_]+\s*:?=", upper):
            continue
        raise UnsafeSqlError("statement_not_allowlisted")
