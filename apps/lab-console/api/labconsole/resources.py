from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MAX_RESOURCE_BYTES = 1_000_000
TEXT_EXTENSIONS = {".html", ".json", ".md", ".py", ".sql", ".yaml", ".yml"}


RESOURCE_MAP: dict[str, dict[str, dict[str, Any]]] = {
    "health-check": {
        "skill": {
            "kind": "file",
            "path": "agents/health-check/skills/health-check-skill.md",
        },
        "rules": {
            "kind": "file",
            "path": "agents/health-check/advisor/rules.md",
        },
        "scripts": {
            "kind": "tree",
            "roots": {
                "contracts": "agents/health-check/contracts",
                "advisor": "agents/health-check/advisor",
                "general-report": "agents/health-check/general_report",
                "health-check-refactor": "agents/health-check/refactor_collector",
                "select_latency": "agents/health-check/select_latency",
                "src": "agents/health-check/src",
            },
            "files": {
                "health-check-refactor": [
                    "README.md",
                    "advisor.py",
                    "analysis.schema.json",
                    "collect_query_tuning_snapshot.py",
                    "mcp_publisher.py",
                    "results/analysis.json",
                    "results/latest.html",
                    "results/latest.json",
                    "rules.md",
                    "rules/collector-policy.json",
                    "sql/120_slow_query_refactor.sql",
                ],
            },
        },
    },
    "audit": {
        "skill": {"kind": "file", "path": "agents/audit/skills/audit-skill.md"},
        "rules": {
            "kind": "file",
            "path": "agents/audit/audit_security/advisor/rules.md",
        },
        "scripts": {
            "kind": "tree",
            "roots": {
                "audit_security": "agents/audit/audit_security",
                "contracts": "agents/audit/contracts",
            },
        },
    },
    "simulation": {
        "scripts": {
            "kind": "tree",
            "roots": {
                "acoes": "apps/lab-console/scripts",
                "carga-latencia": "agents/dba/load-tests/sakila-read-only",
            },
            "files": {
                "acoes": [
                    "run-health-select-simulation.py",
                    "run-slow-query-log-demo.py",
                    "run-audit-drop-lab.py",
                    "run-audit-alter-lab.py",
                ],
                "carga-latencia": ["sakila_read_demo_35.py"],
            },
        },
    },
    "dba": {
        "skill": {"kind": "file", "path": "agents/dba/skills/dba-skill.md"},
        "prompt-health-check": {
            "kind": "file",
            "path": "agents/dba/analise-ocorrencia-health-check/prompt.md",
        },
        "prompt-audit": {
            "kind": "file",
            "path": "agents/dba/analise-ocorrencia-audit/prompt.md",
        },
        "scripts": {
            "kind": "tree",
            "roots": {
                "execucao-resumo": "apps/lab-console/api/labconsole",
                "resumo-health-check": ("agents/dba/analise-ocorrencia-health-check"),
                "resumo-audit": "agents/dba/analise-ocorrencia-audit",
            },
            "files": {
                "execucao-resumo": ["incident_analysis.py"],
                "resumo-health-check": ["prompt.md"],
                "resumo-audit": ["prompt.md"],
            },
        },
    },
    "refactor": {
        "skill": {"kind": "file", "path": "agents/refactor/skills/refactor-skill.md"},
        "rules": {
            "kind": "file",
            "path": "agents/refactor/query_refactor/advisor/rules.md",
        },
        "scripts": {
            "kind": "tree",
            "roots": {
                "advisor": "agents/refactor/query_refactor/advisor",
                "execucao": "agents/refactor/query_refactor",
                "jobs-mcp": "agents/refactor/query_refactor/advisor/results",
                "contracts": "agents/refactor/contracts",
            },
            "files": {
                "advisor": ["agent.py"],
                "execucao": ["mysql_client.py", "mcp_publisher.py"],
            },
        },
    },
    "notification": {
        "skill": {
            "kind": "file",
            "path": "agents/notification/skills/notification-skill.md",
        },
        "rules": {
            "kind": "file",
            "path": "agents/notification/notification/advisor/rules.md",
        },
        "scripts": {
            "kind": "tree",
            "roots": {
                "contracts": "agents/notification/contracts",
                "notification": "agents/notification/notification",
            },
        },
    },
    "mcp": {
        "tools": {
            "kind": "tree",
            "roots": {"tools": "mcp/src/mysqlconf_mcp/tools"},
        },
        "contracts": {
            "kind": "tree",
            "roots": {"contracts": "mcp/src/mysqlconf_mcp/contracts"},
        },
        "scripts": {
            "kind": "tree",
            "roots": {
                "health-check": "agents/health-check/src/alerting",
                "health-query-refactor": "agents/health-check/refactor_collector",
                "audit": "agents/audit/audit_security",
                "refactor": "agents/refactor/query_refactor",
                "servidor": "mcp/src/mysqlconf_mcp",
            },
            "files": {
                "health-check": ["mcp_publisher.py"],
                "health-query-refactor": ["mcp_publisher.py"],
                "audit": ["alerting.py"],
                "refactor": ["mcp_publisher.py"],
                "servidor": ["server.py"],
            },
        },
    },
}


