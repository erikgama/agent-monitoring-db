import getpass
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 600_000
    ).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, _ = encoded.split("$")
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except ValueError:
        return False


def password_cli() -> None:
    print(password_hash(getpass.getpass("Senha do usuário: ")))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Signer:
    """Per-connection, directional, expiring envelopes with replay protection."""

    def __init__(self, key: str, audience: str):
        if len(key) < 32:
            raise ValueError("runner_key_too_short")
        self.key, self.audience = key.encode(), audience
        self.seen: dict[str, float] = {}

    def sign(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "nonce": secrets.token_hex(16),
            "expires": time.time() + 30,
            "aud": self.audience,
            "payload": payload,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return {
            "body": body,
            "signature": hmac.new(self.key, raw.encode(), "sha256").hexdigest(),
        }

    def verify(self, envelope: dict[str, Any]) -> dict[str, Any]:
        body = envelope.get("body", {})
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(self.key, raw.encode(), "sha256").hexdigest()
        if not hmac.compare_digest(signature, str(envelope.get("signature", ""))):
            raise ValueError("bad_signature")
        current = time.time()
        self.seen = {k: v for k, v in self.seen.items() if v > current}
        if (
            body.get("aud") != self.audience
            or not current < body.get("expires", 0) <= current + 35
        ):
            raise ValueError("expired_envelope")
        nonce = body.get("nonce")
        if not isinstance(nonce, str) or nonce in self.seen:
            raise ValueError("replayed_envelope")
        self.seen[nonce] = body["expires"]
        return dict(body["payload"])


SAFE_KEYS = frozenset(
    {
        "status",
        "action",
        "category",
        "severity",
        "alert_id",
        "audit_id",
        "decision",
        "delivery_status",
        "dba_status",
        "mcp_status",
        "accepted",
        "published_to_mcp",
        "delivered",
        "dba_recorded",
        "error_code",
        "mysql_errno",
        "count",
        "total",
        "pid",
        "exit_code",
        "reason",
        "actor",
        "mode",
        "artifact_id",
        "approval_id",
        "sql_hash",
    }
)
SAFE_STRING = re.compile(r"^[a-zA-Z0-9_.:-]{1,160}$")


def sanitize(payload: dict[str, Any]) -> dict[str, Any]:
    """Positive allowlist: arbitrary stdout, SQL and secrets never cross the wire."""
    return {
        k: v
        for k, v in payload.items()
        if k in SAFE_KEYS
        and (
            v is None
            or isinstance(v, bool | int | float)
            or isinstance(v, str)
            and SAFE_STRING.fullmatch(v)
        )
    }
