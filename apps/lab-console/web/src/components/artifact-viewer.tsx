"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Download,
  FileText,
  Maximize,
  RefreshCw,
  Search,
  ZoomIn,
  ZoomOut,
  Printer,
} from "lucide-react";
import { Modal } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api, artifactUrl, type Artifact } from "@/lib/api";

export function ArtifactViewer({
  initial,
  source,
  target,
  onClose,
}: {
  initial: Artifact[];
  source?: string;
  target?: { id?: string; auditId?: string };
  onClose: () => void;
}) {
  const {
    data = initial,
    error,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ["artifacts"],
    queryFn: () => api<Artifact[]>("/artifacts"),
    initialData: initial,
    refetchInterval: 3000,
  });
  const [filter, setFilter] = useState(source || "all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Artifact | null>(null);
  const [tab, setTab] = useState<"html" | "json" | "meta">("html");
  const [zoom, setZoom] = useState(1);
  const items = data.filter(
    (a) =>
      (!target?.id || a.id === target.id) &&
      (!target?.auditId ||
        a.audit_id === target.auditId ||
        a.alert_id === target.auditId) &&
      (filter === "all" || a.source === filter) &&
      `${a.name} ${a.category} ${a.timestamp}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const artifact = selected || items[0];
  const document = useQuery({
    queryKey: ["artifact-content", artifact?.id, artifact?.sha256, tab],
    enabled:
      !!artifact &&
      tab !== "meta" &&
      (tab !== "html" || artifact.html_available),
    retry: false,
    queryFn: async () => {
      const response = await fetch(
        artifactUrl(artifact!, tab === "html" ? "html" : "json"),
      );
      if (!response.ok)
        throw new Error(
          "Evidência indisponível ou substituída. Atualize o catálogo; nenhum outro relatório será usado como substituto.",
        );
      return response.text();
    },
  });
  return (
    <Modal
      open
      onClose={onClose}
      title="Biblioteca de evidências"
      description="Relatórios dos agentes e registros recebidos pelo DBA"
      wide
    >
      <div className="artifact-layout">
        <aside className="artifact-list">
          <div className="search-field">
            <Search size={15} />
            <input
              aria-label="Buscar evidência"
              placeholder="Categoria, data ou relatório"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            aria-label="Filtrar agente"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setSelected(null);
            }}
          >
            <option value="all">Todos os agentes</option>
            <option value="health-check">Health Check</option>
            <option value="audit">Audit Security</option>
            <option value="refactor">Refactor</option>
          </select>
          <Button variant="ghost" size="sm" onClick={() => refetch()}>
            <RefreshCw size={13} className={isFetching ? "spin" : ""} />
            Atualizar catálogo
          </Button>
          {error && <p className="error-message">{error.message}</p>}
          {items.map((item) => (
            <button
              key={item.id}
              className={`artifact-item ${artifact?.id === item.id ? "selected" : ""}`}
              onClick={() => {
                setSelected(item);
                setTab(item.html_available ? "html" : "json");
              }}
            >
              <FileText size={17} />
              <span>
                <b>{item.name}</b>
                <small>
                  {item.source} · {item.retention}
                </small>
                <small>
                  {new Date(item.timestamp).toLocaleString("pt-BR")}
                </small>
              </span>
            </button>
          ))}
          {!items.length && (
            <div className="empty-state">
              <FileText />
              <h3>Nenhum relatório disponível</h3>
              <p>
                {target
                  ? "A evidência desta ocorrência não está retida no catálogo. Um relatório mais recente não será exibido em seu lugar."
                  : "Execute uma demonstração para gerar evidências ou conecte o runner ao catálogo real."}
              </p>
            </div>
          )}
        </aside>
        <div className="artifact-document">
          {artifact ? (
            <>
              <div className="document-toolbar">
                <div className="tabs">
                  {(["html", "json", "meta"] as const).map((value) => (
                    <button
                      key={value}
                      className={tab === value ? "selected" : ""}
                      onClick={() => setTab(value)}
                    >
                      {value === "html"
                        ? "Relatório HTML"
                        : value === "json"
                          ? "JSON"
                          : "Metadados"}
                    </button>
                  ))}
                </div>
                <div className="document-tools">
                  <button
                    aria-label="Diminuir zoom"
                    onClick={() => setZoom((z) => Math.max(0.6, z - 0.1))}
                  >
                    <ZoomOut size={16} />
                  </button>
                  <span>{Math.round(zoom * 100)}%</span>
                  <button
                    aria-label="Aumentar zoom"
                    onClick={() => setZoom((z) => Math.min(1.8, z + 0.1))}
                  >
                    <ZoomIn size={16} />
                  </button>
                  <button
                    aria-label="Tela cheia"
                    onClick={() =>
                      window.document
                        .querySelector(".artifact-document")
                        ?.requestFullscreen()
                    }
                  >
                    <Maximize size={16} />
                  </button>
                  <button
                    aria-label="Imprimir relatório"
                    title="Abrir relatório isolado para impressão pelo navegador"
                    disabled={!artifact.html_available}
                    onClick={() =>
                      window.open(
                        artifactUrl(artifact, "html"),
                        "_blank",
                        "noopener,noreferrer",
                      )
                    }
                  >
                    <Printer size={16} />
                  </button>
                  <a
                    aria-label="Download do relatório"
                    href={artifactUrl(
                      artifact,
                      tab === "html" && artifact.html_available
                        ? "html"
                        : "json",
                    )}
                    download={`report.${tab === "html" && artifact.html_available ? "html" : "json"}`}
                  >
                    <Download size={16} />
                  </a>
                </div>
              </div>
              <div className="document-meta">
                <span className="badge">{artifact.retention}</span>
                <span>{artifact.category}</span>
                <code>{artifact.audit_id.slice(0, 18)}…</code>
              </div>
              {tab === "meta" ? (
                <pre className="json-document">
                  {JSON.stringify(artifact, null, 2)}
                </pre>
              ) : tab === "html" && !artifact.html_available ? (
                <div className="empty-state">
                  <FileText />
                  <h3>HTML histórico não retido</h3>
                  <p>
                    O resumo sanitizado está disponível na aba JSON. Um
                    relatório latest atual não substitui a evidência desta
                    ocorrência.
                  </p>
                </div>
              ) : document.error ? (
                <p className="error-message" role="alert">
                  {document.error.message}
                </p>
              ) : document.isPending ? (
                <p className="empty-state">Carregando evidência…</p>
              ) : (
                <iframe
                  key={`${artifact.id}-${tab}`}
                  title={`Relatório ${artifact.name}`}
                  className="report-frame"
                  sandbox=""
                  referrerPolicy="no-referrer"
                  srcDoc={
                    tab === "html"
                      ? `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'none'; form-action 'none'; base-uri 'none'">${document.data}`
                      : `<pre>${(document.data || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</pre>`
                  }
                  style={{ zoom }}
                />
              )}
            </>
          ) : (
            <div className="empty-state">
              <FileText size={36} />
              <h3>A evidência, com todo o contexto.</h3>
              <p>
                Selecione um relatório para visualizar o HTML original, JSON e
                identidade da coleta.
              </p>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
