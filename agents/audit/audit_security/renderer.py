"""Render a masked Audit snapshot with the Health Check report structure."""

# Embedded, dependency-free HTML/CSS keeps some lines intact.
# ruff: noqa: E501

from __future__ import annotations

import html
import json
from collections import Counter
from typing import Any

DOMAIN_META = {
    "audit_connections": ("Conexões auditadas", "events"),
    "audit_ddl": ("DDL auditado", "events"),
    "audit_errors": ("Erros auditados", "events"),
    "audit_sakila_data_access": ("Acessos ao Sakila", "events"),
    "instance_security": ("Segurança da instância", "instance"),
    "active_connections": ("Conexões ativas", "instance"),
    "accounts": ("Contas", "instance"),
    "global_privileges": ("Privilégios globais", "instance"),
    "roles": ("Roles", "instance"),
    "audit_configuration": ("Configuração Audit", "instance"),
}

STYLE = """
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,
  BlinkMacSystemFont,"Segoe UI",sans-serif;--bg:#071014;--surface:#0d171c;
  --border:#22343d;--soft:#18282f;--text:#e6eef2;--muted:#8fa3af;
  --faint:#607681;--mint:#70d8bd;--cyan:#61c9df;--amber:#efc56b;
  --coral:#ff8f76}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;min-width:320px;color:var(--text);background:
  radial-gradient(circle at 10% -10%,#153329 0,transparent 28rem),
  radial-gradient(circle at 92% 0,#1a1d36 0,transparent 28rem),var(--bg)}
.shell{width:min(1420px,calc(100% - 40px));margin:auto}
code,pre{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace}
.hero{padding:44px 0 28px}.hero-top{display:flex;justify-content:space-between;
  align-items:flex-start;gap:28px}
.eyebrow,.kicker{color:var(--mint);font-size:.7rem;font-weight:850;
  letter-spacing:.16em;text-transform:uppercase}
h1{max-width:800px;margin:10px 0;font-size:clamp(2.2rem,5vw,4.4rem);
  line-height:.98;letter-spacing:-.055em}
.hero-copy,.section-head p{margin:0;color:var(--muted);line-height:1.5}
.overall{min-width:210px;padding:18px 20px;border:1px solid var(--border);
  border-radius:18px;background:#101b21e8;box-shadow:0 20px 60px #0005}
.overall span{display:block;color:var(--muted);font-size:.7rem;font-weight:800;
  letter-spacing:.12em;text-transform:uppercase}
.overall strong{display:block;margin-top:6px;font-size:1.7rem}
.overall.complete strong{color:var(--mint)}.overall.partial strong{color:var(--amber)}
.hero-meta{display:flex;flex-wrap:wrap;gap:10px 26px;margin-top:28px;
  color:var(--muted);font-size:.82rem}.hero-meta strong{color:var(--text)}
.hero-meta code{color:#b9c8cf;overflow-wrap:anywhere}
.summary-bar{display:grid;grid-template-columns:repeat(4,1fr);margin-top:28px;
  overflow:hidden;border:1px solid var(--border);border-radius:17px;background:#0b151aeb}
.summary{padding:17px 21px;border-right:1px solid var(--border)}
.summary:last-child{border:0}.summary strong{display:block;font-size:1.7rem;line-height:1}
.summary span{display:block;margin-top:7px;color:var(--muted);font-size:.76rem}
.summary.available strong{color:var(--mint)}.summary.unavailable strong{color:var(--coral)}
.summary.truncated strong{color:var(--amber)}
.nav{position:sticky;top:0;z-index:5;border-block:1px solid var(--border);
  background:#071014e8;backdrop-filter:blur(16px)}
.nav .shell{display:flex;gap:5px;overflow-x:auto;padding:9px 0}
.nav a{flex:none;padding:9px 12px;border-radius:8px;color:var(--muted);
  font-size:.8rem;font-weight:700;text-decoration:none}
.nav a:hover{color:var(--text);background:#101d23}
main{padding:28px 0 68px}.scope-note{display:grid;grid-template-columns:auto 1fr;
  gap:14px;padding:18px;border:1px solid #584d2e;border-radius:14px;background:#211e14d9}
.scope-note b:first-child{display:grid;width:28px;height:28px;place-items:center;
  border:1px solid #786637;border-radius:50%;color:#f0dda0}
.scope-note strong{color:#f0dda0}.scope-note p{margin:4px 0 0;color:#ad9f73;line-height:1.5}
.report-section{margin-top:46px;scroll-margin-top:70px}
.section-head{display:flex;justify-content:space-between;align-items:flex-end;
  gap:24px;margin-bottom:17px}
.section-head h2{margin:6px 0 4px;font-size:1.55rem;letter-spacing:-.025em}
.count{flex:none;color:var(--faint);font:.76rem "SFMono-Regular",monospace}
.domain-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.domain-card{min-width:0;padding:21px;border:1px solid var(--border);
  border-top:2px solid var(--mint);border-radius:16px;
  background:linear-gradient(145deg,#101b20,#0b1419 80%);box-shadow:0 14px 38px #0002}
.domain-card.unavailable{border-top-color:var(--coral)}
.domain-card.truncated{border-top-color:var(--amber)}
.domain-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}
.domain-head h3{margin:9px 0 0;font-size:1.2rem}
.scope,.status{display:inline-flex;width:max-content;padding:6px 8px;border:1px solid currentColor;
  border-radius:999px;font-size:.64rem;font-weight:850;letter-spacing:.06em;text-transform:uppercase}
.scope.events{color:var(--mint);background:#10251f}
.scope.instance{color:var(--cyan);background:#102229}
.status.available{color:var(--mint);background:#10251f}
.status.unavailable{color:var(--coral);background:#2a1817}
.status.truncated{color:var(--amber);background:#262114}
.description{min-height:42px;margin:15px 0 17px;color:var(--muted);
  font-size:.85rem;line-height:1.5}
.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.metric{min-width:0;padding:11px;border:1px solid var(--soft);border-radius:10px;background:#081216}
.metric span{display:block;min-height:25px;color:var(--faint);font-size:.63rem;
  line-height:1.25;text-transform:uppercase}
.metric strong{display:block;margin-top:5px;overflow:hidden;color:#dce6ea;
  font-size:.94rem;text-overflow:ellipsis;white-space:nowrap}
details{margin-top:17px;border-top:1px solid var(--soft)}
summary{padding-top:14px;color:var(--muted);cursor:pointer;font-size:.74rem;font-weight:750}
pre{max-height:420px;margin:13px 0 0;overflow:auto;padding:15px;border:1px solid var(--soft);
  border-radius:9px;background:#060d10;color:#aebfc7;font-size:.7rem;line-height:1.55;
  white-space:pre-wrap;overflow-wrap:anywhere}
.facts{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px}
.fact{padding:14px;border:1px solid var(--border);border-radius:11px;background:var(--surface)}
.fact span{display:block;color:var(--faint);font-size:.64rem;text-transform:uppercase}
.fact strong{display:block;margin-top:7px;font-size:.86rem;overflow-wrap:anywhere}
.raw{padding:0 17px 17px;border:1px solid var(--border);border-radius:13px;background:var(--surface)}
footer{padding:24px 0 42px;border-top:1px solid var(--soft);color:var(--faint);font-size:.72rem}
@media(max-width:900px){.hero-top{flex-direction:column}.overall{width:100%}
  .domain-grid{grid-template-columns:1fr}.facts{grid-template-columns:repeat(2,1fr)}}
@media(max-width:620px){.shell{width:calc(100% - 24px)}
  .summary-bar{grid-template-columns:repeat(2,1fr)}
  .summary:nth-child(2){border-right:0}
  .summary:nth-child(-n+2){border-bottom:1px solid var(--border)}
  .section-head{flex-direction:column;align-items:flex-start;gap:8px}
  .metrics,.facts{grid-template-columns:1fr}.metric span{min-height:auto}}
"""


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _meta(name: str) -> tuple[str, str]:
    return DOMAIN_META.get(name, (name.replace("_", " ").title(), "instance"))


