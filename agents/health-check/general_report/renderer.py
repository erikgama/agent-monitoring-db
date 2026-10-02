"""Static HTML rendering from an already validated health-check object."""

# The embedded, dependency-free HTML/CSS intentionally keeps some lines intact.
# ruff: noqa: E501

from __future__ import annotations

import html
import json
from collections import Counter
from typing import Any

STATUS_LABELS = {
    "healthy": "Saudável",
    "attention": "Atenção",
    "critical": "Crítico",
    "unknown": "Desconhecido",
    "not_available": "Indisponível",
    "degraded": "Degradado",
    "error": "Erro",
    "available": "Disponível",
    "enabled": "Ativo",
    "disabled": "Inativo",
}

DOMAIN_META = {
    "workload": ("Workload", "schema", "Digests e comportamento SQL do sakila."),
    "active_sessions": (
        "Sessões ativas",
        "schema",
        "Sessões não ociosas usando sakila no momento da coleta.",
    ),
    "schema_tables": (
        "Tabelas",
        "schema",
        "Estrutura, volume e capacidade das tabelas InnoDB do sakila.",
    ),
    "indexes": (
        "Índices",
        "schema",
        "Uso, redundância e seletividade dos índices do sakila.",
    ),
    "locks": (
        "Locks",
        "mixed",
        "Esperas atuais do sakila e contador global acumulado de deadlocks.",
    ),
    "connections": (
        "Conexões",
        "instance",
        "Conexões e abortos acumulados de toda a instância.",
    ),
    "innodb": (
        "InnoDB",
        "instance",
        "Buffer pool, redo e locks do mecanismo InnoDB da instância.",
    ),
    "errors": (
        "Erros",
        "instance",
        "Contadores globais acumulados, sem atribuição ao schema.",
    ),
    "replication": (
        "Replicação",
        "instance",
        "Estado dos canais e membros de replicação da instância.",
    ),
}

SCOPE_META = {
    "schema": (
        "Schema sakila",
        "Sakila",
        "Sinais filtrados explicitamente para o schema sakila.",
    ),
    "mixed": (
        "Escopo misto",
        "Misto",
        "Blocos que combinam sinais do sakila e da instância.",
    ),
    "instance": (
        "Contexto da instância",
        "Instância",
        "Sinais globais do servidor, sem atribuição exclusiva ao sakila.",
    ),
}


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _status_label(value: Any) -> str:
    status = str(value)
    return STATUS_LABELS.get(status, status.replace("_", " ").title())


def _number(value: Any, decimals: int = 0) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:,.{decimals}f}"
    return text.replace(",", "#").replace(".", ",").replace("#", ".")


def _bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024 or unit == "TB":
            return f"{_number(size, 0 if unit == 'B' else 1)} {unit}"
        size /= 1024
    return "—"


