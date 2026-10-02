"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  Command,
  Database,
  FileText,
  FlaskConical,
  GitBranch,
  Layers3,
  Mail,
  MessagesSquare,
  Play,
  Radio,
  ShieldCheck,
  Terminal,
  Workflow,
  X,
} from "lucide-react";
import {
  AgentCanvas,
  StatusBadge,
  type AgentResourceLink,
} from "@/components/canvas";
import { ArtifactViewer } from "@/components/artifact-viewer";
import { DbaChat } from "@/components/dba-chat";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/dialog";
import { ResourceEditor } from "@/components/resource-editor";
import {
  ACTIVE,
  api,
  eventLabels,
  stamp,
  type State,
  type User,
  type LabEvent,
} from "@/lib/api";

const errors: Record<string, string> = {
  runner_offline: "O runner local está desconectado.",
  runner_scope_denied: "A execução precisa estar habilitada no runner local.",
  resource_busy: "Já existe uma operação usando este agente.",
  reauth_required:
    "Sua confirmação expirou. Saia e entre novamente para executar.",
  invalid_credentials: "Usuário ou senha inválidos.",
  session_expired: "Sua sessão expirou. Entre novamente.",
  rate_limited: "Muitas solicitações. Aguarde um minuto.",
  operator_required: "Seu perfil permite somente visualização.",
};

const confirmationDescriptions: Record<string, string> = {
  "health.lab":
    "Ativa o serviço de monitoração do Health Check no schema sakila.",
  "audit.lab": "Ativa o serviço de monitoração do Audit no schema sakila.",
  "refactor.lab":
    "Ativa o serviço do Refactor para receber consultas lentas e validá-las no sakila_dev.",
  "health.load":
    "Inicia consultas no banco sakila e gera entradas no Slow Query Log.",
  "audit.drop": "Executa o comando DROP no schema sakila.",
  "audit.alter": "Executa o comando ALTER no schema sakila.",
  "health.collect": "Executa uma coleta atual no schema sakila.",
  "lab.three":
    "Ativa os serviços e inicia os três processos controlados no schema sakila.",
};

const serviceActions = new Set(["health.lab", "audit.lab", "refactor.lab"]);
const commandActions = new Set(["audit.drop", "audit.alter"]);
const queryActions = new Set(["health.load"]);
const staleSessionErrors = new Set(["csrf_denied", "session_expired"]);

