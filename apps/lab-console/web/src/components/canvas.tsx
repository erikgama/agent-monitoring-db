"use client";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  BaseEdge,
  getBezierPath,
  applyNodeChanges,
  type Node,
  type NodeProps,
  type EdgeProps,
  type NodeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  Activity,
  ShieldCheck,
  Workflow,
  Mail,
  Database,
  GitBranch,
  Play,
  Square,
  FileText,
  MessagesSquare,
  ArrowUpRight,
  FlaskConical,
  type LucideIcon,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { Button } from "@/components/ui/button";
import { ACTIVE, labels, type State, type LabEvent } from "@/lib/api";

const specs: Record<
  string,
  {
    title: string;
    subtitle: string;
    icon: LucideIcon;
    color: string;
    position: { x: number; y: number };
    tag: string;
  }
> = {
  "health-check": {
    title: "Health Check",
    subtitle: "Saúde e performance do MySQL",
    icon: Activity,
    color: "#67d4e7",
    position: { x: 0, y: 0 },
    tag: "AGENTE",
  },
  audit: {
    title: "Audit Security",
    subtitle: "Eventos de segurança do sakila",
    icon: ShieldCheck,
    color: "#ff9c83",
    position: { x: 0, y: 420 },
    tag: "AGENTE",
  },
  simulation: {
    title: "Simulação Aplicação",
    subtitle: "Dispare eventos para validar os agentes",
    icon: FlaskConical,
    color: "#e8b86f",
    position: { x: 400, y: 0 },
    tag: "LABORATÓRIO",
  },
  mcp: {
    title: "MCP Central",
    subtitle: "Validação e roteamento de alertas e refatorações",
    icon: Workflow,
    color: "#baa5ff",
    position: { x: 400, y: 420 },
    tag: "ORQUESTRADOR",
  },
  notification: {
    title: "Notification",
    subtitle: "Roteamento de alertas por severidade",
    icon: Mail,
    color: "#9facff",
    position: { x: 800, y: 0 },
    tag: "AGENTE",
  },
  dba: {
    title: "DBA",
    subtitle: "Seu centro de decisão operacional",
    icon: Database,
    color: "#f0ce7c",
    position: { x: 1200, y: 420 },
    tag: "AGENTE",
  },
  refactor: {
    title: "Refactor",
    subtitle: "Validação de query lenta no sakila_dev",
    icon: GitBranch,
    color: "#83dcb1",
    position: { x: 800, y: 420 },
    tag: "AGENTE",
  },
};
export type CanvasActions = {
  onAction: (action: string, execute: boolean) => void;
  onStop: (id: string) => void;
  onArtifacts: (source?: string) => void;
  onChat: () => void;
  onInspect: (event: LabEvent) => void;
  onResources: (agent: string, resource: AgentResourceLink) => void;
  canOperate: boolean;
};
export type AgentResourceLink = {
  id:
    | "skill"
    | "rules"
    | "scripts"
    | "tools"
    | "contracts"
    | "prompt-health-check"
    | "prompt-audit";
  label:
    | "Skill"
    | "Rules"
    | "Scripts"
    | "Tools"
    | "Contratos"
    | "Rule Health Check"
    | "Rule Audit";
  path: string;
  description: string;
  guide?: string[];
};
type AgentData = {
  agent: string;
  state: State;
  actions: CanvasActions;
  [key: string]: unknown;
};
const agentResources: Record<string, AgentResourceLink[]> = {
  "health-check": [
    {
      id: "skill",
      label: "Skill",
      path: "agents/health-check/skills/health-check-skill.md",
      description: "Competência de observação read-only e análise de saúde.",
    },
    {
      id: "rules",
      label: "Rules",
      path: "agents/health-check/advisor/rules.md",
      description:
        "Regra do Luna: P99 da query monitorada acima de 2,0 segundos.",
    },
    {
      id: "scripts",
      label: "Scripts",
      path: "agents/health-check/{advisor,select_latency,refactor_collector,src,general_report,contracts}",
      description:
        "Fluxo técnico do Health Check: latência, relatório geral e triagem de queries para o Refactor pelo MCP.",
    },
  ],
  audit: [
    {
      id: "skill",
      label: "Skill",
      path: "agents/audit/skills/audit-skill.md",
      description: "Competência de investigação Audit somente leitura.",
    },
    {
      id: "rules",
      label: "Rules",
      path: "agents/audit/audit_security/advisor/rules.md",
      description: "Regras determinísticas de DDL protegido.",
    },
    {
      id: "scripts",
      label: "Scripts",
      path: "agents/audit/{audit_security,contracts}",
      description:
        "Coleta read-only, SQL versionado e evidências mais recentes do Audit.",
    },
  ],
  simulation: [
    {
      id: "scripts",
      label: "Scripts",
      path: "apps/lab-console/scripts + agents/dba/load-tests",
      description:
        "Orquestradores e workloads usados nos cenários SELECT, DROP e ALTER.",
    },
  ],
  mcp: [
    {
      id: "tools",
      label: "Tools",
      path: "mcp/src/mysqlconf_mcp/tools",
      description:
        "Catálogo e implementação das três capacidades controladas do MCP.",
    },
    {
      id: "contracts",
      label: "Contratos",
      path: "mcp/src/mysqlconf_mcp/contracts",
      description: "Schemas e proveniência dos contratos aceitos pelo MCP.",
    },
    {
      id: "scripts",
      label: "Scripts",
      path: "publishers stdio dos agentes + mcp/src/mysqlconf_mcp/server.py",
      description:
        "Clientes que chamam o MCP por STDIO e servidor central que registra as Tools.",
    },
  ],
  notification: [
    {
      id: "skill",
      label: "Skill",
      path: "agents/notification/skills/notification-skill.md",
      description: "Competência de entrega de alertas já validados.",
    },
    {
      id: "rules",
      label: "Rules",
      path: "agents/notification/notification/advisor/rules.md",
      description:
        "Role em português: preserva a severidade validada e seleciona o canal.",
    },
    {
      id: "scripts",
      label: "Scripts",
      path: "agents/notification/{notification,contracts}",
      description:
        "Fluxo técnico do Notification: advisor, validação, canal, templates e contratos.",
    },
  ],
  dba: [
    {
      id: "skill",
      label: "Skill",
      path: "agents/dba/skills/dba-skill.md",
      description: "Competência de coordenação técnica e mudanças controladas.",
    },
    {
      id: "prompt-health-check",
      label: "Rule Health Check",
      path: "agents/dba/analise-ocorrencia-health-check/prompt.md",
      description:
        "Prompt real usado pelo Luna para resumir ocorrências do Health Check recebidas pelo DBA.",
    },
    {
      id: "prompt-audit",
      label: "Rule Audit",
      path: "agents/dba/analise-ocorrencia-audit/prompt.md",
      description:
        "Prompt real usado pelo Luna para resumir ocorrências sanitizadas do Audit recebidas pelo DBA.",
    },
    {
      id: "scripts",
      label: "Scripts",
      path: "agents/dba/resumos + labconsole/incident_analysis.py",
      description:
        "Código real do resumo automático e regras factuais de Health Check e Audit.",
    },
  ],
  refactor: [
    {
      id: "skill",
      label: "Skill",
      path: "agents/refactor/skills/refactor-skill.md",
      description: "Competência de refatoração com contrato preservado.",
    },
    {
      id: "rules",
      label: "Rules",
      path: "agents/refactor/query_refactor/advisor/rules.md",
      description: "Preservação de SQL, resultado e validações autorizadas.",
    },
    {
      id: "scripts",
      label: "Scripts",
      path: "agents/refactor/{advisor,execucao,jobs-mcp,contracts}",
      description:
        "Advisor, execução MySQL, publicação MCP, contratos e jobs reais com SQL original e proposta.",
    },
  ],
};
const simulationActions = new Set(["health.load", "audit.drop", "audit.alter"]);
function elapsedLabel(seconds: number) {
  const minutes = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const remainder = (seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${remainder}`;
}
export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`status status-${status}`}>
      <i />
      {labels[status] || status}
    </span>
  );
}
function AgentNode({ data }: NodeProps<Node<AgentData>>) {
  const { agent, state, actions } = data;
  const spec = specs[agent];
  const Icon = spec.icon;
  const resources = agentResources[agent] || [];
  const jobs = state.jobs.filter(
    (j) =>
      (state.actions[j.action]?.agent === agent &&
        !simulationActions.has(j.action)) ||
      (j.action === "lab.three" && ["health-check", "audit"].includes(agent)),
  );
  const active = jobs.find((j) => ACTIVE.has(j.status));
  const status =
    active?.status ||
    (jobs[0] && ["failed", "timed_out", "interrupted"].includes(jobs[0].status)
      ? jobs[0].status
      : "") ||
    (["mcp", "notification"].includes(agent) ? "on-demand" : "idle");
  const recent = state.events.find((e) => e.source === agent);
  const sent = state.metrics.deliveries_confirmed;
  const recorded = state.metrics.dba_records;
  const style = { "--agent": spec.color } as CSSProperties;
  return (
    <article
      className={`agent-node agent-${agent}`}
      style={style}
      aria-label={`Agente ${spec.title}`}
    >
      {resources.length > 0 && (
        <div
          className="resource-rail nodrag nowheel"
          aria-label={`Recursos de ${spec.title}`}
        >
          {resources.map((resource) => (
            <button
              key={resource.label}
              className="resource-link"
              title={resource.path}
              onClick={(event) => {
                event.stopPropagation();
                actions.onResources(agent, resource);
              }}
            >
              {resource.label}
            </button>
          ))}
        </div>
      )}
      {agent === "dba" ? (
        <Handle type="target" position={Position.Left} id="from-mcp" />
      ) : agent === "refactor" ? (
        <Handle type="target" position={Position.Left} id="from-mcp" />
      ) : (
        <>
          <Handle type="target" position={Position.Left} />
          <Handle type="source" position={Position.Right} />
        </>
      )}
      <div className="agent-top">
        <span className="agent-icon">
          <Icon size={23} strokeWidth={1.7} />
        </span>
        <span className="eyebrow">{spec.tag}</span>
        <span className="agent-number">
          {Object.keys(specs).indexOf(agent) + 1 < 10 ? "0" : ""}
          {Object.keys(specs).indexOf(agent) + 1}
        </span>
      </div>
      <div className="agent-title">
        <h3>{spec.title}</h3>
        <StatusBadge status={status} />
      </div>
      <p className="agent-subtitle">{spec.subtitle}</p>
      {agent === "audit" && (
        <div className="rule-chips">
          <span>destructive_ddl</span>
          <span>schema_change</span>
          <p>Uma publicação por evento · deduplicação ativa</p>
        </div>
      )}
      {agent === "mcp" && (
        <div className="mcp-routes">
          <div className="mcp-route-title">
            <strong>3 tools MCP</strong>
            <span>STDIO</span>
          </div>
          <div className="mcp-tool-list">
            <code>
              <span className="mcp-tool-name">
                <i className="route-dot" />
                incident_raise
              </span>
              <small>Health/Audit → Notification + DBA</small>
            </code>
            <code>
              <span className="mcp-tool-name">
                <i className="route-dot" />
                refactor_request_raise
              </span>
              <small>Health → Refactor</small>
            </code>
            <code>
              <span className="mcp-tool-name">
                <i className="route-dot" />
                refactor_result_raise
              </span>
              <small>Refactor → DBA + Notification</small>
            </code>
          </div>
          <p>Publicadores: Health 2 · Audit 1 · Refactor 1</p>
        </div>
      )}
      {agent === "notification" && (
        <div className="agent-metrics">
          <span>
            <b>{sent.toString().padStart(2, "0")}</b>
            {state.mode === "demo" ? "envios simulados" : "aceitos pelo SMTP"}
          </span>
          <span>
            <b>{state.metrics.delivery_failures.toString().padStart(2, "0")}</b>
            falhas
          </span>
        </div>
      )}
      {agent === "dba" && (
        <div className="dba-note">
          <span>
            <FileText size={15} />
            {recorded} evidências recebidas
          </span>
          <p>Investigue, consulte e decida com contexto.</p>
        </div>
      )}
      {agent === "refactor" && (
        <div className="refactor-note">
          <span>request.json → Advisor → proposed.sql</span>
          <p>Validação sakila_dev → result.json → MCP</p>
        </div>
      )}
      {active && (
        <div className="job-note">
          Job {active.id.slice(0, 8)} · {labels[active.status]}
        </div>
      )}
      <div className="action-bar nodrag nowheel">
        {["health-check", "audit"].includes(agent) && (
          <>
            {active ? (
              <Button
                size="sm"
                variant="danger"
                disabled={!actions.canOperate}
                onClick={() => actions.onStop(active.id)}
              >
                <Square size={12} />
                Parar
              </Button>
            ) : (
              <Button
                size="sm"
                disabled={!actions.canOperate}
                onClick={() =>
                  actions.onAction(
                    agent === "audit" ? "audit.lab" : "health.lab",
                    true,
                  )
                }
              >
                <Play size={13} />
                Ativar {agent === "audit" ? "Audit" : "Health Check"}
              </Button>
            )}
          </>
        )}
        {agent === "health-check" && (
          <div className="health-report-actions">
            <Button
              size="icon"
              variant="outline"
              aria-label="Abrir latest.html do General Collector"
              title="Abrir o latest.html do General Collector em nova aba"
              onClick={() =>
                window.open(
                  "/api/artifacts/current/health/html",
                  "_blank",
                  "noopener,noreferrer",
                )
              }
            >
              <FileText size={16} />
            </Button>
            <Button
              size="icon"
              variant="outline"
              aria-label="Abrir latest.html do Select Latency"
              title="Abrir o latest.html do Select Latency em nova aba"
              onClick={() =>
                window.open(
                  "/api/artifacts/current/latency/html",
                  "_blank",
                  "noopener,noreferrer",
                )
              }
            >
              <Activity size={16} />
            </Button>
          </div>
        )}
        {agent === "audit" && (
          <Button
            size="sm"
            variant="outline"
            aria-label="Abrir latest.html do Audit"
            title="Abrir o latest.html atual do Audit em nova aba"
            onClick={() =>
              window.open(
                "/api/artifacts/current/audit/html",
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            <FileText size={13} />
            Audit HTML
          </Button>
        )}
        {agent === "refactor" &&
          (active ? (
            <Button
              size="sm"
              variant="danger"
              disabled={!actions.canOperate}
              onClick={() => actions.onStop(active.id)}
            >
              <Square size={12} />
              Parar
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={!actions.canOperate}
              onClick={() => actions.onAction("refactor.lab", true)}
            >
              <Play size={13} />
              Ativar Refactor
            </Button>
          ))}
        {agent === "dba" && (
          <Button size="sm" onClick={actions.onChat}>
            <MessagesSquare size={14} />
            Conversar com DBA
            <ArrowUpRight size={13} />
          </Button>
        )}
        {agent === "mcp" && (
          <Button
            size="sm"
            variant="outline"
            disabled={!recent}
            onClick={() => {
              if (recent) actions.onInspect(recent);
            }}
          >
            <Workflow size={13} />
            Inspecionar fluxo
          </Button>
        )}
        {["health-check", "dba", "refactor"].includes(agent) && (
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Abrir relatório detalhado ${spec.title}`}
            title="Abrir relatório detalhado"
            onClick={() =>
              actions.onArtifacts(agent === "dba" ? undefined : agent)
            }
          >
            <FileText size={16} />
          </Button>
        )}
      </div>
      {agent === "health-check" && (
        <button
          className="collect-link nodrag"
          disabled={!actions.canOperate || !!active}
          onClick={() => actions.onAction("health.collect", true)}
        >
          Executar coleta atual <ArrowUpRight size={12} />
        </button>
      )}
    </article>
  );
}
function SimulationNode({ data }: NodeProps<Node<AgentData>>) {
  const { state, actions } = data;
  const spec = specs.simulation;
  const Icon = spec.icon;
  const resources = agentResources.simulation;
  const [now, setNow] = useState(() => Date.now());
  const jobs = state.jobs.filter(
    (job) => simulationActions.has(job.action) && ACTIVE.has(job.status),
  );
  const selectJob = jobs.find((job) => job.action === "health.load");
  const selectJobId = selectJob?.id;
  useEffect(() => {
    if (!selectJobId) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [selectJobId]);
  const selectElapsed = selectJob
    ? Math.max(0, Math.floor((now - Date.parse(selectJob.created_at)) / 1000))
    : 0;
  const selectWarmingUp = selectElapsed < 60;
  const style = { "--agent": spec.color } as CSSProperties;
  const scenarios = [
    {
      action: "health.load",
      label: "Iniciar consultas",
      description: "Banco sakila + Slow Query Log",
      enabledBy: "health.lab",
      startLabel: "Iniciar consultas",
      cancelLabel: "Cancelar consultas",
    },
    {
      action: "audit.drop",
      label: "Tentar DROP",
      description: "DDL destrutivo negado",
      enabledBy: "audit.lab",
      startLabel: "Tentar DROP",
      cancelLabel: "Cancelar Tentar DROP",
    },
    {
      action: "audit.alter",
      label: "Tentar ALTER",
      description: "Mudança de schema negada",
      enabledBy: "audit.lab",
      startLabel: "Tentar ALTER",
      cancelLabel: "Cancelar Tentar ALTER",
    },
  ];
  const activeActions = new Set(
    state.jobs.filter((job) => ACTIVE.has(job.status)).map((job) => job.action),
  );
  return (
    <article
      className="agent-node agent-simulation"
      style={style}
      aria-label="Componente Simulação Aplicação"
    >
      <div
        className="resource-rail nodrag nowheel"
        aria-label="Recursos de Simulação Aplicação"
      >
        {resources.map((resource) => (
          <button
            key={resource.label}
            className="resource-link"
            title={resource.path}
            onClick={(event) => {
              event.stopPropagation();
              actions.onResources("simulation", resource);
            }}
          >
            {resource.label}
          </button>
        ))}
      </div>
      <Handle
        type="target"
        position={Position.Left}
        id="health-scenario"
        style={{ top: "35%" }}
      />
      <Handle
        type="target"
        position={Position.Left}
        id="audit-scenario"
        style={{ top: "70%" }}
      />
      <div className="agent-top">
        <span className="agent-icon">
          <Icon size={23} strokeWidth={1.7} />
        </span>
        <span className="eyebrow">{spec.tag}</span>
        <span className="agent-number">03</span>
      </div>
      <div className="agent-title">
        <h3>{spec.title}</h3>
        <StatusBadge status={jobs[0]?.status || "idle"} />
      </div>
      <p className="agent-subtitle">{spec.subtitle}</p>
      {selectJob && (
        <div className="simulation-progress" aria-live="polite">
          <div>
            <span>
              {selectWarmingUp
                ? "Aquecendo banco em baixa carga"
                : "Consultas paralelas + Slow Query Log"}
            </span>
            <strong>{elapsedLabel(selectElapsed)}</strong>
          </div>
          <div className="simulation-progress-track" aria-hidden="true">
            <i className="is-indeterminate" />
          </div>
          <small>
            {selectWarmingUp
              ? "1 TPS por 1 minuto antes da carga paralela"
              : "Ativo até você clicar em Cancelar"}
          </small>
        </div>
      )}
      <div className="simulation-actions nodrag nowheel">
        {scenarios.map((scenario) => {
          const active = jobs.find((job) => job.action === scenario.action);
          return (
            <div key={scenario.action}>
              <span>
                <b>{scenario.label}</b>
                <small>{scenario.description}</small>
              </span>
              <Button
                size="sm"
                variant={active ? "danger" : "outline"}
                aria-label={active ? scenario.cancelLabel : scenario.startLabel}
                disabled={
                  !actions.canOperate ||
                  (!active && !activeActions.has(scenario.enabledBy))
                }
                title={scenario.label}
                onClick={() =>
                  active
                    ? actions.onStop(active.id)
                    : actions.onAction(scenario.action, true)
                }
              >
                {active ? <Square size={12} /> : <Play size={12} />}
                {active ? "Cancelar" : "Iniciar"}
              </Button>
            </div>
          );
        })}
      </div>
    </article>
  );
}
function FlowEdge(props: EdgeProps) {
  const [bezierPath] = getBezierPath(props);
  const path =
    props.id === "mcp-dba"
      ? `M ${props.sourceX} ${props.sourceY} L ${props.sourceX + 38} ${props.sourceY} L ${props.sourceX + 38} ${Math.min(props.sourceY, props.targetY) - 185} L ${props.targetX - 38} ${Math.min(props.sourceY, props.targetY) - 185} L ${props.targetX - 38} ${props.targetY} L ${props.targetX} ${props.targetY}`
      : bezierPath;
  return (
    <>
      <BaseEdge
        id={props.id}
        markerEnd={props.markerEnd}
        markerStart={props.markerStart}
        path={path}
        style={{ stroke: "#4b5b66", strokeWidth: 1.5 }}
      />
      {props.animated && (
        <path
          d={path}
          className="flow-pulse"
          data-testid={`active-flow-${props.id}`}
        />
      )}
    </>
  );
}
const nodeTypes = { agent: AgentNode, simulation: SimulationNode };
const edgeTypes = { flow: FlowEdge };
const routes: Array<{
  source: string;
  sourceHandle?: string;
  target: string;
  targetHandle?: string;
}> = [
  {
    source: "health-check",
    target: "simulation",
    targetHandle: "health-scenario",
  },
  { source: "audit", target: "simulation", targetHandle: "audit-scenario" },
  { source: "health-check", target: "mcp" },
  { source: "audit", target: "mcp" },
  { source: "mcp", target: "notification" },
  { source: "mcp", target: "dba", targetHandle: "from-mcp" },
  {
    source: "mcp",
    target: "refactor",
    targetHandle: "from-mcp",
  },
];
export function AgentCanvas({
  state,
  actions,
}: {
  state: State;
  actions: CanvasActions;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [flow, setFlow] = useState<ReactFlowInstance | null>(null);
  useEffect(() => {
    if (!flow || !container.current) return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() =>
        flow.fitView({ padding: 0.01, maxZoom: 1.25, duration: 0 }),
      );
    });
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [flow]);
  const [baseNodes, setBaseNodes] = useState<Node[]>(
    Object.entries(specs).map(([id, spec]) => ({
      id,
      type: id === "simulation" ? "simulation" : "agent",
      position: spec.position,
      data: {},
    })),
  );
  const nodes = useMemo(
    () =>
      baseNodes.map((node) => ({
        ...node,
        data: { agent: node.id, state, actions },
      })),
    [state, actions, baseNodes],
  );
  const activeActions = new Set(
    state.jobs.filter((job) => ACTIVE.has(job.status)).map((job) => job.action),
  );
  const activeRoutes = new Set<string>();
  if (["health.load", "lab.three"].some((action) => activeActions.has(action)))
    activeRoutes.add("health-check-simulation");
  if (
    ["audit.drop", "audit.alter", "lab.three"].some((action) =>
      activeActions.has(action),
    )
  )
    activeRoutes.add("audit-simulation");
  if (["health.lab", "lab.three"].some((action) => activeActions.has(action))) {
    activeRoutes.add("health-check-mcp");
  }
  if (["audit.lab", "lab.three"].some((action) => activeActions.has(action)))
    activeRoutes.add("audit-mcp");
  if (activeActions.has("lab.three")) {
    activeRoutes.add("mcp-notification");
    activeRoutes.add("mcp-dba");
  }
  if (activeActions.has("refactor.lab")) activeRoutes.add("mcp-refactor");
  const edges = routes.map(
    ({ source, sourceHandle, target, targetHandle }) => ({
      id: `${source}-${target}`,
      source,
      sourceHandle,
      target,
      targetHandle,
      type: "flow",
      animated: activeRoutes.has(`${source}-${target}`),
      style: { opacity: 1 },
      data: { target },
    }),
  );
  function changed(changes: NodeChange[]) {
    setBaseNodes((previous) => applyNodeChanges(changes, previous));
  }
  return (
    <div className="canvas" data-testid="canvas" ref={container}>
      <div className="canvas-label">
        <span className="live-dot" />
        ARQUITETURA EM TEMPO REAL<span>7 componentes</span>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={changed}
        onInit={setFlow}
        fitView
        fitViewOptions={{ padding: 0.01, maxZoom: 1.25 }}
        minZoom={0.3}
        maxZoom={1.5}
        nodesConnectable={false}
        nodesFocusable={false}
        colorMode="dark"
        onEdgeClick={(_, edge) => {
          const event = state.events.find(
            (e) => e.source === edge.target || e.source === edge.source,
          );
          if (event) actions.onInspect(event);
        }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          color="#35424c"
        />
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="canvas-legend">
        <span>
          <i className="legend-line" />
          Fluxo de evidências
        </span>
        <span>
          <i className="legend-line active" />
          Evento em trânsito
        </span>
        <span>Arraste para organizar · scroll para zoom</span>
      </div>
    </div>
  );
}
