"""Atomic JSON and HTML artifact publication."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_artifacts(
    output_dir: Path,
    report: dict[str, Any],
    html_document: str,
) -> None:
    if str(report.get("audit_id")) not in html_document:
        raise ValueError("artifact_audit_id_mismatch")
    if str(report.get("collected_at")) not in html_document:
        raise ValueError("artifact_collected_at_mismatch")
    _atomic_text(
        output_dir / "latest.json",
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_text(output_dir / "latest.html", html_document)
