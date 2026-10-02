"""Private, minimal state for alert repetition suppression."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .cooldown import CooldownPolicy

STATE_FIELDS = {
    "dedupe_key",
    "last_severity",
    "last_sent_at",
    "last_audit_id",
    "occurrence_count",
}
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


@dataclass(slots=True)
class AlertState:
    dedupe_key: str
    last_severity: str
    last_sent_at: str
    last_audit_id: str
    occurrence_count: int


@dataclass(frozen=True, slots=True)
class StateDecision:
    publish: bool
    reason: str


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class AlertStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._records = self._load()

    def _load(self) -> dict[str, AlertState]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("alert_state_must_be_list")
        records: dict[str, AlertState] = {}
        for item in raw:
            if not isinstance(item, dict) or set(item) != STATE_FIELDS:
                raise ValueError("invalid_alert_state_record")
            state = AlertState(**item)
            records[state.dedupe_key] = state
        return records

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        payload = [asdict(self._records[key]) for key in sorted(self._records)]
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)

    def get(self, dedupe_key: str) -> AlertState | None:
        return self._records.get(dedupe_key)

    def decide(
        self,
        alert: dict[str, Any],
        now: datetime,
        cooldown: CooldownPolicy,
    ) -> StateDecision:
        current = self.get(alert["dedupe_key"])
        if current is None:
            return StateDecision(True, "new_condition")
        previous_rank = _SEVERITY_RANK[current.last_severity]
        current_rank = _SEVERITY_RANK[alert["severity"]]
        if current_rank > previous_rank:
            return StateDecision(True, "severity_escalated")
        elapsed = (
            now.astimezone(UTC) - _parse_utc(current.last_sent_at)
        ).total_seconds()
        if elapsed >= cooldown.seconds_for(alert["severity"]):
            return StateDecision(True, "cooldown_elapsed")
        return StateDecision(False, "cooldown_active")

    def mark_sent(self, alert: dict[str, Any], sent_at: str) -> None:
        previous = self.get(alert["dedupe_key"])
        self._records[alert["dedupe_key"]] = AlertState(
            dedupe_key=alert["dedupe_key"],
            last_severity=alert["severity"],
            last_sent_at=sent_at,
            last_audit_id=alert["audit_id"],
            occurrence_count=(previous.occurrence_count if previous else 0) + 1,
        )
        self._save()

    def mark_suppressed(self, alert: dict[str, Any]) -> None:
        current = self.get(alert["dedupe_key"])
        if current is None:
            raise ValueError("cannot_suppress_unknown_alert")
        current.last_severity = alert["severity"]
        current.last_audit_id = alert["audit_id"]
        current.occurrence_count += 1
        self._save()

    def resolve_missing(self, active_keys: set[str]) -> list[str]:
        resolved = sorted(set(self._records) - active_keys)
        if resolved:
            for key in resolved:
                del self._records[key]
            self._save()
        return resolved
