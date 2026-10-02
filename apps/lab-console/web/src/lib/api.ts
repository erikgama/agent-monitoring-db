export type User = {
  username: string;
  role: "viewer" | "operator" | "dba_approver";
  csrf: string;
};
export type Job = {
  id: string;
  action: string;
  execute: boolean;
  status: string;
  mode: string;
  created_at: string;
  updated_at: string;
  reason?: string;
};
export type LabEvent = {
  event_id: string;
  correlation_id: string;
  job_id: string;
  occurred_at: string;
  source: string;
  type: string;
  severity: string;
  payload: Record<string, string | number | boolean | null>;
};
export type Artifact = {
  id: string;
  source: string;
  location: string;
  name: string;
  audit_id: string | null;
  request_id?: string | null;
  result_id?: string | null;
  alert_id?: string;
  category: string;
  severity: string;
  timestamp: string;
  retention: string;
  html_available: boolean;
  sha256: string;
  size: number;
  schema_version: string;
};
export type State = {
  mode: "demo" | "integrated";
  runner: {
    online: boolean;
    simulated: boolean;
    execute_enabled: boolean;
    last_seen: number;
    actions: string[];
  };
  jobs: Job[];
  events: LabEvent[];
  metrics: {
    alerts_detected: number;
    deliveries_confirmed: number;
    delivery_failures: number;
    dba_records: number;
  };
  actions: Record<
    string,
    { agent: string; label: string; timeout: number; sql?: string }
  >;
  artifacts: Artifact[];
  timestamp: string;
  contracts: string[];
};
export type Proposal = {
  id: string;
  action: string;
  sql: string;
  schema: string;
  timeout_seconds: number;
  row_limit: number;
  byte_limit: number;
  impact: string;
  rollback: string;
  profile: string;
};
export type ChatReply = {
  answer: string;
  citations: { label: string; artifact_id: string }[];
  proposal?: Proposal;
};
export type IncidentEvidence = {
  name: string;
  label: string;
  format: "html" | "json";
};
export type Incident = {
  source: "health-check" | "audit";
  record_id: string;
  alert_id: string;
  audit_id: string;
  title: string;
  summary: string;
  severity: "info" | "warning" | "critical";
  category: string;
  detected_at: string;
  received_at?: string;
  rule_correction?: string | null;
  evidence: IncidentEvidence[];
  analysis_status: "historical" | "processing" | "ready" | "failed";
};
export type IncidentDetail = Incident & {
  analysis?: string | null;
  analysis_error?: string;
};
export type RefactorDelivery = {
  id: string;
  title: string;
  created_at: string;
  status: string;
  scope: string;
  change_summary: string;
  before_seconds: number;
  after_seconds: number;
  saved_seconds?: number | null;
  improvement_percent?: number | null;
  speedup?: number | null;
  original_sql: string;
  proposed_sql: string;
  report_path: string;
  original_path: string;
  proposal_path: string;
  production_ready: false;
  pipeline_mode: "simulation";
};
export const ACTIVE = new Set([
  "queued",
  "validating",
  "starting",
  "ready",
  "running",
  "stopping",
]);
export async function api<T>(
  path: string,
  options: RequestInit = {},
  csrf?: string,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "x-csrf-token": csrf } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: "connection_failed" }));
    throw new Error(
      typeof body.detail === "string" ? body.detail : "invalid_request",
    );
  }
  return response.json();
}
export function artifactUrl(item: Artifact, format: "html" | "json") {
  return `/api/artifacts/${encodeURIComponent(item.id)}/${format}?sha=${encodeURIComponent(item.sha256)}`;
}
export function incidentEvidenceUrl(
  incident: Incident,
  evidence: IncidentEvidence,
) {
  return `/api/dba/incidents/${encodeURIComponent(incident.source)}/${encodeURIComponent(incident.record_id)}/evidence/${encodeURIComponent(evidence.name)}`;
}
export async function incidentEvidenceContent(
  incident: Incident,
  evidence: IncidentEvidence,
): Promise<string> {
  const response = await fetch(incidentEvidenceUrl(incident, evidence));
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: "incident_evidence_unavailable" }));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "incident_evidence_unavailable",
    );
  }
  return response.text();
}
export const labels: Record<string, string> = {
  queued: "Na fila",
  validating: "Validando",
  starting: "Iniciando",
  ready: "Pronto",
  running: "Executando",
  stopping: "Encerrando",
  succeeded: "Concluído",
  failed: "Falha",
  cancelled: "Encerrado",
  interrupted: "Interrompido",
  timed_out: "Tempo excedido",
  offline: "Desconectado",
  idle: "Em espera",
  "on-demand": "Sob demanda",
};
export const eventLabels: Record<string, string> = {
  "agent.starting": "Agente iniciando",
  "agent.ready": "Agente pronto",
  "lab.progress": "Progresso do laboratório",
  "job.validating": "Validando execução",
  "job.starting": "Iniciando execução",
  "job.running": "Execução em andamento",
  "job.stopping": "Encerrando processos",
  "job.cancelled": "Execução encerrada",
  "job.interrupted": "Execução interrompida",
  "job.failed": "Falha na execução",
  "job.timed_out": "Prazo excedido",
  "notification.dry_run": "Entrega validada · dry-run",
  "notification.skipped": "Entrega não solicitada",
  "notification.failed": "Falha na entrega",
  "lab.started": "Laboratório iniciado",
  "alert.detected": "Alerta detectado",
  "mcp.validated": "Contrato validado",
  "notification.sent": "E-mail enviado",
  "dba.recorded": "Evidência registrada",
  "artifact.available": "Relatório disponível",
  "alert.suppressed": "Repetição suprimida",
  "audit.attempt.denied": "Tentativa negada · 1142",
  "job.succeeded": "Execução concluída",
  "job.ready": "Agente pronto",
  "query.completed": "Consulta concluída",
};
export function stamp(value: string) {
  return new Date(value).toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