def _domain_card(name: str, domain: dict[str, Any]) -> str:
    label, group = _meta(name)
    available = domain.get("status") == "available"
    decoration = (
        "truncated"
        if available and domain.get("truncated")
        else ("available" if available else "unavailable")
    )
    status_label = {
        "available": "Disponível",
        "unavailable": "Indisponível",
        "truncated": "Truncado",
    }[decoration]
    rows = domain.get("rows") or []
    sample = rows[:10] if isinstance(rows, list) else []
    technical = {
        "source": domain.get("source"),
        "query_file": domain.get("query_file"),
        "row_count_total": domain.get("row_count_total"),
        "truncated": bool(domain.get("truncated")),
        "rows": sample,
    }
    metrics = (
        ("Linhas retornadas", _number(domain.get("row_count_returned"))),
        ("Duração", f"{_number(domain.get('duration_ms'))} ms"),
        ("Amostra exibida", _number(len(sample))),
    )
    metric_html = "".join(
        "<div class='metric'>"
        f"<span>{_escape(title)}</span><strong>{_escape(value)}</strong></div>"
        for title, value in metrics
    )
    detail = _escape(
        json.dumps(technical, ensure_ascii=False, indent=2, sort_keys=True)
    )
    return (
        f"<article class='domain-card {decoration}'>"
        "<div class='domain-head'><div>"
        f"<span class='scope {group}'>{'Eventos' if group == 'events' else 'Instância'}</span>"
        f"<h3>{_escape(label)}</h3></div>"
        f"<span class='status {decoration}'>{status_label}</span></div>"
        "<p class='description'>Fatos mascarados e limites da coleta atual.</p>"
        f"<div class='metrics'>{metric_html}</div>"
        f"<details><summary>Ver amostra e dados técnicos</summary><pre>{detail}</pre></details>"
        "</article>"
    )