export function Console() {
  const queryClient = useQueryClient();
  const {
    data: session,
    error: sessionError,
    refetch: retrySession,
  } = useQuery({
    queryKey: ["session"],
    queryFn: () => api<User>("/access", { method: "POST" }),
    retry: false,
    refetchInterval: 20 * 60 * 1000,
  });
  const user = session;
  const {
    data: state,
    error: stateError,
    refetch,
  } = useQuery({
    queryKey: ["state"],
    queryFn: async () => {
      try {
        return await api<State>("/state");
      } catch (cause) {
        if ((cause as Error).message !== "session_expired") throw cause;

        const renewedSession = await api<User>("/access", {
          method: "POST",
        });
        queryClient.setQueryData(["session"], renewedSession);
        return api<State>("/state");
      }
    },
    enabled: !!user,
    refetchInterval: 3000,
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<{
    action: string;
    execute: boolean;
  } | null>(null);
  const [typed, setTyped] = useState("");
  const [panel, setPanel] = useState<
    "artifacts" | "chat" | "jobs" | "guide" | null
  >(null);
  const [source, setSource] = useState<string | undefined>();
  const [artifactTarget, setArtifactTarget] = useState<{
    id?: string;
    auditId?: string;
  }>();
  const [selectedEvent, setSelectedEvent] = useState<LabEvent | null>(null);
  const [resource, setResource] = useState<{
    agent: string;
    link: AgentResourceLink;
  } | null>(null);
  const [feed, setFeed] = useState<"events" | "console">("events");
  const [activityOpen, setActivityOpen] = useState(false);
  const renewLocalSession = useCallback(async () => {
    const renewed = await api<User>("/access", { method: "POST" });
    queryClient.setQueryData(["session"], renewed);
    return renewed;
  }, [queryClient]);
  useEffect(() => {
    if (!user) return;
    const events = new EventSource("/api/events");
    events.onmessage = () =>
      queryClient.invalidateQueries({ queryKey: ["state"] });
    return () => events.close();
  }, [user, queryClient]);
  const fail = useCallback(
    (e: unknown) =>
      setError(errors[(e as Error).message] || (e as Error).message),
    [],
  );
  const action = useCallback((name: string, execute: boolean) => {
    setConfirm({ action: name, execute });
    setTyped("");
    setError("");
  }, []);
  const stop = useCallback(
    async (id: string) => {
      if (!user) return;
      try {
        await api(`/jobs/${id}/stop`, { method: "POST" }, user.csrf);
        await refetch();
      } catch (e) {
        if (!staleSessionErrors.has((e as Error).message)) {
          fail(e);
          return;
        }
        try {
          const renewed = await renewLocalSession();
          await api(`/jobs/${id}/stop`, { method: "POST" }, renewed.csrf);
          await refetch();
        } catch (retryError) {
          fail(retryError);
        }
      }
    },
    [user, refetch, fail, renewLocalSession],
  );
  const artifacts = useCallback(
    (agent?: string, target?: { id?: string; auditId?: string }) => {
      setSource(agent);
      setArtifactTarget(target);
      setPanel("artifacts");
    },
    [],
  );
  const actions = useMemo(
    () => ({
      onAction: action,
      onStop: stop,
      onArtifacts: artifacts,
      onChat: () => setPanel("chat"),
      onInspect: setSelectedEvent,
      onResources: (agent: string, link: AgentResourceLink) =>
        setResource({ agent, link }),
      canOperate: !!user && user.role !== "viewer" && !!state?.runner.online,
    }),
    [action, stop, artifacts, user, state?.runner.online],
  );
  async function start() {
    if (!confirm || !user) return;
    setBusy(true);
    setError("");
    const payload = {
      ...confirm,
      confirmation: typed,
      request_id: crypto.randomUUID(),
    };
    try {
      await api(
        "/jobs",
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
        user.csrf,
      );
      setConfirm(null);
      await refetch();
    } catch (e) {
      if (!staleSessionErrors.has((e as Error).message)) {
        fail(e);
      } else {
        try {
          const renewed = await renewLocalSession();
          await api(
            "/jobs",
            { method: "POST", body: JSON.stringify(payload) },
            renewed.csrf,
          );
          setConfirm(null);
          await refetch();
        } catch (retryError) {
          fail(retryError);
        }
      }
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    if (!user) return;
    await api("/logout", { method: "POST" }, user.csrf);
    queryClient.clear();
  }
  if (!user)
    return (
      <main className="loading-page">
        <Workflow className="spin" />
        <p>
          {sessionError
            ? "Não foi possível abrir o laboratório. Verifique o acesso local."
            : "Abrindo o laboratório…"}
        </p>
        {sessionError && (
          <Button onClick={() => retrySession()}>Tentar novamente</Button>
        )}
      </main>
    );
  if (!state)
    return (
      <main className="loading-page">
        <Workflow className="spin" />
        <p>{stateError ? stateError.message : "Conectando ao laboratório…"}</p>
        <Button onClick={logout} variant="outline">
          Reconectar
        </Button>
      </main>
    );
  const active = state.jobs.filter((j) => ACTIVE.has(j.status));
  const detected = state.events.filter((e) => e.type === "alert.detected");
  const correlations = Array.from(
    new Set(detected.map((e) => e.correlation_id)),
  ).slice(0, 3);
  return (
    <main className="app-shell">
      <aside className="nav-rail">
        <button
          className="brand-mark"
          aria-label="Abrir guia"
          onClick={() => setPanel("guide")}
        >
          <Workflow size={22} />
        </button>
        <button
          className={panel === null ? "active" : ""}
          aria-label="Canvas do laboratório"
          title="Laboratório"
          onClick={() => setPanel(null)}
        >
          <Layers3 size={21} />
        </button>
        <button
          aria-label="Jobs e histórico"
          title="Execuções"
          onClick={() => setPanel("jobs")}
        >
          <Activity size={21} />
        </button>
        <button
          aria-label="Biblioteca de evidências"
          title="Relatórios"
          onClick={() => artifacts()}
        >
          <FileText size={21} />
        </button>
        <button
          aria-label="Chat do DBA"
          title="DBA"
          onClick={() => setPanel("chat")}
        >
          <MessagesSquare size={21} />
        </button>
        <div className="rail-bottom">
          <button
            aria-label="Guia operacional"
            title="Guia"
            onClick={() => setPanel("guide")}
          >
            <BookOpen size={21} />
          </button>
          <span
            className="user-avatar"
            title={`${user.username} · ${user.role}`}
          >
            {user.username.slice(0, 2).toUpperCase()}
          </span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="brand-name">
            agent<span>-monitoring-db</span>
            <span className="brand-divider" />
            <span className="topbar-label">Mission Control</span>
          </div>
          <div className="topbar-status">
            <span
              className={`connection ${state.runner.online ? "connected" : ""}`}
            >
              <i />
              {state.runner.simulated
                ? "Runner simulado"
                : state.runner.online
                  ? "Runner conectado"
                  : "Runner desconectado"}
            </span>
            <span className={`mode-badge ${state.mode}`}>
              <FlaskConical size={13} />
              {state.mode === "demo" ? "DEMONSTRAÇÃO" : "INTEGRADO"}
            </span>
            <span className="scope">sakila</span>
          </div>
        </header>
        <section className="workspace-heading">
          <div>
            <div className="breadcrumb">
              WORKSPACE <ChevronRight size={12} /> MYSQL HEATWAVE
            </div>
            <h1>
              Laboratório de agentes
              <span className="version-label">LIVE CANVAS</span>
            </h1>
            <p>
              Da observação à evidência. Acompanhe cada etapa em tempo real.
            </p>
          </div>
        </section>
        {state.mode === "demo" && (
          <div className="demo-strip">
            <FlaskConical size={14} />
            <span>Ambiente de demonstração</span>
            <p>
              Eventos e relatórios fictícios. Nenhuma conexão com MySQL ou envio
              real de e-mail.
            </p>
            <button onClick={() => setPanel("guide")}>
              Como conectar o laboratório <ArrowUpRight size={13} />
            </button>
          </div>
        )}
        {(error || stateError) && (
          <div className="error-banner" role="alert">
            {error || errors[stateError?.message || ""] || stateError?.message}
            <button aria-label="Fechar erro" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        <div className="metric-strip">
          <div>
            <span className="metric-icon cyan">
              <Activity size={19} />
            </span>
            <span>
              <small>EXECUÇÕES ATIVAS</small>
              <b>
                {String(active.length).padStart(2, "0")}
                <em>/ 6 componentes</em>
              </b>
            </span>
          </div>
          <div>
            <span className="metric-icon coral">
              <ShieldCheck size={19} />
            </span>
            <span>
              <small>ALERTAS DETECTADOS</small>
              <b>
                {String(state.metrics.alerts_detected).padStart(2, "0")}
                <em>3 cenários disponíveis</em>
              </b>
            </span>
          </div>
          <div>
            <span className="metric-icon violet">
              <Mail size={19} />
            </span>
            <span>
              <small>
                {state.mode === "demo"
                  ? "ENTREGAS SIMULADAS"
                  : "ENTREGAS CONFIRMADAS"}
              </small>
              <b>
                {String(state.metrics.deliveries_confirmed).padStart(2, "0")}
                <em>via MCP + Notification</em>
              </b>
            </span>
          </div>
          <button onClick={() => artifacts()}>
            <span className="metric-icon gold">
              <FileText size={19} />
            </span>
            <span>
              <small>REGISTROS NO DBA</small>
              <b>
                {String(state.metrics.dba_records).padStart(2, "0")}
                <em>evidências disponíveis</em>
              </b>
            </span>
            <ArrowUpRight size={17} />
          </button>
        </div>
        <div
          className={`main-grid ${activityOpen ? "" : "activity-minimized"}`}
        >
          <AgentCanvas state={state} actions={actions} />
          <aside
            className={`activity-panel ${activityOpen ? "" : "collapsed"}`}
          >
            {activityOpen ? (
              <>
                <div className="activity-heading">
                  <div>
                    <Radio size={15} />
                    <h2>Atividade ao vivo</h2>
                  </div>
                  <div className="activity-heading-actions">
                    <span className="badge">{state.events.length}</span>
                    <button
                      className="activity-toggle"
                      aria-label="Minimizar atividade ao vivo"
                      title="Minimizar atividade ao vivo"
                      onClick={() => setActivityOpen(false)}
                    >
                      <ChevronRight size={15} />
                    </button>
                  </div>
                </div>
                <div className="tabs">
                  <button
                    className={feed === "events" ? "selected" : ""}
                    onClick={() => setFeed("events")}
                  >
                    Timeline
                  </button>
                  <button
                    className={feed === "console" ? "selected" : ""}
                    onClick={() => setFeed("console")}
                  >
                    <Terminal size={12} />
                    Console
                  </button>
                </div>
                <div className="event-feed" aria-live="polite">
                  {!state.events.length ? (
                    <div className="empty-feed">
                      <div className="empty-illustration">
                        <Workflow size={28} />
                        <span />
                        <span />
                      </div>
                      <h3>Pronto para observar</h3>
                      <p>
                        Ative um agente ou execute a demonstração. Cada evento
                        aparecerá aqui, do primeiro sinal à evidência.
                      </p>
                      <button
                        onClick={() => action("lab.three", true)}
                        disabled={!actions.canOperate}
                      >
                        Começar demonstração <ArrowRight size={14} />
                      </button>
                    </div>
                  ) : (
                    state.events.slice(0, 60).map((event) =>
                      feed === "console" ? (
                        <button
                          key={event.event_id}
                          className="console-line"
                          onClick={() => setSelectedEvent(event)}
                        >
                          <time>{stamp(event.occurred_at)}</time>
                          <b>{event.source}</b>
                          <code>{event.type}</code>
                          <small>{JSON.stringify(event.payload)}</small>
                        </button>
                      ) : (
                        <button
                          key={event.event_id}
                          className={`event-item event-${event.source}`}
                          onClick={() => setSelectedEvent(event)}
                        >
                          <span className="event-dot">
                            {event.type.endsWith("sent") ||
                            event.type.endsWith("recorded") ? (
                              <Check size={12} />
                            ) : event.source === "mcp" ? (
                              <Workflow size={12} />
                            ) : (
                              <Activity size={12} />
                            )}
                          </span>
                          <span className="event-body">
                            <span>
                              <b>{eventLabels[event.type] || event.type}</b>
                              <time>{stamp(event.occurred_at)}</time>
                            </span>
                            <small>
                              {event.source}{" "}
                              {event.payload.category
                                ? `· ${event.payload.category}`
                                : ""}
                            </small>
                            {event.payload.decision && (
                              <code>{event.payload.decision}</code>
                            )}
                          </span>
                        </button>
                      ),
                    )
                  )}
                </div>
                <div className="pipeline-summary">
                  <span className="eyebrow">ÚLTIMOS FLUXOS</span>
                  {correlations.length ? (
                    correlations.map((id) => {
                      const events = state.events.filter(
                        (e) => e.correlation_id === id,
                      );
                      return (
                        <div className="pipeline-row" key={id}>
                          <span>
                            {String(
                              events.find((e) => e.payload.category)?.payload
                                .category || "alerta",
                            )}
                          </span>
                          <div>
                            {[
                              "mcp.validated",
                              "notification.sent",
                              "dba.recorded",
                            ].map((type, i) => (
                              <span
                                key={type}
                                className={
                                  events.some((e) => e.type === type)
                                    ? "passed"
                                    : ""
                                }
                                title={["MCP", "E-mail", "DBA"][i]}
                              >
                                <CheckCheck size={12} />
                              </span>
                            ))}
                          </div>
                        </div>
                      );
                    })
                  ) : (
                    <p>MCP → e-mail + registro DBA</p>
                  )}
                </div>
                <button
                  className="dba-shortcut"
                  onClick={() => setPanel("chat")}
                >
                  <span>
                    <Database size={17} />
                  </span>
                  <div>
                    <b>Converse com o DBA</b>
                    <small>Investigue com contexto</small>
                  </div>
                  <ArrowUpRight size={16} />
                </button>
              </>
            ) : (
              <button
                className="activity-minimize-toggle"
                aria-label="Expandir atividade ao vivo"
                title="Expandir atividade ao vivo"
                onClick={() => setActivityOpen(true)}
              >
                <Radio size={15} />
                <span className="badge">{state.events.length}</span>
                <ChevronLeft size={15} />
              </button>
            )}
          </aside>
        </div>
        <footer className="statusbar">
          <span>
            <i className="live-dot" />{" "}
            {state.mode === "demo"
              ? "Simulação isolada"
              : "Acesso local · sem login"}
          </span>
          <span>MySQL HeatWave · sakila</span>
          <span>Atualizado {stamp(state.timestamp)}</span>
          <button onClick={() => setPanel("guide")}>
            <Command size={12} /> Guia operacional
          </button>
        </footer>
      </div>
      {confirm && (
        <Modal
          open
          onClose={() => setConfirm(null)}
          title={state.actions[confirm.action]?.label || confirm.action}
          description={
            confirm.execute
              ? confirmationDescriptions[confirm.action] ||
                "Inicia esta ação no schema sakila."
              : "Confere o plano desta ação sem iniciar o serviço."
          }
        >
          <div className="confirmation-body">
            {confirm.execute && (
              <label>
                Digite sakila para confirmar
                <input
                  autoFocus
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder="sakila"
                />
              </label>
            )}
            {error && <p className="error-message">{error}</p>}
            <div className="dialog-actions">
              <Button variant="outline" onClick={() => setConfirm(null)}>
                Cancelar
              </Button>
              <Button
                disabled={busy || (confirm.execute && typed !== "sakila")}
                onClick={start}
              >
                <Play size={14} />
                {busy
                  ? "Iniciando…"
                  : !confirm.execute
                    ? "Confirmar"
                    : serviceActions.has(confirm.action)
                      ? "Ativar serviço"
                      : queryActions.has(confirm.action)
                        ? "Iniciar consultas"
                        : commandActions.has(confirm.action)
                          ? "Executar comando"
                          : "Iniciar processo"}
              </Button>
            </div>
          </div>
        </Modal>
      )}
      {resource && (
        <ResourceEditor
          agent={resource.agent}
          resource={resource.link}
          user={user}
          onClose={() => setResource(null)}
        />
      )}
      {panel === "artifacts" && (
        <ArtifactViewer
          initial={state.artifacts}
          source={source}
          target={artifactTarget}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === "chat" && (
        <DbaChat user={user} onClose={() => setPanel(null)} />
      )}
      {panel === "jobs" && (
        <Modal
          open
          wide
          onClose={() => setPanel(null)}
          title="Execuções do laboratório"
          description="Histórico de jobs, ações e encerramentos"
        >
          <div className="job-table">
            <div className="job-table-head">
              <span>Ação</span>
              <span>Início</span>
              <span>Status</span>
              <span>Controle</span>
            </div>
            {state.jobs.map((job) => (
              <div key={job.id}>
                <span>
                  <b>{state.actions[job.action]?.label}</b>
                  <small>
                    {job.id.slice(0, 16)} ·{" "}
                    {job.execute ? "execute" : "dry-run"}
                  </small>
                </span>
                <span>{stamp(job.created_at)}</span>
                <StatusBadge status={job.status} />
                <span>
                  {ACTIVE.has(job.status) && (
                    <Button
                      variant="danger"
                      size="sm"
                      disabled={user.role === "viewer"}
                      onClick={() => stop(job.id)}
                    >
                      Parar
                    </Button>
                  )}
                </span>
              </div>
            ))}
            {!state.jobs.length && (
              <div className="empty-state">
                Nenhuma execução nesta sessão do laboratório.
              </div>
            )}
          </div>
        </Modal>
      )}
      {panel === "guide" && (
        <Modal
          open
          wide
          onClose={() => setPanel(null)}
          title="Um laboratório, responsabilidades claras"
          description="Como os componentes trabalham juntos"
        >
          <div className="guide-grid">
            <section>
              <Workflow size={26} />
              <h3>Observe o fluxo</h3>
              <p>
                Health Check e Audit coletam evidências e aplicam suas regras. O
                MCP valida os contratos e encaminha, de forma independente, para
                o Notification e para a inbox do DBA.
              </p>
              <p>
                O MCP usa stdio sob demanda. O Notification não é um daemon e
                realiza uma tentativa SMTP por chamada.
              </p>
            </section>
            <section>
              <FlaskConical size={26} />
              <h3>Demo e ambiente integrado</h3>
              <p>
                O modo é fixado no servidor. Demo usa eventos e relatórios
                fictícios. Para conectar os agentes, inicie a API em modo
                integrated e registre o runner local com a chave configurada
                pelo operador.
              </p>
              <p>
                O runner usa conexão de saída WSS. Senhas MySQL e SMTP
                permanecem no ambiente local.
              </p>
            </section>
            <section>
              <FileText size={26} />
              <h3>Abra as evidências</h3>
              <p>
                O Health preserva JSON e HTML na inbox DBA. O Audit conserva
                resumos sanitizados no DBA e um HTML latest-only na origem. A
                identidade da coleta é verificada antes de associar um relatório
                ao alerta.
              </p>
            </section>
            <section>
              <GitBranch size={26} />
              <h3>Decida com o DBA</h3>
              <p>
                O chat consulta o estado observado. Consultas do catálogo só
                executam após aprovação. O Refactor recebe solicitações
                versionadas pelo MCP; nenhum resultado autoriza uma mudança
                automática no banco.
              </p>
            </section>
          </div>
        </Modal>
      )}
      {selectedEvent && (
        <Modal
          open
          onClose={() => setSelectedEvent(null)}
          title={eventLabels[selectedEvent.type] || selectedEvent.type}
          description={`${selectedEvent.source} · ${stamp(selectedEvent.occurred_at)}`}
        >
          <div className="event-inspector">
            <span className="badge">{state.mode}</span>
            <h4>Identidade do fluxo</h4>
            <code>{selectedEvent.correlation_id}</code>
            <pre>{JSON.stringify(selectedEvent.payload, null, 2)}</pre>
            <div className="trace-stages">
              {state.events
                .filter(
                  (e) => e.correlation_id === selectedEvent.correlation_id,
                )
                .reverse()
                .map((e) => (
                  <p key={e.event_id}>
                    <Check size={13} />
                    <b>{e.source}</b>
                    <span>{eventLabels[e.type] || e.type}</span>
                  </p>
                ))}
            </div>
            <Button
              variant="outline"
              onClick={() => {
                setSelectedEvent(null);
                artifacts(undefined, {
                  auditId: String(
                    selectedEvent.payload.audit_id ||
                      selectedEvent.payload.alert_id ||
                      selectedEvent.correlation_id,
                  ),
                });
              }}
            >
              <FileText size={15} />
              Ver relatórios detalhados
            </Button>
          </div>
        </Modal>
      )}
    </main>
  );
}
