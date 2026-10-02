#!/usr/bin/env python3
"""Run independent MySQL security queries and publish JSON + HTML snapshots."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import mysql.connector
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Install dependencies first: pip install -r requirements.txt") from exc

ROOT = Path(__file__).resolve().parent
QUERIES = ROOT / "queries"
SEVERITY_ORDER = {"healthy": 0, "attention": 1, "unavailable": 2, "critical": 3}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_value(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, bool) or value is None or isinstance(value, (str, int, float)):
        return value
    return str(value)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(value)
        temp_name = handle.name
    os.replace(temp_name, path)


def findings_for(domain: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if domain == "instance_security" and rows:
        row = rows[0]
        if not row.get("require_secure_transport"):
            findings.append({"severity": "attention", "id": "tls.not_required", "title": "Transporte seguro não é obrigatório"})
        if row.get("default_password_lifetime_days") in (0, "0"):
            findings.append({"severity": "attention", "id": "password.expiry_disabled", "title": "Expiração global de senha está desabilitada"})
        if row.get("local_infile") in (1, "1", True):
            findings.append({"severity": "attention", "id": "local_infile.enabled", "title": "LOCAL INFILE está habilitado"})
        if row.get("audit_plugin_status") != "ACTIVE":
            findings.append({"severity": "attention", "id": "audit.not_active", "title": "Enterprise Audit não está ativo"})
        if row.get("audit_log_format") not in ("JSON", "json"):
            findings.append({"severity": "attention", "id": "audit.json_unavailable", "title": "Audit não está em formato JSON"})
        if row.get("audit_log_disable") in ("ON", "on", 1, True):
            findings.append({"severity": "attention", "id": "audit.disabled", "title": "Enterprise Audit está desabilitado"})
        if not row.get("collector_has_audit_admin"):
            findings.append({"severity": "attention", "id": "collector.audit_admin_missing", "title": "Coletor não possui AUDIT_ADMIN"})
    elif domain == "active_connections":
        count = sum(row.get("transport_assessment") == "tls_not_confirmed" for row in rows)
        if count:
            findings.append({"severity": "attention", "id": "connections.tls_not_confirmed", "title": "Conexões remotas sem TLS confirmado", "count": count})
    elif domain == "accounts":
        anonymous = sum(bool(row.get("anonymous_user")) for row in rows)
        broad = sum(bool(row.get("wildcard_or_broad_host")) for row in rows)
        if anonymous:
            findings.append({"severity": "critical", "id": "accounts.anonymous", "title": "Contas anônimas encontradas", "count": anonymous})
        if broad:
            findings.append({"severity": "attention", "id": "accounts.broad_host", "title": "Contas com host amplo/coringa", "count": broad})
    elif domain == "global_privileges":
        elevated = sum(bool(row.get("elevated_privilege")) for row in rows)
        delegated = sum(bool(row.get("elevated_privilege")) and bool(row.get("grantable")) for row in rows)
        if elevated:
            findings.append({"severity": "attention", "id": "privileges.elevated", "title": "Privilégios administrativos globais", "count": elevated})
        if delegated:
            findings.append({"severity": "critical", "id": "privileges.delegable_elevated", "title": "Privilégios administrativos delegáveis", "count": delegated})
    elif domain == "audit_preflight" and not rows:
        findings.append({"severity": "attention", "id": "audit.tables_not_visible", "title": "Tabelas de filtro Audit não visíveis"})
    elif domain == "audit_connections":
        failed = sum(row.get("outcome") == "failure" for row in rows)
        plain = sum(str(row.get("connection_type", "")).lower() in ("tcp/ip", "tcp") for row in rows)
        if failed:
            findings.append({"severity": "attention", "id": "audit.connection_failures", "title": "Falhas de conexão na janela", "count": failed})
        if plain:
            findings.append({"severity": "attention", "id": "audit.unencrypted_tcp", "title": "Conexões TCP sem TLS na janela", "count": plain})
    elif domain == "audit_errors":
        if rows:
            findings.append({"severity": "critical" if len(rows) >= 10 else "attention", "id": "audit.errors", "title": "Erros registrados no Audit", "count": len(rows)})
    elif domain == "audit_ddl":
        successful_drop = sum(row.get("sql_command") in ("drop_table", "drop_database") and row.get("outcome") == "success" for row in rows)
        blocked_drop = sum(row.get("sql_command") in ("drop_table", "drop_database") and row.get("outcome") == "failure" for row in rows)
        identity_change = sum(row.get("sql_command") in ("create_user", "alter_user", "drop_user") and row.get("outcome") == "success" for row in rows)
        if successful_drop:
            findings.append({"severity": "attention", "id": "audit.ddl.successful_drop", "title": "DROP executado com sucesso — validar autorização e impacto", "count": successful_drop,
                             "llm_interpretation": "Pode ser mudança autorizada, mas exige validação de ticket, escopo e impacto."})
        if blocked_drop:
            findings.append({"severity": "critical", "id": "audit.ddl.blocked_drop_attempt", "title": "Tentativa de DROP bloqueada — possível sabotagem ou abuso de privilégio", "count": blocked_drop,
                             "llm_interpretation": "Trate como tentativa de ação destrutiva não autorizada; correlacione ator, origem, horário e outras falhas de autenticação."})
        if identity_change:
            findings.append({"severity": "critical", "id": "audit.ddl.identity_change", "title": "Criação, alteração ou remoção de conta detectada", "count": identity_change})
    return findings


def status_for(findings: list[dict[str, Any]]) -> str:
    if any(item["severity"] == "critical" for item in findings):
        return "critical"
    if findings:
        return "attention"
    return "healthy"


def fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def in_utc_window(occurred_at: str, windows: list[dict[str, Any]]) -> bool:
    try:
        moment = dt.datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        current = moment.timetz().replace(tzinfo=None)
    except (TypeError, ValueError):
        return False
    for window in windows:
        if moment.weekday() not in window.get("days", []):
            continue
        try:
            start = dt.time.fromisoformat(window["start"])
            end = dt.time.fromisoformat(window["end"])
        except (KeyError, ValueError):
            continue
        if (start <= end and start <= current <= end) or (start > end and (current >= start or current <= end)):
            return True
    return False


def evaluate_policy(policy: dict[str, Any], domains: dict[str, Any]) -> dict[str, Any]:
    controls = policy.get("controls", {})
    violations: list[dict[str, Any]] = []
    not_evaluated: list[str] = []

    def violation(rule_id: str, title: str, evidence: dict[str, Any], severity: str = "critical") -> None:
        violations.append({"id": rule_id, "title": title, "severity": severity, "evidence": evidence})

    instance_rows = domains.get("instance_security", {}).get("rows", [])
    if instance_rows:
        instance = instance_rows[0]
        if controls.get("require_secure_transport") is True and not instance.get("require_secure_transport"):
            violation("policy.require_secure_transport", "Política exige transporte seguro", {"actual": False})
        if controls.get("allow_local_infile") is False and instance.get("local_infile") in (1, "1", True):
            violation("policy.local_infile", "Política não permite LOCAL INFILE", {"actual": True}, "attention")
        maximum_lifetime = controls.get("maximum_default_password_lifetime_days")
        actual_lifetime = instance.get("default_password_lifetime_days")
        if isinstance(maximum_lifetime, int) and (actual_lifetime is None or int(actual_lifetime) == 0 or int(actual_lifetime) > maximum_lifetime):
            violation("policy.password_lifetime", "Expiração padrão de senha fora da política", {"actual_days": actual_lifetime, "maximum_days": maximum_lifetime}, "attention")
    else:
        not_evaluated.append("instance_security controls")

    account_rows = domains.get("accounts", {}).get("rows", [])
    maximum_broad = controls.get("maximum_broad_host_accounts")
    broad_count = sum(bool(row.get("wildcard_or_broad_host")) for row in account_rows)
    if isinstance(maximum_broad, int) and broad_count > maximum_broad:
        violation("policy.broad_host_accounts", "Contas com host amplo excedem a política", {"actual": broad_count, "maximum": maximum_broad})

    privilege_rows = domains.get("global_privileges", {}).get("rows", [])
    maximum_delegable = controls.get("maximum_delegable_elevated_privilege_assignments")
    delegable_count = sum(bool(row.get("elevated_privilege")) and bool(row.get("grantable")) for row in privilege_rows)
    if isinstance(maximum_delegable, int) and delegable_count > maximum_delegable:
        violation("policy.delegable_elevated_privileges", "Privilégios administrativos delegáveis excedem a política", {"observed_in_returned_rows": delegable_count, "maximum": maximum_delegable})
    if domains.get("global_privileges", {}).get("truncated"):
        not_evaluated.append("complete global privilege inventory (truncated)")

    audit_domains = [name for name in domains if name.startswith("audit_") and name != "audit_preflight"]
    unavailable_audit = [name for name in audit_domains if domains[name]["status"] == "unavailable"]
    if controls.get("require_audit_event_coverage") and unavailable_audit:
        violation("policy.audit_coverage", "Política exige cobertura de eventos Audit", {"unavailable_domains": unavailable_audit})

    connection_rows = domains.get("audit_connections", {}).get("rows", [])
    allowed_ips = policy.get("network", {}).get("allowed_source_ips", [])
    if allowed_ips:
        allowed_sources = {fingerprint(ip) for ip in allowed_ips}
        for row in connection_rows:
            if row.get("outcome") == "success" and row.get("source_id") and row["source_id"] not in allowed_sources:
                violation("policy.source_ip_not_allowed", "Conexão bem-sucedida de IP fora da lista permitida", {"source_id": row["source_id"], "actor_id": row.get("actor_id"), "occurred_at_utc": row.get("occurred_at_utc")})
    else:
        not_evaluated.append("network allowed_source_ips is empty")

    for rule in policy.get("account_access", []):
        user = rule.get("user")
        if not user:
            continue
        actor = fingerprint(user)
        allowed_sources = {fingerprint(ip) for ip in rule.get("allowed_source_ips", [])}
        windows = rule.get("allowed_utc_windows", [])
        for row in connection_rows:
            if row.get("outcome") != "success" or row.get("actor_id") != actor:
                continue
            if allowed_sources and row.get("source_id") not in allowed_sources:
                violation("policy.account_source", "Usuário conectou de origem não permitida", {"user": user, "source_id": row.get("source_id"), "occurred_at_utc": row.get("occurred_at_utc")})
            if windows and not in_utc_window(row.get("occurred_at_utc"), windows):
                violation("policy.account_schedule", "Usuário conectou fora da janela permitida", {"user": user, "occurred_at_utc": row.get("occurred_at_utc")})

    return {"policy_schema_version": policy.get("schema_version"), "mode": policy.get("mode", "detect_only"), "status": status_for(violations), "violations": violations, "not_evaluated": not_evaluated}


def run_query(connection: Any, sql: str, max_rows: int) -> tuple[list[dict[str, Any]], int, int]:
    start = time.monotonic()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(sql)
        rows = [{key: json_value(value) for key, value in row.items()} for row in cursor.fetchall()]
    finally:
        cursor.close()
    duration_ms = round((time.monotonic() - start) * 1000)
    return rows[:max_rows], len(rows), duration_ms


AUDIT_EVENT_DOMAINS = {"audit_connections", "audit_ddl", "audit_errors", "audit_sakila_data_access"}


def anonymized(value: Any) -> str | None:
    """Keep correlation possible without placing account names/IPs in the report."""
    if value is None or value == "":
        return None
    return fingerprint(str(value))


def read_audit_events(connection: Any, start_utc: str, max_events: int) -> tuple[list[dict[str, Any]], bool]:
    """Read every available Audit page, stopping at the explicit collection cap.

    audit_log_read() returns a page and uses session state for the next page. A
    single SQL call therefore silently misses recent events on busy servers.
    """
    cursor = connection.cursor()
    events: list[dict[str, Any]] = []
    truncated = False
    first = True
    try:
        while len(events) < max_events:
            page_size = min(500, max_events - len(events))
            if first:
                sql = "SELECT CONVERT(audit_log_read(JSON_OBJECT('start', JSON_OBJECT('timestamp', %s), 'max_array_length', %s)) USING utf8mb4)"
                cursor.execute(sql, (start_utc, page_size))
                first = False
            else:
                cursor.execute("SELECT CONVERT(audit_log_read(JSON_OBJECT('max_array_length', %s)) USING utf8mb4)", (page_size,))
            raw = cursor.fetchone()[0]
            page = json.loads(raw)
            finished = bool(page and page[-1] is None)
            entries = [item for item in page if isinstance(item, dict)]
            events.extend(entries)
            if finished:
                break
            if not entries:
                truncated = True
                break
        else:
            truncated = True
    finally:
        try:
            cursor.execute("SELECT audit_log_read('null')")
            cursor.fetchone()
        finally:
            cursor.close()
    return events, truncated


def audit_rows(domain: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_class = event.get("class")
        event_name = event.get("event")
        occurred_at = event.get("timestamp")
        event_id = event.get("id")
        account = (event.get("account") or {}).get("user")
        source = (event.get("login") or {}).get("ip")
        general = event.get("general_data") or {}
        connection_data = event.get("connection_data") or {}
        table_data = event.get("table_access_data") or {}
        if domain == "audit_connections" and event_class == "connection":
            status = connection_data.get("status")
            rows.append({"occurred_at_utc": occurred_at, "event_id": event_id, "connection_event": event_name,
                         "outcome": "success" if status == 0 else "unknown" if status is None else "failure",
                         "status_code": status, "connection_type": connection_data.get("connection_type"),
                         "actor_id": anonymized(account), "source_id": anonymized(source)})
        elif domain == "audit_errors" and event_class in ("connection", "general"):
            status = connection_data.get("status") if event_class == "connection" else general.get("status")
            if status not in (None, 0):
                query = general.get("query")
                rows.append({"occurred_at_utc": occurred_at, "event_id": event_id, "event_class": event_class,
                             "event_name": event_name, "mysql_error_code": status,
                             "sql_command": general.get("sql_command"), "actor_id": anonymized(account),
                             "source_id": anonymized(source), "sql_fingerprint": anonymized(query)})
        elif domain == "audit_ddl" and event_class == "general" and event_name == "status":
            command = general.get("sql_command")
            if (isinstance(command, str) and command.startswith(("alter_", "create_", "drop_"))) or command in ("truncate", "rename_table"):
                status = general.get("status")
                destructive = command in ("drop_table", "drop_database", "truncate")
                identity_change = command in ("create_user", "alter_user", "drop_user")
                outcome = "success" if status == 0 else "unknown" if status is None else "failure"
                if destructive and outcome == "failure":
                    assessment, reason = "critical", "blocked_destructive_ddl_possible_sabotage"
                    guidance = "Tentativa bloqueada de ação destrutiva; possível sabotagem ou abuso de privilégio. Correlacionar imediatamente."
                elif destructive:
                    assessment, reason = "attention", "successful_destructive_ddl_requires_change_validation"
                    guidance = "Ação destrutiva concluída; validar mudança aprovada, escopo e impacto."
                elif identity_change:
                    assessment, reason = "critical", "identity_change"
                    guidance = "Mudança de identidade/privilégio deve ser validada contra aprovação administrativa."
                else:
                    assessment, reason = "attention", "schema_change"
                    guidance = "Mudança de esquema; validar contra janela de mudança."
                rows.append({"occurred_at_utc": occurred_at, "event_id": event_id, "sql_command": command,
                             "outcome": outcome,
                             "status_code": status, "actor_id": anonymized(account), "source_id": anonymized(source),
                             "sql_fingerprint": anonymized(general.get("query")),
                             "security_assessment": assessment, "assessment_reason": reason,
                             "llm_interpretation": guidance})
        elif domain == "audit_sakila_data_access" and event_class == "table_access" and table_data.get("db") == "sakila":
            event_type = table_data.get("event") or event_name
            command = table_data.get("sql_command")
            rows.append({"occurred_at_utc": occurred_at, "event_id": event_id,
                         "operation": "SELECT" if event_type == "read" and command == "select" else str(event_type).upper(),
                         "sql_command": command, "object_schema": table_data.get("db"), "object_table": table_data.get("table"),
                         "actor_id": anonymized(account), "source_id": anonymized(source),
                         "sql_fingerprint": anonymized(table_data.get("query"))})
    return sorted(rows, key=lambda row: (str(row.get("occurred_at_utc") or ""), row.get("event_id") or -1), reverse=True)


def render_html(report: dict[str, Any], summary: dict[str, Any]) -> str:
    cards: list[str] = []
    for name, domain in report["domains"].items():
        status = domain["status"]
        findings = domain.get("findings", [])
        cards.append(
            "<section class='card'>"
            f"<h2>{html.escape(name)}</h2><p class='badge {status}'>{status}</p>"
            f"<p>{domain.get('row_count_returned', 0)}/{domain.get('row_count_total', 0)} linha(s); {domain.get('duration_ms', 0)} ms</p>"
            f"<pre>{html.escape(json.dumps({'findings': findings, 'sample': domain.get('rows', [])[:10]}, ensure_ascii=False, indent=2))}</pre>"
            "</section>"
        )
    return f"""<!doctype html>
