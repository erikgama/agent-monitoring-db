"""Human-readable HTML rendering with no external resources."""

from __future__ import annotations

import html
import json
from typing import Any


def _pretty(value: Any) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2))


def render_html(report: dict[str, Any]) -> str:
    cards: list[str] = []
    for name, domain in report["domains"].items():
        status = domain["status"]
        sample = domain.get("rows", [])[:10]
        details = _pretty({"rows": sample})
        cards.append(
            "<section class='card'>"
            f"<h2>{html.escape(name)}</h2>"
            f"<span class='badge {html.escape(status)}'>{html.escape(status)}</span>"
            f"<p>{domain.get('row_count_returned', 0)} linha(s) retornadas; "
            f"{domain.get('duration_ms', 0)} ms</p>"
            f"<pre>{details}</pre>"
            "</section>"
        )
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MySQL Audit Security</title>
<style>
body{{margin:0;background:#f4f7fb;color:#17202a;font-family:system-ui,sans-serif}}
header{{background:#152935;color:white;padding:24px}}header>div,main{{max-width:1280px;margin:auto}}
main{{padding:24px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px}}
.card{{background:white;border-radius:8px;padding:18px;box-shadow:0 1px 5px #0002}}
.badge{{display:inline-block;border-radius:999px;padding:4px 10px;font-weight:700}}
.healthy{{background:#d5f5e3;color:#196f3d}}.attention{{background:#fcf3cf;color:#7d6608}}
.critical{{background:#fadbd8;color:#922b21}}.unavailable{{background:#e5e7e9;color:#424949}}
pre{{white-space:pre-wrap;overflow:auto;max-height:420px;background:#f8fafc;padding:12px;border-radius:6px}}
</style></head><body><header><div><h1>MySQL HeatWave — Audit Security</h1>
<p>Audit ID: <code>{html.escape(str(report["audit_id"]))}</code></p>
<p>Coletado em {html.escape(str(report["collected_at"]))}</p>
<span class="badge">{html.escape(report["overall_status"])}</span>
</div></header><main><p>Este relatório contém somente fatos coletados e
mascarados. A decisão de alerta pertence ao agente Luna.</p>
<h2>Domínios</h2><div class="grid">{"".join(cards)}</div>
</main></body></html>
"""