def _sections(domains: dict[str, dict[str, Any]]) -> str:
    sections = []
    for group, short, title, description in (
        (
            "events",
            "Eventos",
            "Eventos auditados",
            "Fatos da janela consultada; somente sakila é elegível para os alertas definidos.",
        ),
        (
            "instance",
            "Instância",
            "Instância e acesso",
            "Contexto global do servidor, sem atribuição exclusiva ao sakila.",
        ),
    ):
        items = [
            (name, domain)
            for name, domain in domains.items()
            if _meta(name)[1] == group
        ]
        cards = "".join(_domain_card(name, domain) for name, domain in items)
        sections.append(
            f"<section class='report-section' id='{group}'>"
            "<div class='section-head'><div>"
            f"<span class='kicker'>{_escape(short)}</span><h2>{_escape(title)}</h2>"
            f"<p>{_escape(description)}</p></div>"
            f"<span class='count'>{len(items):02d} blocos</span></div>"
            f"<div class='domain-grid'>{cards}</div></section>"
        )
    return "".join(sections)


def render_html(report: dict[str, Any]) -> str:
    domains = report.get("domains") or {}
    counts = Counter(str(domain.get("status")) for domain in domains.values())
    truncated = sum(bool(domain.get("truncated")) for domain in domains.values())
    scope = report.get("scope") or {}
    overall = "complete" if report.get("overall_status") == "complete" else "partial"
    overall_label = "Completa" if overall == "complete" else "Parcial"
    summary = "".join(
        f"<div class='summary {name}'><strong>{_number(value)}</strong>"
        f"<span>{_escape(label)}</span></div>"
        for name, value, label in (
            ("available", counts.get("available", 0), "Domínios disponíveis"),
            ("unavailable", counts.get("unavailable", 0), "Indisponíveis"),
            ("truncated", truncated, "Truncados"),
            (
                "rows",
                sum(int(d.get("row_count_returned") or 0) for d in domains.values()),
                "Linhas retornadas",
            ),
        )
    )
    facts = "".join(
        "<div class='fact'>"
        f"<span>{_escape(label)}</span><strong>{_escape(value)}</strong></div>"
        for label, value in (
            (
                "Schema funcional",
                ", ".join(scope.get("functional_schemas") or []) or "—",
            ),
            ("Janela Audit", f"{_number(scope.get('audit_window_minutes'))} min"),
            ("Limite de eventos", _number(scope.get("max_audit_events"))),
            ("Duração", f"{_number(report.get('duration_ms'))} ms"),
        )
    )
    technical = _escape(
        json.dumps(
            {
                "target": report.get("target") or {},
                "scope": scope,
                "collector": report.get("collector") or {},
                "data_quality": report.get("data_quality") or {},
                "policy_evaluation": report.get("policy_evaluation") or {},
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MySQL HeatWave · Relatório Audit</title><style>{STYLE}</style></head>
<body><header class="hero"><div class="shell">
  <div class="hero-top"><div><span class="eyebrow">MySQL HeatWave · Audit Security</span>
    <h1>Relatório de auditoria</h1>
    <p class="hero-copy">Coleta somente leitura com evidências mascaradas.</p></div>
    <div class="overall {overall}"><span>Status da coleta</span><strong>{overall_label}</strong></div>
  </div>
  <div class="hero-meta"><span>Coletado em <strong>{_escape(report.get("collected_at", "—"))}</strong></span>
    <span>Audit ID <code>{_escape(report.get("audit_id", "—"))}</code></span></div>
  <div class="summary-bar">{summary}</div>
</div></header>
<nav class="nav"><div class="shell"><a href="#events">Eventos Audit</a>
  <a href="#instance">Instância</a><a href="#technical">Coleta</a></div></nav>
<main class="shell">
  <aside class="scope-note"><b>!</b><div><strong>Os dados deste relatório são fatos de coleta.</strong>
    <p>A decisão de alerta pertence ao advisor. Os dados globais da instância não são exclusivos do sakila.</p>
  </div></aside>
  {_sections(domains)}
  <section class="report-section" id="technical"><div class="section-head"><div>
    <span class="kicker">Coleta</span><h2>Ambiente e cobertura</h2>
    <p>Limites e qualidade do snapshot atual.</p></div></div>
    <div class="facts">{facts}</div>
    <details class="raw"><summary>Ver metadados completos da coleta</summary><pre>{technical}</pre></details>
  </section>
</main>
<footer><div class="shell">Snapshot estático, somente leitura · {_escape(report.get("audit_id", "—"))}</div></footer>
</body></html>
"""