class ResourceStore:
    """Small local-only file browser with fixed project paths."""

    def __init__(self, repository: Path):
        self.repository = repository.resolve()

    def view(self, agent: str, resource: str, path: str = "") -> dict[str, Any]:
        spec = self._spec(agent, resource)
        if spec["kind"] == "file":
            target = self._direct_file(spec, path)
            return self._document(target, spec["path"])
        if path:
            return self._document(self._tree_file(spec, path), path)
        return {"kind": "tree", "files": self._tree_files(spec)}

    def write(
        self, agent: str, resource: str, path: str, content: str
    ) -> dict[str, str]:
        if len(content.encode()) > MAX_RESOURCE_BYTES:
            raise ValueError("resource_too_large")
        spec = self._spec(agent, resource)
        target = (
            self._direct_file(spec, path)
            if spec["kind"] == "file"
            else self._tree_file(spec, path)
        )
        if not self._editable(target):
            raise PermissionError("resource_read_only")
        target.write_text(content, encoding="utf-8")
        return {"status": "saved", "path": str(target.relative_to(self.repository))}

    def _spec(self, agent: str, resource: str) -> dict[str, Any]:
        try:
            return RESOURCE_MAP[agent][resource]
        except KeyError as error:
            raise ValueError("resource_not_found") from error

    def _direct_file(self, spec: dict[str, Any], requested: str) -> Path:
        target = self.repository / spec["path"]
        if requested and requested != spec["path"]:
            raise ValueError("resource_not_found")
        return self._checked_file(target)

    def _tree_files(self, spec: dict[str, Any]) -> list[str]:
        files: list[str] = []
        for label, relative in spec["roots"].items():
            root = self._checked_directory(self.repository / relative)
            explicit = spec.get("files", {}).get(label)
            allowed = set(explicit or ())
            for current, directories, names in os.walk(root, followlinks=False):
                directories[:] = [
                    name for name in directories if not name.startswith(".")
                ]
                directory = Path(current)
                if explicit is not None:
                    directory_relative = directory.relative_to(root)
                    prefix = (
                        ""
                        if directory_relative == Path(".")
                        else directory_relative.as_posix() + "/"
                    )
                    directories[:] = [
                        name
                        for name in directories
                        if any(
                            candidate.startswith(prefix + name + "/")
                            for candidate in allowed
                        )
                    ]
                for name in sorted(names):
                    if name.startswith("."):
                        continue
                    candidate = directory / name
                    candidate_relative = candidate.relative_to(root).as_posix()
                    if explicit is not None and candidate_relative not in allowed:
                        continue
                    if candidate.suffix not in TEXT_EXTENSIONS:
                        continue
                    try:
                        self._checked_file(candidate)
                    except ValueError:
                        continue
                    files.append(f"{label}/{candidate.relative_to(root).as_posix()}")
        return sorted(files)

    def _tree_file(self, spec: dict[str, Any], requested: str) -> Path:
        label, separator, relative = requested.partition("/")
        root_relative = spec["roots"].get(label)
        if not separator or not relative or not root_relative:
            raise ValueError("resource_not_found")
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or any(part in {"", ".", ".."} for part in relative_path.parts)
            or relative_path.suffix not in TEXT_EXTENSIONS
        ):
            raise ValueError("resource_not_found")
        root = self._checked_directory(self.repository / root_relative)
        target = self._checked_file(root / relative_path)
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("resource_not_found")
        if target.relative_to(root).as_posix() != relative:
            raise ValueError("resource_not_found")
        explicit = spec.get("files", {}).get(label)
        if explicit is not None and relative not in explicit:
            raise ValueError("resource_not_found")
        return target

    def _checked_directory(self, path: Path) -> Path:
        if (
            path.is_symlink()
            or not path.is_dir()
            or not path.resolve().is_relative_to(self.repository)
        ):
            raise ValueError("resource_not_found")
        return path

    def _checked_file(self, path: Path) -> Path:
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(self.repository)
            or path.stat().st_size > MAX_RESOURCE_BYTES
        ):
            raise ValueError("resource_not_found")
        return path

    def _document(self, path: Path, editable_path: str) -> dict[str, Any]:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("resource_not_text") from error
        return {
            "kind": "file",
            "path": editable_path,
            "source_path": str(path.relative_to(self.repository)),
            "content": content,
            "editable": self._editable(path),
        }

    def _editable(self, path: Path) -> bool:
        """Generated reports are evidence and cannot be modified in the console."""
        return "results" not in path.relative_to(self.repository).parts
