#!/usr/bin/env python3
"""Real LLM checks with synthetic fixtures; no MySQL, MCP publication or SMTP."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    (
        "health-check",
        "agents/health-check",
        """
        import sys
        sys.path.insert(0, 'tests')
        from test_advisor import report
        from advisor.agent import CodexLunaAnalyzer, build_analysis_payload
        from select_latency.collector import render_select_latency_html
        html = render_select_latency_html(report())
        result = CodexLunaAnalyzer().analyze(build_analysis_payload(html))
        assert result['decision'] in {'no_alert', 'alert', 'inconclusive'}
    """,
    ),
    (
        "audit",
        "agents/audit",
        """
        import sys
        from datetime import UTC, datetime
        sys.path.insert(0, 'tests')
        from test_audit_security import FakeClient
        from audit_security.collector import collect_snapshot
        from audit_security.renderer import render_html
        from audit_security.advisor.agent import (
            CodexLunaAnalyzer, build_analysis_payload,
        )
        fixture = collect_snapshot(
            FakeClient(), window_minutes=1, max_events=100, max_rows=50,
        )
        payload = build_analysis_payload(
            render_html(fixture), agent_started_at=datetime.now(UTC),
        )
        result = CodexLunaAnalyzer().analyze(payload)
        assert result['decision'] in {'no_alert', 'alert', 'inconclusive'}
    """,
    ),
    (
        "slow-query-advisor",
        "agents/health-check",
        """
        import sys
        sys.path.insert(0, 'tests')
        from test_refactor_advisor import snapshot
        from refactor_collector.advisor import CodexLunaAnalyzer, _validate_analysis
        fixture, html, _ = snapshot(86.0)
        result = CodexLunaAnalyzer().analyze({
            'report_json': fixture, 'report_html': html,
            'already_sent_fingerprints': [],
        })
        _validate_analysis(result, fixture)
    """,
    ),
    (
        "refactor-proposal",
        "agents/refactor",
        """
        import sys
        sys.path.insert(0, 'tests')
        from test_worker import request_payload
        from query_refactor.advisor.agent import CodexSolRefactorAdvisor
        result = CodexSolRefactorAdvisor().propose(request_payload())
        assert result['contract_version'] == 'refactor_advisor_proposal.v1'
    """,
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Authorize four real provider calls"
    )
    args = parser.parse_args()
    if not args.execute:
        print("Use --execute para autorizar quatro chamadas reais ao LLM configurado.")
        return 0
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(
            (
                "MYSQL_",
                "SMTP_",
                "LAB_",
                "MCP_",
                "NOTIFICATION_",
                "HEALTHCHECK_",
                "AUDIT_SECURITY_",
            )
        ):
            environment.pop(key, None)
    environment.update(
        MCP_NOTIFICATION_ENABLED="false",
        MCP_DBA_ENABLED="false",
        NOTIFICATION_DELIVERY_ENABLED="false",
    )
    failed = False
    for name, project, source in CHECKS:
        completed = subprocess.run(
            ["uv", "run", "--locked", "python", "-c", textwrap.dedent(source)],
            cwd=ROOT / project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        success = completed.returncode == 0
        failed |= not success
        print(
            json.dumps(
                {
                    "check": name,
                    "status": "ok" if success else "failed",
                    "synthetic_input": True,
                    "database_accessed": False,
                    "published": False,
                }
            ),
            flush=True,
        )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
