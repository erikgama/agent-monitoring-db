from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

NOTIFICATION_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = NOTIFICATION_ROOT.parents[1]
CONTRACTS = NOTIFICATION_ROOT / "contracts"
PROVENANCE = CONTRACTS / "provenance.json"


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class ContractSyncTests(unittest.TestCase):
    def test_packaged_schema_matches_health_check_canonical_schema(self) -> None:
        self.assert_contract_sync(
            "health_check_alert.v1",
            REPOSITORY_ROOT
            / "agents"
            / "health-check"
            / "contracts"
            / "health_check_alert.v1.schema.json",
        )

    def test_packaged_schema_matches_audit_canonical_schema(self) -> None:
        self.assert_contract_sync(
            "audit_security_alert.v1",
            REPOSITORY_ROOT
            / "agents"
            / "audit"
            / "contracts"
            / "audit_security_alert.v1.schema.json",
        )

    def test_packaged_schema_matches_refactor_canonical_schema(self) -> None:
        self.assert_contract_sync(
            "query_refactor_result.v1",
            REPOSITORY_ROOT
            / "agents"
            / "refactor"
            / "contracts"
            / "query_refactor_result.v1.schema.json",
        )

    def assert_contract_sync(self, version: str, canonical_path: Path) -> None:
        canonical = canonical_path.read_bytes()
        packaged = (CONTRACTS / f"{version}.schema.json").read_bytes()
        provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
        metadata = provenance["contracts"][version]

        self.assertEqual(packaged, canonical)
        self.assertEqual(sha256(canonical), metadata["canonical_sha256"])
        self.assertEqual(sha256(packaged), metadata["canonical_sha256"])


if __name__ == "__main__":
    unittest.main()
