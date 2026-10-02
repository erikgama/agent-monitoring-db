"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import {
  Activity,
  ArrowLeft,
  BellRing,
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  GitCompareArrows,
  HeartPulse,
  RefreshCw,
  Rocket,
  Send,
  ShieldAlert,
  Trash2,
  Wrench,
} from "lucide-react";
import { Modal } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  api,
  incidentEvidenceContent,
  incidentEvidenceUrl,
  type Incident,
  type IncidentDetail,
  type IncidentEvidence,
  type RefactorDelivery,
  type User,
} from "@/lib/api";

type Filter = "all" | "health-check" | "audit";
type DbaView = "menu" | "alerts" | "refactor";
type Message = { role: "user" | "assistant"; text: string };

function sourceLabel(source: Incident["source"]) {
  return source === "health-check" ? "Health Check" : "Audit";
}

function incidentKey(incident: Incident) {
  return `${incident.source}/${incident.record_id}`;
}

function formatEvidence(content: string, format: IncidentEvidence["format"]) {
  if (format !== "json") return content;
  try {
    return JSON.stringify(JSON.parse(content), null, 2);
  } catch {
    return content;
  }
}

export function DbaChat({
  user,
  onClose,
}: {
  user: User;
  onClose: () => void;
}) {
  const [view, setView] = useState<DbaView>("menu");
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedKey, setSelectedKey] = useState("");
  const [openEvidence, setOpenEvidence] = useState<IncidentEvidence | null>(
    null,
  );
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<{
    key: string;
    text: string;
  } | null>(null);
  const [questionError, setQuestionError] = useState("");
  const [deletingKey, setDeletingKey] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [selectedDeliveryId, setSelectedDeliveryId] = useState("");
  const [simulatedDeliveryId, setSimulatedDeliveryId] = useState("");
  const [deletingDeliveryId, setDeletingDeliveryId] = useState("");
  const [deliveryDeleteError, setDeliveryDeleteError] = useState("");
  const queryClient = useQueryClient();

  const incidentsQuery = useQuery({
    queryKey: ["dba-incidents"],
    queryFn: () => api<Incident[]>("/dba/incidents"),
    refetchInterval: 5000,
  });
  const incidents = useMemo(
    () => incidentsQuery.data || [],
    [incidentsQuery.data],
  );
  const deliveriesQuery = useQuery({
    queryKey: ["dba-refactor-deliveries"],
    queryFn: () => api<RefactorDelivery[]>("/dba/refactor-deliveries"),
    refetchInterval: view === "refactor" ? 5000 : false,
  });
  const deliveries = useMemo(
    () => deliveriesQuery.data || [],
    [deliveriesQuery.data],
  );
  const selectedDelivery =
    deliveries.find((delivery) => delivery.id === selectedDeliveryId) ||
    deliveries[0];
  const filtered = useMemo(
    () =>
      incidents.filter(
        (incident) => filter === "all" || incident.source === filter,
      ),
    [filter, incidents],
  );
  const selected =
    filtered.find((incident) => incidentKey(incident) === selectedKey) ||
    filtered[0];
  const currentKey = selected ? incidentKey(selected) : "";

  const conversationQuery = useQuery({
    queryKey: [
      "dba-incident-conversation",
      selected?.source,
      selected?.record_id,
    ],
    enabled: !!selected && view === "alerts",
    retry: false,
    queryFn: () =>
      api<{ messages: Message[] }>(
        `/dba/incidents/${encodeURIComponent(selected!.source)}/${encodeURIComponent(selected!.record_id)}/conversation`,
      ),
  });

  const detailQuery = useQuery({
    queryKey: ["dba-incident", selected?.source, selected?.record_id],
    enabled: !!selected,
    retry: false,
    queryFn: () =>
      api<IncidentDetail>(
        `/dba/incidents/${encodeURIComponent(selected!.source)}/${encodeURIComponent(selected!.record_id)}`,
      ),
    refetchInterval: 3000,
  });
  const detail = detailQuery.data;
  const evidenceQuery = useQuery({
    queryKey: [
      "dba-incident-evidence",
      selected?.source,
      selected?.record_id,
      openEvidence?.name,
    ],
    enabled: !!selected && openEvidence?.format === "json",
    retry: false,
    queryFn: () => incidentEvidenceContent(selected!, openEvidence!),
  });
  const evidenceContent = useMemo(
    () =>
      evidenceQuery.data && openEvidence
        ? formatEvidence(evidenceQuery.data, openEvidence.format)
        : "",
    [evidenceQuery.data, openEvidence],
  );
  const conversation = conversationQuery.data?.messages || [];
  const healthCount = incidents.filter(
    (incident) => incident.source === "health-check",
  ).length;
  const auditCount = incidents.length - healthCount;
  const criticalCount = incidents.filter(
    (incident) => incident.severity === "critical",
  ).length;

  async function ask(suggestedQuestion?: string) {
    if (!selected || asking) return;
    const text = (suggestedQuestion || question).trim();
    if (!text) return;
    setQuestion("");
    setQuestionError("");
    setAsking(true);
    setPendingQuestion({ key: incidentKey(selected), text });
    try {
      const response = await api<{ answer: string; messages: Message[] }>(
        `/dba/incidents/${encodeURIComponent(selected.source)}/${encodeURIComponent(selected.record_id)}/question`,
        {
          method: "POST",
          body: JSON.stringify({ message: text }),
        },
        user.csrf,
      );
      queryClient.setQueryData(
        ["dba-incident-conversation", selected.source, selected.record_id],
        { messages: response.messages },
      );
    } catch (error) {
      setQuestionError((error as Error).message);
      setQuestion(text);
    } finally {
      setAsking(false);
      setPendingQuestion(null);
    }
  }

  async function deleteIncident(incident: Incident) {
    const key = incidentKey(incident);
    if (deletingKey) return;
    const confirmed = window.confirm(
      `Excluir definitivamente esta ocorrência de ${sourceLabel(incident.source)}?\n\n${incident.title}\n\nOs arquivos do alerta e o resumo associado serão apagados.`,
    );
    if (!confirmed) return;

    setDeleteError("");
    setDeletingKey(key);
    try {
      await api<{ deleted: boolean }>(
        `/dba/incidents/${encodeURIComponent(incident.source)}/${encodeURIComponent(incident.record_id)}`,
        { method: "DELETE" },
        user.csrf,
      );
      queryClient.setQueryData<Incident[]>(["dba-incidents"], (current) =>
        (current || []).filter((item) => incidentKey(item) !== key),
      );
      queryClient.removeQueries({
        queryKey: ["dba-incident", incident.source, incident.record_id],
      });
      queryClient.removeQueries({
        queryKey: [
          "dba-incident-evidence",
          incident.source,
          incident.record_id,
        ],
      });
      queryClient.removeQueries({
        queryKey: [
          "dba-incident-conversation",
          incident.source,
          incident.record_id,
        ],
      });
      await queryClient.invalidateQueries({ queryKey: ["state"] });
      if (currentKey === key) {
        setSelectedKey("");
        setOpenEvidence(null);
      }
      await incidentsQuery.refetch();
    } catch {
      setDeleteError(
        "Não foi possível excluir a ocorrência. Atualize a lista e tente novamente.",
      );
    } finally {
      setDeletingKey("");
    }
  }

  async function deleteDelivery(delivery: RefactorDelivery) {
    if (deletingDeliveryId) return;
    const confirmed = window.confirm(
      `Excluir definitivamente esta entrega do Refactor?\n\n${delivery.title}\n\nO relatório, as SQLs original e refatorada, a cópia recebida pelo DBA e a deduplicação desta execução serão apagados. A mesma consulta poderá ser processada novamente.`,
    );
    if (!confirmed) return;

    setDeliveryDeleteError("");
    setDeletingDeliveryId(delivery.id);
    try {
      await api<{ deleted: boolean }>(
        `/dba/refactor-deliveries/${encodeURIComponent(delivery.id)}`,
        { method: "DELETE" },
        user.csrf,
      );
      queryClient.setQueryData<RefactorDelivery[]>(
        ["dba-refactor-deliveries"],
        (current) => (current || []).filter((item) => item.id !== delivery.id),
      );
      if (selectedDeliveryId === delivery.id) setSelectedDeliveryId("");
      if (simulatedDeliveryId === delivery.id) setSimulatedDeliveryId("");
      await deliveriesQuery.refetch();
    } catch {
      setDeliveryDeleteError(
        "Não foi possível excluir a entrega do Refactor. Atualize a lista e tente novamente.",
      );
    } finally {
      setDeletingDeliveryId("");
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={
        view === "menu"
          ? "DBA"
          : view === "alerts"
            ? "Central de ocorrências do DBA"
            : "Refactor"
      }
      description={
        view === "menu"
          ? "Escolha a área que deseja consultar"
          : view === "alerts"
            ? "Alertas recebidos do Health Check e Audit · análise somente dos arquivos preservados"
            : "Propostas recebidas pelo DBA · comparação e pipeline simulado"
      }
      full
      className="dba-modal"
    >
      {view === "menu" ? (
        <div className="dba-entry-menu">
          <div className="dba-entry-copy">
            <span>Central operacional</span>
            <h3>O que você deseja analisar?</h3>
            <p>
              Consulte as ocorrências recebidas ou as propostas devolvidas pelo
              agente Refactor ao DBA.
            </p>
          </div>
          <div className="dba-entry-options">
            <button type="button" onClick={() => setView("alerts")}>
              <span className="dba-entry-icon alerts">
                <BellRing size={24} />
              </span>
              <span>
                <small>MONITORAMENTO</small>
                <b>Alertas</b>
                <em>
                  Health Check e Audit, com evidências e conversa contextual.
                </em>
              </span>
              <strong>{incidents.length}</strong>
            </button>
            <button type="button" onClick={() => setView("refactor")}>
              <span className="dba-entry-icon refactor">
                <GitCompareArrows size={24} />
              </span>
              <span>
                <small>CONSULTAS</small>
                <b>Refactor</b>
                <em>
                  Compare a SQL original, a proposta e os tempos de laboratório.
                </em>
              </span>
              <strong>{deliveries.length}</strong>
            </button>
          </div>
        </div>
      ) : (
        <div className="dba-section-view">
          <button
            type="button"
            className="dba-back-button"
            onClick={() => {
              setView("menu");
              setOpenEvidence(null);
            }}
          >
            <ArrowLeft size={14} />
            Voltar ao menu do DBA
          </button>
          {view === "alerts" ? (
            <div className="incident-dashboard">
              <aside className="incident-sidebar">
                <div className="incident-counters">
                  <div>
                    <HeartPulse size={16} />
                    <span>Health Check</span>
                    <b>{healthCount}</b>
                  </div>
                  <div>
                    <ShieldAlert size={16} />
                    <span>Audit</span>
                    <b>{auditCount}</b>
                  </div>
                  <div>
                    <Activity size={16} />
                    <span>Críticos</span>
                    <b>{criticalCount}</b>
                  </div>
                </div>
                <div
                  className="incident-filters tabs"
                  aria-label="Filtrar ocorrências"
                >
                  {(
                    [
                      ["all", "Todos"],
                      ["health-check", "Health"],
                      ["audit", "Audit"],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      className={filter === value ? "selected" : ""}
                      onClick={() => {
                        setFilter(value);
                        setSelectedKey("");
                        setOpenEvidence(null);
                      }}
                    >
                      {label}
                    </button>
                  ))}
                  <button
                    className="incident-refresh"
                    aria-label="Atualizar ocorrências"
                    onClick={() => incidentsQuery.refetch()}
                  >
                    <RefreshCw
                      size={14}
                      className={incidentsQuery.isFetching ? "spin" : ""}
                    />
                  </button>
                </div>
                {deleteError && (
                  <p className="incident-delete-error" role="alert">
                    {deleteError}
                  </p>
                )}
                <div className="incident-list">
                  {filtered.map((incident) => {
                    const key = incidentKey(incident);
                    const selected = currentKey === key;
                    return (
                      <div
                        key={key}
                        className={`incident-list-row ${selected ? "selected" : ""}`}
                      >
                        {user.role === "dba_approver" && (
                          <button
                            type="button"
                            className="incident-delete-button"
                            aria-label={`Excluir ocorrência: ${incident.title}`}
                            title="Excluir ocorrência"
                            disabled={!!deletingKey}
                            onClick={() => deleteIncident(incident)}
                          >
                            {deletingKey === key ? (
                              <RefreshCw className="spin" size={13} />
                            ) : (
                              <Trash2 size={13} />
                            )}
                          </button>
                        )}
                        <button
                          type="button"
                          className="incident-list-item"
                          onClick={() => {
                            setSelectedKey(key);
                            setOpenEvidence(null);
                          }}
                        >
                          <span
                            className={`incident-source ${incident.source}`}
                          >
                            {incident.source === "health-check" ? (
                              <HeartPulse size={15} />
                            ) : (
                              <ShieldAlert size={15} />
                            )}
                          </span>
                          <span>
                            <small>
                              {sourceLabel(incident.source)} ·{" "}
                              {incident.category}
                            </small>
                            <b>{incident.title}</b>
                            <time>
                              {new Date(incident.detected_at).toLocaleString(
                                "pt-BR",
                              )}
                            </time>
                          </span>
                          <i className={`severity-dot ${incident.severity}`} />
                        </button>
                      </div>
                    );
                  })}
                  {!filtered.length && !incidentsQuery.isLoading && (
                    <div className="incident-empty">
                      <Database size={24} />
                      <b>Nenhuma ocorrência recebida</b>
                      <span>
                        Esta tela exibirá somente alertas preservados nas
                        inboxes do DBA.
                      </span>
                    </div>
                  )}
                  {incidentsQuery.isLoading && (
                    <div className="incident-empty">
                      <RefreshCw className="spin" size={22} />
                      <span>Carregando ocorrências…</span>
                    </div>
                  )}
                </div>
              </aside>

              <section className="incident-detail">
                {selected ? (
                  openEvidence ? (
                    <div className="incident-evidence-viewer">
                      <header className="incident-evidence-heading">
                        <button
                          type="button"
                          onClick={() => setOpenEvidence(null)}
                        >
                          <ArrowLeft size={16} />
                          Voltar para a ocorrência
                        </button>
                        <div>
                          <span>{openEvidence.format.toUpperCase()}</span>
                          <h2>{openEvidence.label}</h2>
                          <small>{openEvidence.name}</small>
                        </div>
                      </header>
                      <div className="incident-evidence-content">
                        {openEvidence.format === "html" ? (
                          <iframe
                            title={openEvidence.label}
                            sandbox=""
                            src={incidentEvidenceUrl(selected, openEvidence)}
                          />
                        ) : evidenceQuery.isLoading ? (
                          <div className="incident-analysis-state">
                            <RefreshCw className="spin" />
                            <b>Carregando evidência…</b>
                          </div>
                        ) : evidenceQuery.isError ? (
                          <div className="incident-analysis-state failed">
                            <ShieldAlert />
                            <b>Não foi possível abrir esta evidência</b>
                            <span>
                              {(evidenceQuery.error as Error).message}
                            </span>
                          </div>
                        ) : (
                          <pre>{evidenceContent}</pre>
                        )}
                      </div>
                    </div>
                  ) : (
                    <>
                      <header className="incident-detail-heading">
                        <div className="incident-detail-meta">
                          <div>
                            <span className={`source-badge ${selected.source}`}>
                              {sourceLabel(selected.source)}
                            </span>
                            <span
                              className={`severity-badge ${selected.severity}`}
                            >
                              {selected.severity}
                            </span>
                          </div>
                          <time>
                            {new Date(selected.detected_at).toLocaleString(
                              "pt-BR",
                            )}
                          </time>
                        </div>
                        <div className="incident-detail-main">
                          <div className="incident-alert-copy">
                            <h2>{selected.title}</h2>
                            <p>{selected.summary}</p>
                          </div>
                          <div className="incident-evidence-bar">
                            <span>
                              Evidências recebidas · dados sensíveis ocultos
                            </span>
                            {selected.evidence.map((evidence) => (
                              <button
                                type="button"
                                key={evidence.name}
                                onClick={() => setOpenEvidence(evidence)}
                              >
                                <FileText size={13} />
                                {evidence.label}
                              </button>
                            ))}
                          </div>
                        </div>
                      </header>

                      {selected.rule_correction && (
                        <div className="incident-rule-correction">
                          <ShieldAlert size={15} />
                          <span>{selected.rule_correction}</span>
                        </div>
                      )}

                      <div className="incident-conversation">
                        <div className="incident-messages" aria-live="polite">
                          <div className="assistant incident-summary-message">
                            <div className="incident-section-title">
                              <Database size={17} />
                              <b>DBA · resumo da ocorrência</b>
                              <span>
                                gerado somente a partir das evidências acima
                              </span>
                            </div>
                            {detailQuery.isLoading ||
                            detail?.analysis_status === "processing" ? (
                              <div className="incident-analysis-state">
                                <RefreshCw className="spin" />
                                <b>O resumo está sendo gerado…</b>
                              </div>
                            ) : detail?.analysis_status === "ready" &&
                              detail.analysis ? (
                              <div className="incident-summary markdown-content">
                                <ReactMarkdown skipHtml>
                                  {detail.analysis}
                                </ReactMarkdown>
                              </div>
                            ) : detail?.analysis_status === "historical" ? (
                              <div className="incident-analysis-state">
                                <FileText />
                                <b>
                                  Ocorrência histórica sem resumo automático
                                </b>
                                <span>
                                  Somente novas ocorrências recebidas com o
                                  serviço ativo são resumidas. As evidências
                                  continuam disponíveis acima.
                                </span>
                              </div>
                            ) : (
                              <div className="incident-analysis-state failed">
                                <ShieldAlert />
                                <b>Não foi possível gerar o resumo</b>
                                <span>
                                  As evidências originais continuam disponíveis
                                  para leitura.
                                </span>
                              </div>
                            )}
                          </div>
                          {conversation.map((message, index) => (
                            <div key={index} className={message.role}>
                              <b>
                                {message.role === "assistant" ? "DBA" : "Você"}
                              </b>
                              {message.role === "assistant" ? (
                                <div className="message-markdown markdown-content">
                                  <ReactMarkdown skipHtml>
                                    {message.text}
                                  </ReactMarkdown>
                                </div>
                              ) : (
                                <p>{message.text}</p>
                              )}
                            </div>
                          ))}
                          {pendingQuestion?.key === currentKey && (
                            <>
                              <div className="user pending">
                                <b>Você</b>
                                <p>{pendingQuestion.text}</p>
                              </div>
                              <div className="assistant pending">
                                <b>DBA</b>
                                <p>
                                  <RefreshCw className="spin" size={11} />
                                  Analisando as evidências desta ocorrência…
                                </p>
                              </div>
                            </>
                          )}
                        </div>
                        <form
                          onSubmit={(event) => {
                            event.preventDefault();
                            ask();
                          }}
                        >
                          <input
                            aria-label="Pergunta sobre a ocorrência"
                            placeholder="Pergunte sobre esta ocorrência…"
                            value={question}
                            maxLength={2000}
                            onChange={(event) =>
                              setQuestion(event.target.value)
                            }
                          />
                          <Button
                            size="icon"
                            aria-label="Enviar pergunta"
                            disabled={asking || !question.trim()}
                          >
                            <Send size={17} />
                          </Button>
                        </form>
                        {questionError && (
                          <p className="error-message" role="alert">
                            {questionError}
                          </p>
                        )}
                      </div>
                    </>
                  )
                ) : (
                  <div className="incident-detail-empty">
                    <Database size={32} />
                    <h3>Ocorrências do DBA</h3>
                    <p>
                      Quando o MCP registrar um alerta validado, ele aparecerá
                      aqui com seus arquivos originais.
                    </p>
                  </div>
                )}
              </section>
            </div>
          ) : (
            <div className="refactor-dashboard">
              <aside className="refactor-sidebar">
                <div className="refactor-sidebar-heading">
                  <Wrench size={16} />
                  <span>
                    <b>Entregas do Refactor</b>
                    <small>{deliveries.length} recebida(s)</small>
                  </span>
                  <button
                    type="button"
                    aria-label="Atualizar entregas do Refactor"
                    onClick={() => deliveriesQuery.refetch()}
                  >
                    <RefreshCw
                      size={14}
                      className={deliveriesQuery.isFetching ? "spin" : ""}
                    />
                  </button>
                </div>
                {deliveryDeleteError && (
                  <p className="incident-delete-error" role="alert">
                    {deliveryDeleteError}
                  </p>
                )}
                <div className="refactor-delivery-list">
                  {deliveries.map((delivery) => {
                    const selected = selectedDelivery?.id === delivery.id;
                    return (
                      <div
                        key={delivery.id}
                        className={`refactor-delivery-row ${user.role === "dba_approver" ? "deletable" : ""} ${selected ? "selected" : ""}`}
                      >
                        {user.role === "dba_approver" && (
                          <button
                            type="button"
                            className="refactor-delivery-delete"
                            aria-label={`Excluir entrega do Refactor: ${delivery.title}`}
                            title="Excluir entrega do Refactor"
                            disabled={!!deletingDeliveryId}
                            onClick={() => deleteDelivery(delivery)}
                          >
                            {deletingDeliveryId === delivery.id ? (
                              <RefreshCw className="spin" size={13} />
                            ) : (
                              <Trash2 size={13} />
                            )}
                          </button>
                        )}
                        <button
                          type="button"
                          className="refactor-delivery-select"
                          onClick={() => {
                            setSelectedDeliveryId(delivery.id);
                            setSimulatedDeliveryId("");
                          }}
                        >
                          <span>
                            <GitCompareArrows size={15} />
                          </span>
                          <span>
                            <b>{delivery.title}</b>
                            <small>
                              {delivery.created_at
                                ? new Date(delivery.created_at).toLocaleString(
                                    "pt-BR",
                                  )
                                : delivery.id}
                            </small>
                          </span>
                        </button>
                      </div>
                    );
                  })}
                  {!deliveries.length && !deliveriesQuery.isLoading && (
                    <div className="refactor-empty">
                      <GitCompareArrows size={26} />
                      <b>Nenhuma proposta recebida</b>
                      <span>
                        As próximas entregas do Refactor ao DBA aparecerão aqui.
                      </span>
                    </div>
                  )}
                  {deliveriesQuery.isLoading && (
                    <div className="refactor-empty">
                      <RefreshCw className="spin" size={22} />
                      <span>Carregando propostas…</span>
                    </div>
                  )}
                </div>
              </aside>

              <section className="refactor-detail">
                {selectedDelivery ? (
                  <>
                    <header className="refactor-detail-heading">
                      <div>
                        <span className="refactor-status">
                          {selectedDelivery.status || "Recebida pelo DBA"}
                        </span>
                        <h2>{selectedDelivery.title}</h2>
                        <p>{selectedDelivery.scope}</p>
                      </div>
                      <div className="refactor-timing-summary">
                        <div>
                          <Clock3 size={15} />
                          <small>ANTES</small>
                          <b>
                            {selectedDelivery.before_seconds.toLocaleString(
                              "pt-BR",
                              { maximumFractionDigits: 6 },
                            )}{" "}
                            s
                          </b>
                        </div>
                        <div>
                          <CheckCircle2 size={15} />
                          <small>DEPOIS</small>
                          <b>
                            {selectedDelivery.after_seconds.toLocaleString(
                              "pt-BR",
                              { maximumFractionDigits: 6 },
                            )}{" "}
                            s
                          </b>
                        </div>
                        <div>
                          <Activity size={15} />
                          <small>GANHO NO LAB</small>
                          <b>
                            {selectedDelivery.improvement_percent != null
                              ? `${selectedDelivery.improvement_percent.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%`
                              : "—"}
                          </b>
                        </div>
                      </div>
                    </header>

                    <div className="refactor-detail-body">
                      {selectedDelivery.change_summary && (
                        <div className="refactor-change-summary">
                          <GitCompareArrows size={15} />
                          <span>{selectedDelivery.change_summary}</span>
                        </div>
                      )}
                      <div className="refactor-sql-comparison">
                        <article>
                          <header>
                            <span>SQL original</span>
                            <small>{selectedDelivery.original_path}</small>
                          </header>
                          <pre>{selectedDelivery.original_sql}</pre>
                        </article>
                        <article className="proposed">
                          <header>
                            <span>SQL refatorada</span>
                            <small>{selectedDelivery.proposal_path}</small>
                          </header>
                          <pre>{selectedDelivery.proposed_sql}</pre>
                        </article>
                      </div>
                    </div>

                    <footer className="refactor-pipeline">
                      <div>
                        <b>Pipeline de produção</b>
                        <span>
                          Demonstração visual: nenhum SQL, job ou mudança será
                          executado.
                        </span>
                      </div>
                      {simulatedDeliveryId === selectedDelivery.id ? (
                        <span className="refactor-simulated">
                          <CheckCircle2 size={16} />
                          Pipeline simulado com sucesso
                        </span>
                      ) : (
                        <Button
                          type="button"
                          onClick={() =>
                            setSimulatedDeliveryId(selectedDelivery.id)
                          }
                        >
                          <Rocket size={16} />
                          Enviar para produção
                          <small>SIMULAÇÃO</small>
                        </Button>
                      )}
                    </footer>
                  </>
                ) : (
                  <div className="refactor-empty detail">
                    <Database size={32} />
                    <b>Caixa de entrada do Refactor</b>
                    <span>
                      Quando o Refactor devolver uma proposta ao DBA, a
                      comparação aparecerá aqui.
                    </span>
                  </div>
                )}
              </section>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
