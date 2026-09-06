"""Optional local Mem0/Qdrant backend for TacoAgent task memories."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable
from uuid import uuid4

if TYPE_CHECKING:
    from tacoagent.core.runtime import RuntimeState
    from tacoagent.graph.state import LayeredMemory, TacoGraphState


MEM0_QDRANT_PATH = ".tacoagent/memory/mem0_qdrant"
MEM0_COLLECTION = "tacoagent_task_memories"
MAX_COMPRESSED_MEMORY = 3200
EXTERNAL_MEMORY_DB = ".tacoagent/memory/external_memory.sqlite3"


def _clean_text(value: object, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _dedupe(values: Iterable[object], *, limit: int = 20) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value, 500)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def compress_task_memory(summary: dict[str, Any]) -> dict[str, Any]:
    """Remove noisy fields and produce compact text suitable for embedding."""
    task = _clean_text(summary.get("task", ""), 700)
    outcome = _clean_text(summary.get("summary", ""), 1200)
    plan = _clean_text(summary.get("plan_summary", ""), 600)
    if plan.casefold() in outcome.casefold():
        plan = ""
    todos = [
        _clean_text(item.get("content", ""), 300)
        for item in summary.get("todos", [])
        if isinstance(item, dict)
        and item.get("status") in {"pending", "in_progress", "blocked"}
    ]
    files = _dedupe(summary.get("important_files", []), limit=15)
    tools = _dedupe(summary.get("tools_used", []), limit=12)
    error = _clean_text(summary.get("last_error", ""), 600)

    parts = [f"Task: {task}", f"Outcome: {outcome}"]
    if plan:
        parts.append(f"Plan: {plan}")
    if todos:
        parts.append("Open work: " + "; ".join(_dedupe(todos, limit=10)))
    if files:
        parts.append("Files: " + ", ".join(files))
    if tools:
        parts.append("Tools: " + ", ".join(tools))
    if error and error.casefold() not in outcome.casefold():
        parts.append(f"Failure: {error}")
    text = _clean_text("\n".join(part for part in parts if not part.endswith(": ")), MAX_COMPRESSED_MEMORY)
    fingerprint = hashlib.sha256(text.casefold().encode("utf-8")).hexdigest()
    return {
        "text": text,
        "fingerprint": fingerprint,
        "metadata": {
            "memory_id": str(summary.get("memory_id", "")),
            "created_at": str(summary.get("created_at", "")),
            "session_id": str(summary.get("session_id", "")),
            "session_turn": int(summary.get("session_turn", 0) or 0),
            "status": str(summary.get("status", "")),
            "passed": summary.get("passed"),
            "task": task,
            "summary": outcome,
            "important_files": files,
            "tools_used": tools,
            "fingerprint": fingerprint,
        },
    }


@lru_cache(maxsize=16)
def _create_mem0(workspace: str | Path):
    root = Path(workspace).expanduser().resolve()
    mem0_runtime = root / ".tacoagent" / "memory" / "mem0_runtime"
    mem0_runtime.mkdir(parents=True, exist_ok=True)
    # Mem0 otherwise writes telemetry/config state to ~/.mem0. Keep every
    # external-memory artifact inside TacoAgent's workspace boundary.
    os.environ.setdefault("MEM0_DIR", str(mem0_runtime))
    os.environ.setdefault("MEM0_TELEMETRY", "False")
    from dotenv import load_dotenv

    load_dotenv(override=False)
    from mem0 import Memory

    qdrant_path = root / MEM0_QDRANT_PATH
    qdrant_path.mkdir(parents=True, exist_ok=True)
    openai_config: dict[str, Any] = {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    }
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    if base_url:
        openai_config["openai_base_url"] = base_url
    embedder_config: dict[str, Any] = {
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
    }
    if base_url:
        embedder_config["openai_base_url"] = base_url
    return Memory.from_config(
        {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": str(qdrant_path),
                    "collection_name": MEM0_COLLECTION,
                    "on_disk": True,
                },
            },
            "llm": {"provider": "openai", "config": openai_config},
            "embedder": {"provider": "openai", "config": embedder_config},
        }
    )


def _scope(summary: dict[str, Any], workspace: str | Path) -> str:
    session_id = str(summary.get("session_id", "")).strip()
    if session_id:
        return session_id
    return "workspace-" + hashlib.sha256(
        str(Path(workspace).expanduser().resolve()).encode("utf-8")
    ).hexdigest()[:16]


def add_mem0_task_memory(
    workspace: str | Path, summary: dict[str, Any], *, known_fingerprints: set[str] | None = None
) -> dict[str, Any]:
    """Store compressed memory locally through Mem0, with graceful fallback."""
    compact = compress_task_memory(summary)
    if compact["fingerprint"] in (known_fingerprints or set()):
        return {"ok": True, "stored": False, "deduplicated": True, **compact}
    try:
        memory = _create_mem0(workspace)
        result = memory.add(
            compact["text"],
            user_id=_scope(summary, workspace),
            agent_id="tacoagent",
            run_id=str(summary.get("memory_id", "")) or None,
            metadata=compact["metadata"],
            infer=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "stored": False,
            "error": f"{type(exc).__name__}: {exc}",
            **compact,
        }
    return {"ok": True, "stored": True, "result": result, **compact}


def search_mem0_task_memories(
    workspace: str | Path, query: str, *, session_id: str = "", limit: int = 6
) -> list[dict[str, Any]]:
    if not query.strip() or limit <= 0:
        return []
    scope = session_id or _scope({}, workspace)
    try:
        memory = _create_mem0(workspace)
        try:
            response = memory.search(
                query=query,
                filters={"user_id": scope, "agent_id": "tacoagent"},
                limit=limit,
            )
        except TypeError:
            response = memory.search(query=query, user_id=scope, limit=limit)
    except Exception:
        return []
    items = response.get("results", []) if isinstance(response, dict) else response
    results: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        results.append(
            {
                "memory_id": metadata.get("memory_id") or item.get("id", ""),
                "task": metadata.get("task", ""),
                "summary": metadata.get("summary") or item.get("memory", ""),
                "status": metadata.get("status", ""),
                "important_files": metadata.get("important_files", []),
                "score": item.get("score"),
                "storage": "mem0-local-qdrant",
            }
        )
    return results[:limit]


# Task-summary storage -------------------------------------------------------

TASK_MEMORY_ROOT = ".tacoagent/memory"
TASK_MEMORY_FILE = "task_summaries.jsonl"
LATEST_TASK_SUMMARY_FILE = "LATEST_TASK_SUMMARY.md"
MAX_MEMORY_TEXT = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short(value: object, limit: int = MAX_MEMORY_TEXT) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _unique(values: Iterable[object], *, limit: int = 30) -> list[str]:
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def generate_task_summary(
    task: str,
    events: Iterable[dict[str, Any]],
    *,
    final_answer: str = "",
    route: str = "workflow",
    session_id: str = "",
    session_turn: int = 0,
) -> dict[str, Any]:
    plan_summary, last_error = "", ""
    todos: list[dict[str, str]] = []
    tools: list[str] = []
    files: list[str] = []
    checks: list[dict[str, Any]] = []
    passed: bool | None = None
    event_count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_count += 1
        kind = event.get("type")
        if kind == "plan_snapshot":
            plan_summary = _short(event.get("plan_summary", ""), 1200)
            todos = [
                {
                    "id": str(item.get("id", "")),
                    "content": _short(item.get("content", ""), 400),
                    "status": str(item.get("status", "pending")),
                }
                for item in event.get("todos", [])
                if isinstance(item, dict)
            ][:20]
        elif kind == "tool_call":
            tools.append(str(event.get("name", "")))
            args = event.get("args", {})
            if isinstance(args, dict):
                files.extend(
                    str(args[key])
                    for key in ("path", "file_path", "filename")
                    if args.get(key)
                )
        elif kind == "verifier_result":
            passed = bool(event.get("passed", False))
            last_error = _short(event.get("last_error", ""), 1000)
            checks = [
                dict(item)
                for item in event.get("checks", [])
                if isinstance(item, dict)
            ][:20]
    status = "completed" if passed is not False else "failed"
    summary_text = _short(final_answer, 1800) or plan_summary
    if not summary_text:
        summary_text = f"Task {status}: {_short(task, 800)}"
    return {
        "memory_id": f"memory-{uuid4().hex[:12]}",
        "created_at": _now(),
        "session_id": session_id,
        "session_turn": int(session_turn),
        "route": route,
        "task": _short(task, 1200),
        "summary": summary_text,
        "status": status,
        "passed": passed,
        "plan_summary": plan_summary,
        "todos": todos,
        "tools_used": _unique(tools),
        "important_files": _unique(files),
        "verification_checks": checks,
        "last_error": last_error,
        "event_count": event_count,
    }


def _memory_root(workspace: str | Path) -> Path:
    root = Path(workspace).expanduser().resolve() / TASK_MEMORY_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _external_memory_path(workspace: str | Path) -> Path:
    root = Path(workspace).expanduser().resolve()
    path = root / EXTERNAL_MEMORY_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _external_memory_connection(workspace: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_external_memory_path(workspace), timeout=10)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            memory_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            session_id TEXT NOT NULL,
            session_turn INTEGER NOT NULL,
            task TEXT NOT NULL,
            status TEXT NOT NULL,
            summary_hint TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC)"
    )
    return connection


def store_external_memory(workspace: str | Path, detail: dict[str, Any]) -> dict[str, Any]:
    """Upsert the complete memory record into the local ID-addressable store."""
    memory_id = str(detail.get("memory_id", "")).strip()
    if not memory_id:
        return {"ok": False, "stored": False, "error": "memory_id is required"}
    try:
        with closing(_external_memory_connection(workspace)) as connection:
            connection.execute(
                """
                INSERT INTO memories (
                    memory_id, created_at, session_id, session_turn,
                    task, status, summary_hint, detail_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    created_at=excluded.created_at,
                    session_id=excluded.session_id,
                    session_turn=excluded.session_turn,
                    task=excluded.task,
                    status=excluded.status,
                    summary_hint=excluded.summary_hint,
                    detail_json=excluded.detail_json
                """,
                (
                    memory_id,
                    str(detail.get("created_at", "")),
                    str(detail.get("session_id", "")),
                    int(detail.get("session_turn", 0) or 0),
                    str(detail.get("task", "")),
                    str(detail.get("status", "")),
                    _clean_text(detail.get("summary", ""), 240),
                    json.dumps(detail, ensure_ascii=False, default=str),
                ),
            )
            connection.commit()
    except (OSError, sqlite3.Error) as exc:
        return {"ok": False, "stored": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "stored": True, "memory_id": memory_id, "path": EXTERNAL_MEMORY_DB}


def get_external_memory(workspace: str | Path, memory_id: str) -> dict[str, Any]:
    """Fetch one complete memory by its stable ID without semantic inference."""
    identifier = str(memory_id).strip()
    if not identifier:
        return {"ok": False, "error": "memory_id is required"}
    if not (Path(workspace).expanduser().resolve() / EXTERNAL_MEMORY_DB).is_file():
        return {"ok": False, "error": f"Unknown memory_id: {identifier}"}
    try:
        with closing(_external_memory_connection(workspace)) as connection:
            row = connection.execute(
                "SELECT detail_json FROM memories WHERE memory_id = ?", (identifier,)
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if row is None:
        return {"ok": False, "error": f"Unknown memory_id: {identifier}"}
    try:
        detail = json.loads(row[0])
    except json.JSONDecodeError:
        return {"ok": False, "error": f"Corrupt memory record: {identifier}"}
    return {"ok": True, "memory_id": identifier, "detail": detail}


def load_external_memory_index(
    workspace: str | Path, *, limit: int = 6, memory_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """Load prompt-safe metadata; detailed payloads remain outside model context."""
    if limit <= 0:
        return []
    if not (Path(workspace).expanduser().resolve() / EXTERNAL_MEMORY_DB).is_file():
        return []
    try:
        with closing(_external_memory_connection(workspace)) as connection:
            if memory_ids:
                ordered_ids = list(dict.fromkeys(str(item) for item in memory_ids if item))[:limit]
                placeholders = ",".join("?" for _ in ordered_ids)
                rows = connection.execute(
                    f"SELECT memory_id, created_at, task, status, summary_hint "
                    f"FROM memories WHERE memory_id IN ({placeholders})",
                    ordered_ids,
                ).fetchall()
                by_id = {row[0]: row for row in rows}
                rows = [by_id[item] for item in ordered_ids if item in by_id]
            else:
                rows = connection.execute(
                    "SELECT memory_id, created_at, task, status, summary_hint "
                    "FROM memories ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
    except (OSError, sqlite3.Error):
        return []
    return [
        {
            "memory_id": row[0],
            "created_at": row[1],
            "task": row[2],
            "status": row[3],
            "summary_hint": row[4],
            "detail_available": True,
            "detail_tool": "memory_get",
        }
        for row in rows
    ]


def _markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Latest TacoAgent Task Memory",
        "",
        f"- Memory ID: `{summary.get('memory_id', '')}`",
        f"- Created: {summary.get('created_at', '')}",
        f"- Status: **{summary.get('status', 'unknown')}**",
        f"- Task: {summary.get('task', '')}",
        "",
        "## Summary",
        "",
        str(summary.get("summary", "")),
    ]
    for title, key in (("Tools used", "tools_used"), ("Important files", "important_files")):
        values = summary.get(key, [])
        if values:
            lines.extend(["", f"## {title}", "", *[f"- {item}" for item in values]])
    if summary.get("last_error"):
        lines.extend(["", "## Last error", "", str(summary["last_error"])])
    return "\n".join(lines).rstrip() + "\n"


def _audit_records(workspace: str | Path) -> list[dict[str, Any]]:
    path = Path(workspace).expanduser().resolve() / TASK_MEMORY_ROOT / TASK_MEMORY_FILE
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def save_task_summary(workspace: str | Path, summary: dict[str, Any]) -> dict[str, Any]:
    root = _memory_root(workspace)
    compact = compress_task_memory(summary)
    known = {
        str(item.get("memory_fingerprint", ""))
        for item in _audit_records(workspace)
        if item.get("memory_fingerprint")
    }
    mem0_result = add_mem0_task_memory(workspace, summary, known_fingerprints=known)
    stored = dict(summary)
    stored["memory_fingerprint"] = compact["fingerprint"]
    stored["compressed_memory"] = compact["text"]
    stored["mem0_storage"] = {
        "ok": bool(mem0_result.get("ok")),
        "stored": bool(mem0_result.get("stored")),
        "deduplicated": bool(mem0_result.get("deduplicated")),
        "error": str(mem0_result.get("error", "")),
    }
    stored["external_memory_storage"] = store_external_memory(workspace, stored)
    with (root / TASK_MEMORY_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stored, ensure_ascii=False, default=str) + "\n")
    (root / LATEST_TASK_SUMMARY_FILE).write_text(_markdown(stored), encoding="utf-8")
    return stored


def load_task_summaries(
    workspace: str | Path,
    *,
    limit: int = 6,
    query: str = "",
    session_id: str = "",
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    audit = _audit_records(workspace)
    if not audit:
        return []
    if query.strip():
        recalled = search_mem0_task_memories(
            workspace, query, session_id=session_id, limit=limit
        )
        if recalled:
            return recalled
    return audit[-limit:]


# Layered graph memory -------------------------------------------------------

RULES_LAYER = {
    "scope": "workspace",
    "storage": "internal",
    "rules": [
        "Work inside the current workspace only.",
        "Use paths relative to the workspace; do not prefix paths with workspace/.",
        "Keep durable task context outside the raw messages transcript when possible.",
        "Treat TODO.md as working plan state, NOTEPAD.md as durable notes, HISTORY_SUMMARY.md as compressed history, and .tacoagent/memory as external task memory.",
        "Recent task memories are lightweight indexes; call memory_get with an exact memory_id only when detailed prior context is relevant.",
        "Do not expose memory write tools to agents; layered memory is assembled by the runtime.",
    ],
}


MEMORY_NODE_PROFILES: dict[str, dict[str, Any]] = {
    "single_agent_planner": {
        "working": {"session_id", "session_turn", "attempts", "max_attempts"},
        "history": 700,
        "notepad": 500,
        "external_index": 4,
    },
    "multi_agent_supervisor": {
        "working": {"session_id", "session_turn", "attempts", "max_attempts"},
        "history": 700,
        "notepad": 500,
        "external_index": 4,
    },
    "actor": {
        "working": {"session_id", "session_turn", "attempts", "max_attempts"},
        "history": 800,
        "notepad": 700,
        "external_index": 4,
    },
    "codeAgent": {
        "working": {"session_id", "session_turn", "attempts", "max_attempts"},
        "history": 800,
        "notepad": 700,
        "external_index": 4,
    },
    "verifier": {
        "working": {"session_id", "session_turn", "attempts", "max_attempts"},
        "history": 400,
        "notepad": 300,
        "external_index": 0,
    },
    "searchAgent": {
        "working": {"session_id", "session_turn"},
        "history": 0,
        "notepad": 0,
        "external_index": 0,
    },
    "context_monitor": {
        "working": {"attempts", "max_attempts"},
        "history": 200,
        "notepad": 0,
        "external_index": 0,
    },
    "context_compressor": {
        "working": {
            "session_id", "session_turn", "plan_summary", "todos",
            "acceptance_criteria", "verification_commands", "last_error",
            "attempts", "max_attempts",
        },
        "history": 1200,
        "notepad": 1000,
        "external_index": 0,
    },
}

DEFAULT_MEMORY_PROFILE = {
    "working": {"session_id", "session_turn", "attempts", "max_attempts", "last_error"},
    "history": 600,
    "notepad": 500,
    "external_index": 4,
}


def _short_text(text: object, limit: int) -> str:
    if limit < 0:
        raise ValueError("limit must not be negative")
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return "." * limit if limit <= 3 else value[: limit - 3] + "..."


def _trim_handoffs(handoffs: object) -> list[dict[str, Any]]:
    if not isinstance(handoffs, list):
        return []
    return [dict(item) for item in handoffs[-6:] if isinstance(item, dict)]


def _read_memory_file(runtime: RuntimeState, relative_path: str) -> dict[str, Any]:
    path = runtime.sandbox.resolve(relative_path)
    if not path.exists():
        return {"exists": False, "path": relative_path, "content": ""}
    if not path.is_file():
        return {"exists": False, "path": relative_path, "content": "", "error": "path is not a file"}
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {"exists": True, "path": relative_path, "content": "", "error": f"{type(exc).__name__}: {exc}"}
    return {"exists": True, "path": relative_path, "content": content}


def read_notepad(runtime: RuntimeState) -> dict[str, Any]:
    return _read_memory_file(runtime, "NOTEPAD.md")


def read_history_summary(runtime: RuntimeState) -> dict[str, Any]:
    return _read_memory_file(runtime, "HISTORY_SUMMARY.md")


def recall_external_memory_index(
    workspace: str | Path,
    query: str,
    *,
    session_id: str = "",
    limit: int = 4,
    semantic: bool = False,
) -> list[dict[str, Any]]:
    """Resolve relevant IDs without loading detailed JSONL memory records."""
    if limit <= 0:
        return []
    recalled = search_mem0_task_memories(
        workspace, query, session_id=session_id, limit=limit
    ) if semantic and query.strip() else []
    recalled_ids = [str(item.get("memory_id", "")) for item in recalled]
    return load_external_memory_index(
        workspace, limit=limit, memory_ids=recalled_ids
    ) or load_external_memory_index(workspace, limit=limit)


def build_layered_memory(state: TacoGraphState, *, node: str = "graph") -> LayeredMemory:
    runtime = state.get("runtime")
    if runtime is None:
        raise ValueError("Graph state is missing runtime")
    profile = MEMORY_NODE_PROFILES.get(node, DEFAULT_MEMORY_PROFILE)
    if state.get("memory_profile_mode") == "legacy-uniform":
        profile = {
            "working": {
                "task", "session_id", "session_turn", "plan_summary", "todos",
                "acceptance_criteria", "verification_commands", "research_notes",
                "sources", "agent_handoffs", "code_agent_summary",
                "verifier_summary", "last_error", "attempts", "max_attempts",
            },
            "history": 2200,
            "notepad": 1800,
            "external_index": 6,
        }
    history_limit = int(profile["history"])
    notepad_limit = int(profile["notepad"])
    index_limit = int(profile["external_index"])
    notepad = read_notepad(runtime) if notepad_limit else {"exists": False, "content": ""}
    history = read_history_summary(runtime) if history_limit else {"exists": False, "content": ""}
    task_memories = recall_external_memory_index(
        runtime.workspace,
        str(state.get("task", "")),
        session_id=str(state.get("session_id", "")),
        limit=index_limit,
        semantic=bool(state.get("memory_semantic_recall", False)),
    )
    working_candidates = {
        "node": node,
        "task": state.get("task", ""),
        "session_id": state.get("session_id", ""),
        "session_turn": state.get("session_turn", 0),
        "plan_summary": state.get("plan_summary", ""),
        "todos": state.get("todos", []),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "verification_commands": state.get("verification_commands", []),
        "research_notes": _short_text(state.get("research_notes", ""), 1600),
        "sources": [
            {"title": str(source.get("title", "")), "url": str(source.get("url", ""))}
            for source in state.get("sources", [])
            if isinstance(source, dict)
        ],
        "agent_handoffs": _trim_handoffs(state.get("agent_handoffs", [])),
        "code_agent_summary": _short_text(state.get("code_agent_summary", ""), 1000),
        "verifier_summary": _short_text(state.get("verifier_summary", ""), 1000),
        "last_error": _short_text(state.get("last_error", ""), 1400),
        "attempts": state.get("attempts", 0),
        "max_attempts": state.get("max_attempts", 3),
    }
    working_memory = {"node": node}
    working_memory.update({
        key: value
        for key, value in working_candidates.items()
        if key in profile["working"] and value not in ("", [], {}, None)
        and not (key == "session_turn" and value == 0)
    })
    history_store = {
        "history_path": "HISTORY_SUMMARY.md",
        "history_exists": history.get("exists", False),
        "history_summary": _short_text(history.get("content", ""), history_limit),
        "notepad_path": "NOTEPAD.md",
        "notepad_exists": notepad.get("exists", False),
        "notepad": _short_text(notepad.get("content", ""), notepad_limit),
        "context_summary": _short_text(
            state.get("context_summary", ""),
            1600 if state.get("memory_profile_mode") == "legacy-uniform" else history_limit,
        ),
        "compression_events": state.get("compression_events", [])[(-3 if state.get("memory_profile_mode") == "legacy-uniform" else -1):],
        "external_memory_path": EXTERNAL_MEMORY_DB,
        "external_memory_backend": "sqlite-detail-store+optional-mem0-index",
        "external_memory_detail_path": EXTERNAL_MEMORY_DB,
        "semantic_index_path": MEM0_QDRANT_PATH,
        "recent_task_memories": task_memories,
    }
    return {
        "rules": dict(RULES_LAYER),
        "working_memory": working_memory,
        "history_summary_store": history_store,
    }


def format_layered_memory_for_prompt(memory: LayeredMemory) -> str:
    return json.dumps(memory, ensure_ascii=False, indent=2, default=str)


def memory_event(memory: LayeredMemory, *, node: str) -> dict[str, Any]:
    return {"type": "memory_snapshot", "node": node, "memory": memory}
