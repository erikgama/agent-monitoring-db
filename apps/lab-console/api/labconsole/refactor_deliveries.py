import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 500_000


class RefactorDeliveries:
    """Read and remove Refactor handoffs addressed to the DBA."""

    def __init__(self, repository: Path):
        self.repository = repository.resolve()
        self.root = (self.repository / "agents/refactor").resolve()
        self.results = self.root / "query_refactor/advisor/results"
        self.dba_inbox = (
            self.repository / "agents/dba/refactor-results/runtime/inbox"
        ).resolve()
        self.refactor_inbox = self.results.resolve()
        self.health_refactor_state = (
            self.repository
            / "agents/health-check/refactor_collector/results/runtime/refactor-state.json"
        ).resolve()

    @staticmethod
    def _capture(pattern: str, content: str, default: str = "") -> str:
        match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
        return match.group(1).strip() if match else default

    def _read(self, path: Path) -> str:
        resolved = path.resolve()
        if (
            not resolved.is_relative_to(self.root)
            or not resolved.is_file()
            or resolved.is_symlink()
            or resolved.stat().st_size > MAX_FILE_BYTES
        ):
            raise ValueError("refactor_artifact_unavailable")
        return resolved.read_text(encoding="utf-8")

    def _artifact(self, relative: str) -> tuple[str, str]:
        clean = relative.strip().strip("`")
        if not clean or Path(clean).is_absolute() or ".." in Path(clean).parts:
            raise ValueError("refactor_artifact_path_invalid")
        path = (self.root / clean).resolve()
        return clean, self._read(path)

    def _parse(self, report: Path) -> dict[str, Any] | None:
        content = self._read(report)
        if "Origem: Refactor" not in content or "Destino: DBA" not in content:
            return None

        identifier = self._capture(r"^- ID:\s*`?([^`\n]+)`?\s*$", content)
        original_ref = self._capture(r"^- Original literal:\s*`([^`]+)`\s*$", content)
        proposal_ref = self._capture(r"^- Proposta canônica:\s*`([^`]+)`\s*$", content)
        timing = re.search(
            r"original\s+`([0-9.]+)\s*s`,\s*proposta\s+`([0-9.]+)\s*s`",
            content,
            re.IGNORECASE,
        )
        if not identifier or not original_ref or not proposal_ref or not timing:
            return None

        original_path, original_sql = self._artifact(original_ref)
        proposal_path, proposed_sql = self._artifact(proposal_ref)
        title = self._capture(r"^# Handoff:\s*(.+)$", content, identifier)
        title = re.sub(r"\s+—\s+primeira passada\s*$", "", title).strip()
        status = self._capture(r"^- Status:\s*\*\*([^*]+)\*\*", content)
        change = self._capture(r"^- Mudança única:\s*(.+(?:\n\s{2,}.+)*)$", content)
        change = " ".join(change.split())
        gain = re.search(
            r"Ganho de laboratório:\s*`?([0-9.]+)\s*s`?\s*\(`?([0-9.]+)%`?,\s*`?([0-9.]+)x`?\)",
            content,
            re.IGNORECASE,
        )

        return {
            "id": identifier,
            "title": title,
            "created_at": self._capture(r"^- Data/hora:\s*`?([^`\n]+)`?\s*$", content),
            "status": status,
            "scope": self._capture(r"^- Escopo:\s*(.+)$", content),
            "change_summary": change,
            "before_seconds": float(timing.group(1)),
            "after_seconds": float(timing.group(2)),
            "saved_seconds": float(gain.group(1)) if gain else None,
            "improvement_percent": float(gain.group(2)) if gain else None,
            "speedup": float(gain.group(3)) if gain else None,
            "original_sql": original_sql,
            "proposed_sql": proposed_sql,
            "report_path": str(report.relative_to(self.repository)),
            "original_path": str(Path("agents/refactor") / original_path),
            "proposal_path": str(Path("agents/refactor") / proposal_path),
            "production_ready": False,
            "pipeline_mode": "simulation",
        }

    def list(self) -> list[dict[str, Any]]:
        if not self.results.is_dir() or self.results.is_symlink():
            return []
        deliveries = []
        for report in sorted(self.results.glob("*/report.md"), reverse=True):
            try:
                parsed = self._parse(report)
            except (OSError, UnicodeError, ValueError):
                continue
            if parsed:
                deliveries.append(parsed)
        return deliveries

    def _find(self, identifier: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", identifier):
            raise ValueError("refactor_delivery_not_found")
        for delivery in self.list():
            if delivery["id"] == identifier:
                return delivery
        raise ValueError("refactor_delivery_not_found")

    def _delete_file(self, relative: str, allowed_root: Path) -> None:
        target = (self.repository / relative).resolve()
        root = allowed_root.resolve()
        if (
            not target.is_relative_to(root)
            or target.is_symlink()
            or not target.is_file()
        ):
            raise ValueError("refactor_delivery_path_unsafe")
        target.unlink()
        parent = target.parent
        if parent != root:
            try:
                parent.rmdir()
            except OSError:
                pass

    def _delete_dba_copy(self, identifier: str) -> None:
        if not self.dba_inbox.is_dir() or self.dba_inbox.is_symlink():
            return
        for result in self.dba_inbox.glob("*/result.json"):
            record = result.parent
            try:
                if (
                    record.is_symlink()
                    or not record.resolve().is_relative_to(self.dba_inbox)
                    or result.is_symlink()
                    or result.stat().st_size > MAX_FILE_BYTES
                ):
                    continue
                payload = json.loads(result.read_text(encoding="utf-8"))
                if payload.get("request_id") == identifier:
                    shutil.rmtree(record)
            except (OSError, UnicodeError, ValueError, TypeError):
                continue

    def _refactor_request_record(
        self, identifier: str
    ) -> tuple[Path | None, str | None]:
        if not self.refactor_inbox.is_dir() or self.refactor_inbox.is_symlink():
            return None, None
        for request in self.refactor_inbox.glob("*/request.json"):
            record = request.parent
            try:
                if (
                    record.is_symlink()
                    or record.parent.resolve() != self.refactor_inbox
                    or request.is_symlink()
                    or request.stat().st_size > MAX_FILE_BYTES
                ):
                    continue
                payload = json.loads(request.read_text(encoding="utf-8"))
                fingerprint = payload.get("query_fingerprint")
                if (
                    payload.get("request_id") == identifier
                    and isinstance(fingerprint, str)
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint)
                ):
                    return record, fingerprint
            except (OSError, UnicodeError, ValueError, TypeError):
                continue
        return None, None

    def _fingerprint_from_health_state(self, identifier: str) -> str | None:
        state = self.health_refactor_state
        if not state.is_file() or state.is_symlink():
            return None
        try:
            if state.stat().st_size > MAX_FILE_BYTES:
                return None
            payload = json.loads(state.read_text(encoding="utf-8"))
            sent = payload.get("sent_fingerprints")
            if not isinstance(sent, dict):
                return None
            for fingerprint, metadata in sent.items():
                if (
                    isinstance(metadata, dict)
                    and metadata.get("request_id") == identifier
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint)
                ):
                    return fingerprint
        except (OSError, UnicodeError, ValueError, TypeError):
            return None
        return None

    def _delete_refactor_request_record(self, record: Path | None) -> None:
        if record is None:
            return
        if (
            record.is_symlink()
            or record.parent.resolve() != self.refactor_inbox
            or not record.is_dir()
        ):
            raise ValueError("refactor_delivery_path_unsafe")
        shutil.rmtree(record)

    def _release_health_dedupe(self, identifier: str, fingerprint: str | None) -> None:
        if fingerprint is None:
            return
        state = self.health_refactor_state
        if not state.is_file() or state.is_symlink():
            return
        if state.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("refactor_delivery_path_unsafe")
        payload = json.loads(state.read_text(encoding="utf-8"))
        sent = payload.get("sent_fingerprints")
        if not isinstance(sent, dict):
            raise ValueError("refactor_delivery_delete_failed")
        metadata = sent.get(fingerprint)
        if not isinstance(metadata, dict) or metadata.get("request_id") != identifier:
            return
        del sent[fingerprint]
        temporary = state.with_name(state.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, state)
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_latest(self, identifier: str) -> None:
        latest = self.results / "latest.json"
        if not latest.is_file() or latest.is_symlink():
            return
        payload = json.loads(latest.read_text(encoding="utf-8"))
        if payload.get("request_id") == identifier:
            latest.unlink()

    def delete(self, identifier: str) -> dict[str, Any]:
        """Remove one delivery and release its query for a future demo run."""
        delivery = self._find(identifier)
        request_record, fingerprint = self._refactor_request_record(identifier)
        fingerprint = fingerprint or self._fingerprint_from_health_state(identifier)
        try:
            self._delete_file(delivery["original_path"], self.results)
            self._delete_file(delivery["proposal_path"], self.results)
            self._delete_file(delivery["report_path"], self.results)
            self._delete_dba_copy(identifier)
            self._delete_refactor_request_record(request_record)
            self._release_health_dedupe(identifier, fingerprint)
            self._clear_latest(identifier)
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            raise ValueError("refactor_delivery_delete_failed") from error
        return {"deleted": True, "id": identifier}