<html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>MySQL Security Snapshot</title><style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f5f7fa;color:#17202a}} header,main{{max-width:1200px;margin:auto;padding:1.25rem}}header{{max-width:none;background:#152935;color:white}}header>div{{max-width:1200px;margin:auto}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:1rem}}.card{{background:#fff;padding:1rem;border-radius:.5rem;box-shadow:0 1px 4px #0002}}.badge{{display:inline-block;padding:.25rem .65rem;border-radius:99px;font-weight:700}}.healthy{{background:#d5f5e3;color:#196f3d}}.attention{{background:#fcf3cf;color:#7d6608}}.critical{{background:#fadbd8;color:#922b21}}.unavailable{{background:#e5e7e9;color:#424949}}pre{{white-space:pre-wrap;overflow:auto;max-height:22rem}}</style></head>
<body><header><div><h1>MySQL Security Snapshot</h1><p>Coletado em {html.escape(report['collected_at'])}</p><p class='badge {summary['overall_status']}'>{summary['overall_status']}</p></div></header>
<main><h2>Resumo</h2><pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre><h2>Domínios</h2><div class='grid'>{''.join(cards)}</div></main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "out")
    parser.add_argument("--audit-window-minutes", type=int, default=1)
    parser.add_argument("--audit-max-events", type=int, default=5000,
                        help="Maximum raw Audit events to page through before reporting truncation")
    parser.add_argument("--max-rows", type=int, default=500)
    parser.add_argument("--history-dir", type=Path, help="Optional directory for immutable detailed snapshots")
    parser.add_argument("--policy-file", type=Path, default=ROOT / "policy" / "security-rules.json")
    args = parser.parse_args()
    if not 1 <= args.audit_window_minutes <= 1440 or not 1 <= args.audit_max_events <= 50000 or args.max_rows < 1:
        parser.error("invalid collection limits")

    password = os.environ.get("MYSQL_PASSWORD")
    config = {
        "host": os.environ.get("MYSQL_HOST", "127.0.0.1"), "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ.get("MYSQL_USER", "security_collector"), "password": password,
        "database": os.environ.get("MYSQL_DATABASE", "mysql"), "connection_timeout": 15,
        "autocommit": True, "use_pure": True,
    }
    if os.environ.get("MYSQL_SSL_CA"):
        config["ssl_ca"] = os.environ["MYSQL_SSL_CA"]
        config["ssl_verify_cert"] = os.environ.get("MYSQL_SSL_VERIFY_CERT", "true").lower() == "true"
    try:
        connection = mysql.connector.connect(**config)
    except mysql.connector.Error as exc:
        # Do not print the connector message: it can contain account/host detail.
        print(json.dumps({"status": "connection_failed", "mysql_errno": exc.errno,
                          "message": "MySQL rejected or could not establish the collector connection."}, ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        bootstrap = connection.cursor()
        bootstrap.execute("SET @audit_start_utc = TIMESTAMPADD(MINUTE, -%s, UTC_TIMESTAMP())", (args.audit_window_minutes,))
        bootstrap.execute("SET @audit_max_events = %s", (args.audit_max_events,))
        bootstrap.close()
        audit_started = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=args.audit_window_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        audit_events: list[dict[str, Any]] = []
        audit_truncated = False
        audit_error: mysql.connector.Error | None = None
        try:
            audit_events, audit_truncated = read_audit_events(connection, audit_started, args.audit_max_events)
        except mysql.connector.Error as exc:
            audit_error = exc
        domains: dict[str, Any] = {}
        for path in sorted(QUERIES.glob("*.sql")):
            domain = path.stem.split("_", 1)[1]
            if domain in AUDIT_EVENT_DOMAINS:
                if audit_error:
                    domains[domain] = {"status": "unavailable", "duration_ms": 0,
                                       "row_count_total": 0, "row_count_returned": 0, "truncated": False,
                                       "rows": [], "findings": [], "query_file": path.name,
                                       "error": {"mysql_errno": audit_error.errno, "sqlstate": audit_error.sqlstate, "message": audit_error.msg}}
                    continue
                started = time.perf_counter()
                audit_domain_rows = audit_rows(domain, audit_events)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                rows = audit_domain_rows[:args.max_rows]
                findings = findings_for(domain, rows)
                domains[domain] = {"status": status_for(findings), "duration_ms": duration_ms,
                                   "row_count_total": len(audit_domain_rows), "row_count_returned": len(rows),
                                   "truncated": audit_truncated or len(audit_domain_rows) > len(rows), "rows": rows,
                                   "findings": findings, "query_file": path.name,
                                   "source": "audit_log_read paged"}
                continue
            try:
                rows, total, duration_ms = run_query(connection, path.read_text(encoding="utf-8"), args.max_rows)
                findings = findings_for(domain, rows)
                domains[domain] = {"status": status_for(findings), "duration_ms": duration_ms,
                                   "row_count_total": total, "row_count_returned": len(rows),
                                   "truncated": total > len(rows), "rows": rows,
                                   "findings": findings, "query_file": path.name}
            except mysql.connector.Error as exc:
                domains[domain] = {"status": "unavailable", "duration_ms": 0,
                                   "row_count_total": 0, "row_count_returned": 0, "truncated": False,
                                   "rows": [], "findings": [], "query_file": path.name,
                                   "error": {"mysql_errno": exc.errno, "sqlstate": exc.sqlstate, "message": exc.msg}}
    finally:
        connection.close()

    data_quality = {
        "unavailable_domains": [name for name, domain in domains.items() if domain["status"] == "unavailable"],
        "truncated_domains": [name for name, domain in domains.items() if domain.get("truncated")],
    }
    try:
        policy = json.loads(args.policy_file.read_text(encoding="utf-8"))
        policy_evaluation = evaluate_policy(policy, domains)
    except (OSError, json.JSONDecodeError) as exc:
        policy_evaluation = {"status": "unavailable", "violations": [], "not_evaluated": ["policy file unavailable"], "error": str(exc)}
    report = {"schema_version": "1.0", "audit_id": str(uuid.uuid4()), "collected_at": utc_now(), "collector": {"name": "mysql-security-monitor", "audit_window_minutes": args.audit_window_minutes, "audit_max_events": args.audit_max_events, "identities": "sha256-truncated"}, "data_quality": data_quality, "policy_evaluation": policy_evaluation, "domains": domains}
    all_findings = [finding for domain in domains.values() for finding in domain.get("findings", [])] + policy_evaluation.get("violations", [])
    statuses = [domain["status"] for domain in domains.values()] + [policy_evaluation["status"]]
    overall = max(statuses, key=lambda value: SEVERITY_ORDER[value], default="unavailable")
    summary = {"schema_version": "1.0", "audit_id": report["audit_id"], "collected_at": report["collected_at"], "overall_status": overall, "critical_findings": sum(f["severity"] == "critical" for f in all_findings), "attention_findings": sum(f["severity"] == "attention" for f in all_findings), "domain_statuses": {name: domain["status"] for name, domain in domains.items()}, "policy_status": policy_evaluation["status"], "policy_violation_count": len(policy_evaluation.get("violations", [])), "data_quality": data_quality}
    atomic_json(args.output_dir / "latest.json", report)
    atomic_json(args.output_dir / "summary.json", summary)
    atomic_text(args.output_dir / "latest.html", render_html(report, summary))
    if args.history_dir:
        atomic_json(args.history_dir / f"{report['collected_at'].replace(':', '').replace('.', '')}.json", report)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
