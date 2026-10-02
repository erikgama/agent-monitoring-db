"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronRight, FileCode2, FolderOpen, Save } from "lucide-react";
import { Modal } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { api, type User } from "@/lib/api";
import type { AgentResourceLink } from "@/components/canvas";

type ResourceDocument = {
  kind: "file";
  path: string;
  source_path: string;
  content: string;
  editable: boolean;
};
type ResourceTree = {
  kind: "tree";
  files: string[];
};
type TreeNode = {
  name: string;
  path?: string;
  children: Map<string, TreeNode>;
};

function makeTree(files: string[]) {
  const root: TreeNode = { name: "", children: new Map() };
  for (const path of files) {
    let node = root;
    for (const [index, name] of path.split("/").entries()) {
      const child = node.children.get(name) || { name, children: new Map() };
      node.children.set(name, child);
      node = child;
      if (index === path.split("/").length - 1) node.path = path;
    }
  }
  return root;
}

function FileTree({
  node,
  openFile,
}: {
  node: TreeNode;
  openFile: (path: string) => void;
}) {
  return (
    <ul className="resource-tree">
      {[...node.children.values()].map((child) =>
        child.path ? (
          <li key={child.path}>
            <button onClick={() => openFile(child.path!)}>
              <FileCode2 size={14} />
              {child.name}
            </button>
          </li>
        ) : (
          <li key={child.name}>
            <details>
              <summary>
                <ChevronRight size={13} />
                <FolderOpen size={14} />
                {child.name}
              </summary>
              <FileTree node={child} openFile={openFile} />
            </details>
          </li>
        ),
      )}
    </ul>
  );
}

export function ResourceEditor({
  agent,
  resource,
  user,
  onClose,
}: {
  agent: string;
  resource: AgentResourceLink;
  user: User;
  onClose: () => void;
}) {
  const [tree, setTree] = useState<ResourceTree | null>(null);
  const [document, setDocument] = useState<ResourceDocument | null>(null);
  const [value, setValue] = useState("");
  const [initial, setInitial] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const endpoint = `/resources/${encodeURIComponent(agent)}/${resource.id}`;
  const treeRoot = useMemo(() => (tree ? makeTree(tree.files) : null), [tree]);

  useEffect(() => {
    let current = true;
    async function load() {
      setLoading(true);
      setError("");
      setSaved("");
      setTree(null);
      setDocument(null);
      try {
        const response = await api<ResourceDocument | ResourceTree>(endpoint);
        if (!current) return;
        if (response.kind === "tree") setTree(response);
        else {
          setDocument(response);
          setValue(response.content);
          setInitial(response.content);
        }
      } catch (cause) {
        if (current) setError((cause as Error).message);
      } finally {
        if (current) setLoading(false);
      }
    }
    void load();
    return () => {
      current = false;
    };
  }, [endpoint]);

  async function openFile(path: string) {
    setLoading(true);
    setError("");
    setSaved("");
    try {
      const response = await api<ResourceDocument>(
        `${endpoint}?path=${encodeURIComponent(path)}`,
      );
      setDocument(response);
      setValue(response.content);
      setInitial(response.content);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    if (!document || value === initial) return;
    setSaving(true);
    setError("");
    setSaved("");
    try {
      await api<{ status: string }>(
        endpoint,
        {
          method: "PUT",
          body: JSON.stringify({ path: document.path, content: value }),
        },
        user.csrf,
      );
      setInitial(value);
      setSaved("Alterações salvas no arquivo.");
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`${resource.label} · ${agent}`}
      description={resource.description}
      wide
      full
    >
      <div
        className={`resource-editor ${treeRoot ? "" : "resource-editor-single"}`}
      >
        {treeRoot && (
          <aside className="resource-browser" aria-label="Arquivos disponíveis">
            <div className="resource-browser-title">
              <FolderOpen size={16} />
              Arquivos
            </div>
            <FileTree node={treeRoot} openFile={openFile} />
          </aside>
        )}
        <section className="resource-document">
          {document && (
            <div className="resource-path-row">
              <code className="resource-path">{document.source_path}</code>
              {!document.editable && (
                <span className="resource-read-only">
                  Evidência · somente leitura
                </span>
              )}
            </div>
          )}
          {loading ? (
            <p className="resource-empty">Carregando arquivo…</p>
          ) : document ? (
            <textarea
              aria-label={`Editor ${document.path}`}
              value={value}
              readOnly={!document.editable}
              onChange={(event) => {
                setValue(event.target.value);
                setSaved("");
              }}
              spellCheck={false}
            />
          ) : treeRoot && resource.guide ? (
            <div className="resource-guide">
              <p className="resource-guide-kicker">MAPA DO FLUXO</p>
              <h3>Como a evidência percorre o Health Check</h3>
              <ol>
                {resource.guide.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              <p>
                Abra uma etapa na lateral para ver o código-fonte ou a SQL
                versionada correspondente.
              </p>
            </div>
          ) : (
            <p className="resource-empty">Escolha um arquivo na lateral.</p>
          )}
          {error && (
            <p className="error-message" role="alert">
              {error}
            </p>
          )}
          <footer className="resource-editor-actions">
            <span>
              {saved ||
                (document && value !== initial ? "Alterações pendentes" : "")}
            </span>
            <Button
              disabled={
                !document || !document.editable || value === initial || saving
              }
              onClick={save}
            >
              <Save size={14} />
              {saving ? "Salvando…" : "Salvar"}
            </Button>
          </footer>
        </section>
      </div>
    </Modal>
  );
}
