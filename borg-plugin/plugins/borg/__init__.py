"""Hermes-native Borg plugin.

This plugin exposes the Claude OS memory primitives used by the Borg Queen-drone
pattern.  Drone launching itself is now handled by native `delegate_task`
(background synchronous or async), so this plugin no longer registers a legacy
drone-spawning tool.

The higher-level orchestration prompt lives in the companion `borg` skill so
`/borg` loads an agent-facing procedure instead of merely printing plugin text.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_API = "http://localhost:8051"
DEFAULT_COLLECTIVE_KB = "borg-collective"


def _api_base() -> str:
    return os.getenv("CLAUDE_OS_API", DEFAULT_API).rstrip("/")


def _collective_kb() -> str:
    return os.getenv("BORG_COLLECTIVE_KB", DEFAULT_COLLECTIVE_KB)


def _headers(content_type: Optional[str] = "application/json") -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type
    token = os.getenv("BORG_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _json_response(ok: bool, **payload: Any) -> str:
    payload.setdefault("success", ok)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _coerce_args(args: Any) -> dict:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _request_json(method: str, path: str, body: Optional[dict] = None, timeout: int = 30) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{_api_base()}{path}",
        data=data,
        method=method,
        headers=_headers("application/json" if body is not None else None),
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured localhost by default
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _slug(value: str, fallback: str = "drone") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return slug[:80] or fallback


def _check() -> bool:
    try:
        _request_json("GET", "/health", timeout=5)
        return True
    except Exception:
        return False


def borg_status(args: dict, **_: Any) -> str:
    """Return Claude OS/Borg collective status."""
    args = _coerce_args(args)
    try:
        health = _request_json("GET", "/health", timeout=5)
        kbs = _request_json("GET", "/api/kb", timeout=10).get("knowledge_bases", [])
        names = [kb.get("name") for kb in kbs if kb.get("name")]
        return _json_response(
            True,
            api=_api_base(),
            health=health,
            collective_kb=_collective_kb(),
            collective_exists=_collective_kb() in names,
            knowledge_bases=names,
        )
    except Exception as exc:
        return _json_response(False, api=_api_base(), error=str(exc))


def borg_ensure_kb(args: dict, **_: Any) -> str:
    args = _coerce_args(args)
    name = args.get("name") or _collective_kb()
    description = args.get("description") or "Borg collective memory shared by Hermes drones."
    kb_type = args.get("kb_type") or "generic"
    try:
        kbs = _request_json("GET", "/api/kb", timeout=10).get("knowledge_bases", [])
        if any(kb.get("name") == name for kb in kbs):
            return _json_response(True, name=name, existed=True, created=False)
        created = _request_json(
            "POST",
            "/api/kb",
            {"name": name, "kb_type": kb_type, "description": description},
            timeout=30,
        )
        return _json_response(True, name=name, existed=False, created=True, response=created)
    except Exception as exc:
        return _json_response(False, name=name, error=str(exc))


def borg_recall(args: dict, **_: Any) -> str:
    args = _coerce_args(args)
    query = (args.get("query") or "").strip()
    if not query:
        return _json_response(False, error="query is required")
    body: Dict[str, Any] = {"query": query, "top_k": int(args.get("top_k") or 8)}
    if args.get("kb_filter"):
        body["kb_filter"] = str(args["kb_filter"])
    try:
        data = _request_json("POST", "/api/kb/search-all", body, timeout=60)
        results = data.get("results", [])
        compact = [
            {
                "kb_name": r.get("kb_name") or (r.get("metadata") or {}).get("kb_name"),
                "score": r.get("score"),
                "text": (r.get("text") or "")[:2000],
                "metadata": r.get("metadata") or {},
            }
            for r in results
        ]
        return _json_response(True, query=query, results=compact, count=len(compact), raw_keys=sorted(data.keys()))
    except Exception as exc:
        return _json_response(False, query=query, error=str(exc))


def borg_recall_kb(args: dict, **_: Any) -> str:
    args = _coerce_args(args)
    kb = args.get("kb") or args.get("kb_name") or _collective_kb()
    query = (args.get("query") or "").strip()
    if not query:
        return _json_response(False, error="query is required")
    try:
        data = _request_json("POST", f"/api/kb/{urllib.parse.quote(str(kb), safe='')}/chat", {"query": query}, timeout=90)
        return _json_response(True, kb=kb, query=query, answer=data.get("answer") or data.get("response"), sources=data.get("sources", []))
    except Exception as exc:
        return _json_response(False, kb=kb, query=query, error=str(exc))


def _verify_doc(kb: str, filename: str, timeout: int = 15) -> bool:
    """Confirm a document actually landed in a KB.

    An upload can 200 while the document never appears (embedding lag, DB write
    race) — a hard-won Borg pitfall. Never trust the assimilate response alone.
    """
    try:
        data = _request_json(
            "GET",
            f"/api/kb/{urllib.parse.quote(str(kb), safe='')}/documents",
            timeout=timeout,
        )
        docs = data if isinstance(data, list) else data.get("documents", [])
        names = {
            (x.get("filename") or x.get("name") or "") if isinstance(x, dict) else str(x)
            for x in docs
        }
        return filename in names
    except Exception:
        return False


def borg_assimilate(args: dict, **_: Any) -> str:
    args = _coerce_args(args)
    kb = args.get("kb") or args.get("kb_name") or _collective_kb()
    title = (args.get("title") or "Borg assimilation").strip()
    content = args.get("content")
    file_path = args.get("file_path")
    verify = args.get("verify", True)  # auto-verify by default
    if file_path:
        path = Path(file_path).expanduser()
        if not path.exists():
            return _json_response(False, error=f"file_path does not exist: {path}")
        content = path.read_text(encoding="utf-8", errors="replace")
        filename = path.name
    else:
        filename = _slug(title, "borg-assimilation") + ".md"
    if not content:
        return _json_response(False, error="content or file_path is required")
    try:
        data = _request_json(
            "POST",
            f"/api/kb/{urllib.parse.quote(str(kb), safe='')}/documents/content",
            {"filename": filename, "title": title, "content": content},
            timeout=60,
        )
        # Auto-verify: an upload can 200 without the document landing.
        verified = False
        if verify:
            verified = _verify_doc(kb, filename)
        return _json_response(
            True,
            kb=kb,
            title=title,
            filename=filename,
            response=data,
            verified=verified,
            **({"warning": f"{filename} not visible in {kb} yet — verify later (embedding lag?)"} if verify and not verified else {}),
        )
    except Exception as exc:
        return _json_response(False, kb=kb, title=title, error=str(exc))


def borg_verify_doc(args: dict, **_: Any) -> str:
    """Verify that a document landed in a KB (check the documents list)."""
    args = _coerce_args(args)
    kb = args.get("kb") or args.get("kb_name") or _collective_kb()
    filename = (args.get("filename") or "").strip()
    if not filename:
        return _json_response(False, error="filename is required")
    try:
        found = _verify_doc(kb, filename)
        return _json_response(
            True,
            kb=kb,
            filename=filename,
            found=found,
            **({"warning": f"{filename} not found in {kb}"} if not found else {}),
        )
    except Exception as exc:
        return _json_response(False, kb=kb, filename=filename, error=str(exc))


def _schema(name: str, description: str, properties: dict, required: Optional[list] = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    }


def register(ctx):
    ctx.register_tool(
        name="borg_status",
        toolset="borg",
        description="Check Claude OS/Borg collective status.",
        emoji="🛸",
        schema=_schema("borg_status", "Check Claude OS/Borg collective status and list KBs.", {}),
        handler=lambda args, **kw: borg_status(args, **kw),
        check_fn=_check,
    )
    ctx.register_tool(
        name="borg_ensure_kb",
        toolset="borg",
        description="Ensure a Claude OS knowledge base exists.",
        emoji="🧠",
        schema=_schema(
            "borg_ensure_kb",
            "Create a Claude OS knowledge base if it does not already exist.",
            {
                "name": {"type": "string", "description": "KB name. Defaults to BORG_COLLECTIVE_KB or borg-collective."},
                "description": {"type": "string", "description": "KB description."},
                "kb_type": {"type": "string", "description": "Claude OS KB type, usually generic/code/documentation/agent-os."},
            },
        ),
        handler=lambda args, **kw: borg_ensure_kb(args, **kw),
        check_fn=_check,
    )
    ctx.register_tool(
        name="borg_recall",
        toolset="borg",
        description="Search across Claude OS KBs before acting.",
        emoji="🔎",
        schema=_schema(
            "borg_recall",
            "Search the Borg collective / Claude OS memory across knowledge bases.",
            {
                "query": {"type": "string", "description": "Search query."},
                "kb_filter": {"type": "string", "description": "Optional KB-name prefix filter."},
                "top_k": {"type": "integer", "description": "Number of results, default 8."},
            },
            ["query"],
        ),
        handler=lambda args, **kw: borg_recall(args, **kw),
        check_fn=_check,
    )
    ctx.register_tool(
        name="borg_recall_kb",
        toolset="borg",
        description="Ask one Claude OS KB a RAG question.",
        emoji="💬",
        schema=_schema(
            "borg_recall_kb",
            "Query one Claude OS knowledge base with RAG chat.",
            {
                "kb": {"type": "string", "description": "KB name. Defaults to borg-collective."},
                "query": {"type": "string", "description": "Question to ask."},
            },
            ["query"],
        ),
        handler=lambda args, **kw: borg_recall_kb(args, **kw),
        check_fn=_check,
    )
    ctx.register_tool(
        name="borg_assimilate",
        toolset="borg",
        description="Write findings into a Claude OS KB.",
        emoji="🧬",
        schema=_schema(
            "borg_assimilate",
            "Assimilate a markdown report into a Claude OS knowledge base.",
            {
                "kb": {"type": "string", "description": "KB name. Defaults to borg-collective."},
                "title": {"type": "string", "description": "Document title."},
                "content": {"type": "string", "description": "Markdown content to save."},
                "file_path": {"type": "string", "description": "Optional file path to read markdown content from."},
                "verify": {"type": "boolean", "description": "Auto-verify the document landed (default true)."},
            },
            ["title"],
        ),
        handler=lambda args, **kw: borg_assimilate(args, **kw),
        check_fn=_check,
    )
    ctx.register_tool(
        name="borg_verify_doc",
        toolset="borg",
        description="Verify a document landed in a Claude OS KB.",
        emoji="✅",
        schema=_schema(
            "borg_verify_doc",
            "Confirm a document is present in a KB by checking the documents list. "
            "An upload can 200 without the document actually landing (embedding lag, write race).",
            {
                "kb": {"type": "string", "description": "KB name. Defaults to borg-collective."},
                "filename": {"type": "string", "description": "Document filename to check."},
            },
            ["filename"],
        ),
        handler=lambda args, **kw: borg_verify_doc(args, **kw),
        check_fn=_check,
    )