def _json(value: Any) -> str:
    return _escape(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _meta(name: str) -> tuple[str, str, str]:
    return DOMAIN_META.get(
        name,
        (name.replace("_", " ").title(), "instance", "Métricas técnicas do MySQL."),
    )


def _scope_kind(name: str, domain: dict[str, Any]) -> str:
    kind = (domain.get("scope") or {}).get("kind")
    return kind if kind in SCOPE_META else _meta(name)[1]


def _highlights(name: str, metrics: dict[str, Any]) -> list[tuple[str, str]]:
    aggregate = metrics.get("aggregate") or {}
    schemas = metrics.get("schemas") or []
    schema = schemas[0] if schemas else {}
    values = {
        "connections": [
            ("Conectadas", _number(metrics.get("threads_connected"))),
            ("Uso", f"{_number(metrics.get('connection_usage_pct'), 2)}%"),
            ("Abortadas", f"{_number(metrics.get('aborted_connect_pct'), 2)}%"),
        ],
        "workload": [
            ("Statements", _number(aggregate.get("statements"))),
            ("Top digests", _number(len(metrics.get("top_digests") or []))),
            ("Sem índice", _number(aggregate.get("no_index_used"))),
        ],
        "active_sessions": [
            ("Ativas", _number(metrics.get("active_count"))),
            ("Maior duração", f"{_number(metrics.get('longest_seconds'))}s"),
            ("Exibidas", _number(len(metrics.get("sessions") or []))),
        ],
        "locks": [
            ("Esperas atuais", _number(metrics.get("current_wait_count"))),
            ("Deadlocks globais", _number(metrics.get("deadlocks"))),
            ("Esperas de tabela", _number(len(metrics.get("table_lock_waits") or []))),
        ],
        "innodb": [
            (
                "Buffer pool hit",
                f"{_number(metrics.get('buffer_pool_hit_ratio_pct'), 3)}%",
            ),
            ("Log waits", _number(metrics.get("log_waits"))),
            ("Row lock waits", _number(metrics.get("row_lock_waits"))),
        ],
        "schema_tables": [
            ("Tabelas", _number(schema.get("table_count"))),
            ("Tamanho", _bytes(schema.get("total_bytes"))),
            (
                "Sem chave primária",
                _number(len(metrics.get("missing_primary_keys") or [])),
            ),
        ],
        "indexes": [
            ("Sem uso observado", _number(len(metrics.get("unused_indexes") or []))),
            ("Redundantes", _number(len(metrics.get("redundant_indexes") or []))),
            ("Amostra seletividade", _number(len(metrics.get("selectivity") or []))),
        ],
        "errors": [
            ("Total acumulado", _number(metrics.get("total_error_count"))),
            ("Tipos exibidos", _number(len(metrics.get("top_errors") or []))),
            ("Escopo", "Global"),
        ],
        "replication": [
            (
                "Modo",
                "Não detectada"
                if metrics.get("mode") == "not_detected"
                else str(metrics.get("mode", "—")),
            ),
            ("Canais", _number(len(metrics.get("channels") or []))),
            ("Erros", _number(len(metrics.get("errors") or []))),
        ],
    }
    return values.get(name, [("Métricas", _number(len(metrics)))])


def _domain_card(name: str, domain: dict[str, Any]) -> str:
    label, _, fallback_description = _meta(name)
    scope = _scope_kind(name, domain)
    description = (domain.get("scope") or {}).get("description") or fallback_description
    scope_label = SCOPE_META[scope][1]
    status = str(domain.get("status", "unknown"))
    metrics = domain.get("metrics") or {}
    highlights = "".join(
        "<div class='metric'>"
        f"<span>{_escape(item_label)}</span><strong>{_escape(value)}</strong>"
        "</div>"
        for item_label, value in _highlights(name, metrics)
    )
    findings = domain.get("findings") or []
    if findings:
        finding_html = (
            "<div class='mini-findings'>"
            + "".join(
                "<p>"
                f"<i class='{_escape(item.get('severity', 'unknown'))}'></i>"
                f"{_escape(item.get('title', 'Achado sem título'))}</p>"
                for item in findings
            )
            + "</div>"
        )
    else:
        finding_html = "<p class='clear'>Nenhum achado neste bloco.</p>"
    technical = {
        "scope": domain.get("scope") or {},
        "metrics": metrics,
        "sources": domain.get("sources") or {},
    }
    return (
        f"<article class='domain-card border-{_escape(status)}'>"
        "<div class='domain-head'><div>"
        f"<span class='scope scope-{_escape(scope)}'>{_escape(scope_label)}</span>"
        f"<h3>{_escape(label)}</h3></div>"
        f"<span class='status {_escape(status)}'>{_escape(_status_label(status))}</span>"
        "</div>"
        f"<p class='description'>{_escape(description)}</p>"
        f"<div class='metrics'>{highlights}</div>{finding_html}"
        "<details><summary>Ver dados técnicos</summary>"
        f"<pre>{_json(technical)}</pre></details>"
        "</article>"
    )


def _domain_sections(domains: dict[str, dict[str, Any]]) -> str:
    result = []
    rank = {"critical": 0, "attention": 1, "unknown": 2, "healthy": 3}
    for scope in ("schema", "mixed", "instance"):
        title, short, description = SCOPE_META[scope]
        items = [
            (name, domain)
            for name, domain in domains.items()
            if _scope_kind(name, domain) == scope
        ]
        if not items:
            continue
        items.sort(key=lambda item: rank.get(item[1].get("status"), 4))
        cards = "".join(_domain_card(name, domain) for name, domain in items)
        result.append(
            f"<section class='report-section' id='{_escape(scope)}'>"
            "<div class='section-head'><div>"
            f"<span class='kicker'>{_escape(short)}</span><h2>{_escape(title)}</h2>"
            f"<p>{_escape(description)}</p></div>"
            f"<span class='count'>{len(items):02d} blocos</span></div>"
            f"<div class='domain-grid'>{cards}</div></section>"
        )
    return "".join(result)


def _finding_cards(report: dict[str, Any]) -> str:
    findings = list(report.get("findings") or [])
    rank = {"critical": 0, "attention": 1, "unknown": 2}
    findings.sort(key=lambda item: rank.get(item.get("severity"), 3))
    if not findings:
        return "<div class='empty'><strong>Nenhum achado.</strong></div>"
    cards = []
    for item in findings:
        severity = str(item.get("severity", "unknown"))
        domain_name = str(item.get("domain", "unknown"))
        label = _meta(domain_name)[0]
        domain = (report.get("domains") or {}).get(domain_name) or {}
        scope = _scope_kind(domain_name, domain)
        scope_label = SCOPE_META[scope][1]
        evidence = _escape(
            json.dumps(item.get("evidence") or {}, ensure_ascii=False, sort_keys=True)
        )
        cards.append(
            f"<article class='finding border-{_escape(severity)}'>"
            "<div class='finding-head'>"
            f"<span class='status {_escape(severity)}'>{_escape(_status_label(severity))}</span>"
            f"<span>{_escape(label)} · {_escape(scope_label)}</span></div>"
            f"<h3>{_escape(item.get('title', 'Achado sem título'))}</h3>"
            f"<code>{evidence}</code>"
            f"<small>{_escape(item.get('id', ''))}</small>"
            "</article>"
        )
    return "<div class='finding-grid'>" + "".join(cards) + "</div>"


def render_snapshot_html(report: dict[str, Any]) -> str:
    status = str(report.get("overall_status", "unknown"))
    domains = report.get("domains") or {}
    counts = Counter(
        str(domain.get("status", "unknown")) for domain in domains.values()
    )
    schemas = ", ".join(report.get("scope", {}).get("schemas") or []) or "—"
    capabilities = report.get("capabilities") or {}
    instance = report.get("instance") or {}
    config = instance.get("config") or {}
    uptime_seconds = int(instance.get("uptime_seconds") or 0)
    days, remainder = divmod(uptime_seconds, 86400)
    hours = remainder // 3600
    uptime = f"{days}d {hours}h" if days else f"{hours}h"

    summary = "".join(
        f"<div class='summary summary-{_escape(item_status)}'>"
        f"<strong>{counts.get(item_status, 0):02d}</strong>"
        f"<span>{_escape(_status_label(item_status))}</span></div>"
        for item_status in ("critical", "attention", "healthy", "unknown")
    )
    capability_pills = "".join(
        "<span class='capability'>"
        f"<strong>{_escape(name.replace('_', ' '))}</strong>"
        f"<i class='{_escape(value)}'>{_escape(_status_label(value))}</i></span>"
        for name, value in capabilities.items()
        if name != "probe"
    )
    facts = [
        ("MySQL", report.get("target", {}).get("mysql_version", "—")),
        ("Uptime", uptime),
        ("Max connections", _number(config.get("max_connections"))),
        ("Buffer pool", _bytes(config.get("innodb_buffer_pool_size_bytes"))),
        ("Duração", f"{_number(report.get('duration_ms'))} ms"),
    ]
    fact_cards = "".join(
        f"<div class='fact'><span>{_escape(label)}</span>"
        f"<strong>{_escape(value)}</strong></div>"
        for label, value in facts
    )
    technical = {
        "target": report.get("target", {}),
        "scope": report.get("scope", {}),
        "capabilities": capabilities,
        "data_retention": report.get("data_retention", {}),
    }

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MySQL HeatWave · Relatório de saúde</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui,
      -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; --bg:#071014;
      --surface:#0d171c; --surface2:#101d23; --border:#22343d;
      --soft:#18282f; --text:#e6eef2; --muted:#8fa3af; --faint:#607681;
      --mint:#70d8bd; --cyan:#61c9df; --amber:#efc56b; --coral:#ff8f76;
      --violet:#a995ed; }}
    * {{ box-sizing:border-box; }} html {{ scroll-behavior:smooth; }}
    body {{ margin:0; min-width:320px; color:var(--text); background:
      radial-gradient(circle at 10% -10%,#153329 0,transparent 28rem),
      radial-gradient(circle at 92% 0,#1a1d36 0,transparent 28rem),var(--bg); }}
    body:before {{ content:""; position:fixed; inset:0; pointer-events:none;
      opacity:.16; background-image:radial-gradient(#48606b .7px,transparent .7px);
      background-size:24px 24px; mask-image:linear-gradient(#000,transparent 70%); }}
    .shell {{ width:min(1420px,calc(100% - 40px)); margin:auto; }}
    code,pre {{ font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace; }}
    .hero {{ position:relative; padding:44px 0 28px; }}
    .hero-top {{ display:flex; justify-content:space-between; align-items:flex-start;
      gap:28px; }}
    .eyebrow,.kicker {{ color:var(--mint); font-size:.7rem; font-weight:850;
      letter-spacing:.16em; text-transform:uppercase; }}
    h1 {{ max-width:800px; margin:10px 0; font-size:clamp(2.2rem,5vw,4.4rem);
      line-height:.98; letter-spacing:-.055em; }}
    .hero-copy,.section-head p {{ margin:0; color:var(--muted); line-height:1.5; }}
    .overall {{ min-width:210px; padding:18px 20px; border:1px solid var(--border);
      border-radius:18px; background:#101b21e8; box-shadow:0 20px 60px #0005; }}
    .overall span {{ display:block; color:var(--muted); font-size:.7rem;
      font-weight:800; letter-spacing:.12em; text-transform:uppercase; }}
    .overall strong {{ display:block; margin-top:6px; font-size:1.7rem; }}
    .overall.critical strong {{ color:var(--coral); }}
    .overall.attention strong {{ color:var(--amber); }}
    .overall.healthy strong {{ color:var(--mint); }}
    .hero-meta {{ display:flex; flex-wrap:wrap; gap:10px 26px; margin-top:28px;
      color:var(--muted); font-size:.82rem; }}
    .hero-meta strong {{ color:var(--text); }} .hero-meta code {{ color:#b9c8cf; }}
    .summary-bar {{ display:grid; grid-template-columns:repeat(4,1fr); margin-top:28px;
      overflow:hidden; border:1px solid var(--border); border-radius:17px;
      background:#0b151aeb; }}
    .summary {{ padding:17px 21px; border-right:1px solid var(--border); }}
    .summary:last-child {{ border:0; }} .summary strong {{ display:block;
      font-size:1.7rem; line-height:1; }} .summary span {{ display:block;
      margin-top:7px; color:var(--muted); font-size:.76rem; }}
    .summary-critical strong {{ color:var(--coral); }}
    .summary-attention strong {{ color:var(--amber); }}
    .summary-healthy strong {{ color:var(--mint); }}
    .nav {{ position:sticky; top:0; z-index:5; border-block:1px solid var(--border);
      background:#071014e8; backdrop-filter:blur(16px); }}
    .nav .shell {{ display:flex; gap:5px; overflow-x:auto; padding:9px 0; }}
    .nav a {{ flex:none; padding:9px 12px; border-radius:8px; color:var(--muted);
      font-size:.8rem; font-weight:700; text-decoration:none; }}
    .nav a:hover {{ color:var(--text); background:var(--surface2); }}
    main {{ position:relative; padding:28px 0 68px; }}
    .scope-note {{ display:grid; grid-template-columns:auto 1fr; gap:14px; padding:18px;
      border:1px solid #584d2e; border-radius:14px; background:#211e14d9; }}
    .scope-note b:first-child {{ display:grid; width:28px; height:28px; place-items:center;
      border:1px solid #786637; border-radius:50%; color:#f0dda0; }}
    .scope-note strong {{ color:#f0dda0; }} .scope-note p {{ margin:4px 0 0;
      color:#ad9f73; line-height:1.5; }}
    .report-section {{ margin-top:46px; scroll-margin-top:70px; }}
    .section-head {{ display:flex; justify-content:space-between; align-items:flex-end;
      gap:24px; margin-bottom:17px; }}
    .section-head h2 {{ margin:6px 0 4px; font-size:1.55rem; letter-spacing:-.025em; }}
    .count {{ flex:none; color:var(--faint); font: .76rem "SFMono-Regular",monospace; }}
    .finding-grid,.domain-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
      gap:14px; }}
    .finding,.domain-card {{ min-width:0; border:1px solid var(--border);
      border-top-width:2px; border-radius:16px; background:linear-gradient(145deg,#101b20,#0b1419 80%);
      box-shadow:0 14px 38px #0002; }}
    .border-critical {{ border-top-color:var(--coral); }}
    .border-attention {{ border-top-color:var(--amber); }}
    .border-healthy {{ border-top-color:var(--mint); }}
    .border-unknown {{ border-top-color:var(--muted); }}
    .finding {{ padding:20px; }} .finding-head,.domain-head {{ display:flex;
      align-items:flex-start; justify-content:space-between; gap:14px; }}
    .finding-head>span:last-child {{ color:var(--muted); font-size:.76rem; font-weight:750; }}
    .finding h3 {{ margin:17px 0 15px; font-size:1rem; line-height:1.4; }}
    .finding code {{ display:block; padding:11px; border:1px solid var(--soft);
      border-radius:9px; background:#071014; color:#b7c7ce; font-size:.72rem;
      overflow-wrap:anywhere; }} .finding small {{ display:block; margin-top:13px;
      color:var(--faint); overflow-wrap:anywhere; }}
    .status,.scope {{ display:inline-flex; width:max-content; padding:6px 8px;
      border:1px solid currentColor; border-radius:999px; font-size:.64rem;
      font-weight:850; letter-spacing:.06em; text-transform:uppercase; }}
    .status.critical {{ color:var(--coral); background:#2a1817; }}
    .status.attention {{ color:var(--amber); background:#262114; }}
    .status.healthy {{ color:var(--mint); background:#10251f; }}
    .status.unknown,.status.not_available {{ color:var(--muted); background:#182126; }}
    .domain-card {{ padding:21px; }} .domain-head h3 {{ margin:9px 0 0; font-size:1.2rem; }}
    .scope-schema {{ color:var(--mint); background:#10251f; }}
    .scope-mixed {{ color:var(--violet); background:#1d1930; }}
    .scope-instance {{ color:var(--cyan); background:#102229; }}
    .description {{ min-height:42px; margin:15px 0 17px; color:var(--muted);
      font-size:.85rem; line-height:1.5; }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }}
    .metric {{ min-width:0; padding:11px; border:1px solid var(--soft);
      border-radius:10px; background:#081216; }} .metric span {{ display:block;
      min-height:25px; color:var(--faint); font-size:.63rem; line-height:1.25;
      text-transform:uppercase; }} .metric strong {{ display:block; margin-top:5px;
      overflow:hidden; color:#dce6ea; font-size:.94rem; text-overflow:ellipsis;
      white-space:nowrap; }}
    .mini-findings {{ display:grid; gap:7px; margin-top:15px; }}
    .mini-findings p {{ display:flex; gap:9px; margin:0; color:#bdcbd1;
      font-size:.77rem; line-height:1.4; }} .mini-findings i {{ flex:none; width:7px;
      height:7px; margin-top:4px; border-radius:50%; background:var(--muted); }}
    .mini-findings i.critical {{ background:var(--coral); }}
    .mini-findings i.attention {{ background:var(--amber); }}
    .clear {{ margin:15px 0 0; color:#648b7e; font-size:.77rem; }}
    details {{ margin-top:17px; border-top:1px solid var(--soft); }}
    summary {{ padding-top:14px; color:var(--muted); cursor:pointer;
      font-size:.74rem; font-weight:750; }} pre {{ max-height:420px; margin:13px 0 0;
      overflow:auto; padding:15px; border:1px solid var(--soft); border-radius:9px;
      background:#060d10; color:#aebfc7; font-size:.7rem; line-height:1.55;
      white-space:pre-wrap; overflow-wrap:anywhere; }}
    .facts {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:9px; }}
    .fact {{ padding:14px; border:1px solid var(--border); border-radius:11px;
      background:var(--surface); }} .fact span {{ display:block; color:var(--faint);
      font-size:.64rem; text-transform:uppercase; }} .fact strong {{ display:block;
      margin-top:7px; font-size:.86rem; overflow-wrap:anywhere; }}
    .capabilities {{ display:flex; flex-wrap:wrap; gap:7px; margin-top:12px; }}
    .capability {{ display:flex; align-items:center; gap:9px; padding:8px 10px;
      border:1px solid var(--soft); border-radius:8px; background:#081216;
      font-size:.7rem; text-transform:capitalize; }} .capability strong {{ color:#b7c6cd; }}
    .capability i {{ color:var(--muted); font-style:normal; }}
    .capability i.available,.capability i.enabled {{ color:var(--mint); }}
    .raw {{ padding:0 17px 17px; border:1px solid var(--border); border-radius:13px;
      background:var(--surface); }} .empty {{ padding:25px; border:1px dashed var(--border);
      border-radius:13px; color:var(--mint); }}
    footer {{ padding:24px 0 42px; border-top:1px solid var(--soft); color:var(--faint);
      font-size:.72rem; }}
    @media(max-width:900px) {{ .hero-top {{ flex-direction:column; }} .overall {{ width:100%; }}
      .finding-grid,.domain-grid {{ grid-template-columns:1fr; }}
      .facts {{ grid-template-columns:repeat(2,1fr); }} }}
    @media(max-width:620px) {{ .shell {{ width:calc(100% - 24px); }}
      .summary-bar {{ grid-template-columns:repeat(2,1fr); }}
      .summary:nth-child(2) {{ border-right:0; }} .summary:nth-child(-n+2) {{ border-bottom:1px solid var(--border); }}
      .section-head {{ flex-direction:column; align-items:flex-start; gap:8px; }}
      .metrics,.facts {{ grid-template-columns:1fr; }} .metric span {{ min-height:auto; }} }}
  </style>
</head>
<body>
  <header class="hero"><div class="shell">
    <div class="hero-top"><div><span class="eyebrow">MySQL HeatWave · Health Check</span>
      <h1>Relatório de saúde</h1><p class="hero-copy">Visão do schema <strong>{_escape(schemas)}</strong> e do contexto da instância.</p></div>
      <div class="overall {_escape(status)}"><span>Status geral</span><strong>{_escape(_status_label(status))}</strong></div>
    </div>
    <div class="hero-meta"><span>Coletado em <strong>{_escape(report.get("collected_at", "—"))}</strong></span>
      <span>Audit ID <code>{_escape(report.get("audit_id", "—"))}</code></span></div>
    <div class="summary-bar">{summary}</div>
  </div></header>
  <nav class="nav"><div class="shell"><a href="#findings">Achados</a><a href="#schema">Sakila</a>
    <a href="#mixed">Escopo misto</a><a href="#instance">Instância</a><a href="#technical">Coleta</a></div></nav>
  <main class="shell">
    <aside class="scope-note"><b>!</b><div><strong>O status geral combina escopos diferentes.</strong>
      <p>Conexões, InnoDB e erros descrevem toda a instância e não podem ser atribuídos exclusivamente ao sakila.</p></div></aside>
    <section class="report-section" id="findings"><div class="section-head"><div>
      <span class="kicker">Prioridades</span><h2>Achados da coleta</h2>
      <p>Itens ordenados por severidade, com a evidência usada na avaliação.</p></div>
      <span class="count">{len(report.get("findings") or []):02d} achados</span></div>{_finding_cards(report)}</section>
    {_domain_sections(domains)}
    <section class="report-section" id="technical"><div class="section-head"><div>
      <span class="kicker">Coleta</span><h2>Ambiente e capacidades</h2>
      <p>Contexto técnico usado para produzir este snapshot.</p></div></div>
      <div class="facts">{fact_cards}</div><div class="capabilities">{capability_pills}</div>
      <details class="raw"><summary>Ver metadados completos da coleta</summary><pre>{_json(technical)}</pre></details>
    </section>
  </main>
  <footer><div class="shell">Snapshot estático, somente leitura · {_escape(report.get("audit_id", "—"))}</div></footer>
</body>
</html>
"""
