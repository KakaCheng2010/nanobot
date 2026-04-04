"""Session management HTTP API."""

from __future__ import annotations

import uuid
from typing import Any

from aiohttp import web

from nanobot.api.server import _error_json, _with_cors, handle_options

API_PREFIX = "api:"


def _public_session_id(key: str) -> str:
    return key[len(API_PREFIX):] if key.startswith(API_PREFIX) else key


def _session_key(session_id: str) -> str:
    return f"{API_PREFIX}{session_id}"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "image_url":
                    parts.append("[image]")
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return str(content)


def _session_summary(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    return {
        "id": _public_session_id(item["key"]),
        "title": metadata.get("title") or item.get("preview") or "新会话",
        "preview": item.get("preview") or "",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "message_count": item.get("message_count", 0),
        "metadata": metadata,
    }


async def list_sessions(request: web.Request) -> web.Response:
    manager = request.app["session_manager"]
    sessions = manager.list_sessions(prefix=API_PREFIX)
    payload = {"object": "list", "data": [_session_summary(item) for item in sessions]}
    return _with_cors(web.json_response(payload))


async def create_session(request: web.Request) -> web.Response:
    manager = request.app["session_manager"]
    try:
        body = await request.json()
    except Exception:
        body = {}

    session_id = str(body.get("session_id") or "").strip() or f"web-{uuid.uuid4().hex[:12]}"
    title = str(body.get("title") or "").strip()
    session = manager.get(_session_key(session_id))
    if session is None:
        metadata = {"title": title} if title else {}
        session = manager.create_session(_session_key(session_id), metadata=metadata)
    elif title:
        session.metadata["title"] = title
        manager.save(session)

    summary = _session_summary(
        {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "message_count": len(session.messages),
            "preview": "",
            "metadata": session.metadata,
        }
    )
    return _with_cors(web.json_response(summary, status=201))


async def get_session(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    manager = request.app["session_manager"]
    session = manager.get(_session_key(session_id))
    if session is None:
        return _error_json(404, f"Session '{session_id}' not found")

    payload = {
        "id": session_id,
        "title": session.metadata.get("title") or "新会话",
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "message_count": len(session.messages),
        "metadata": session.metadata,
        "messages": [
            {
                "id": index + 1,
                "role": message.get("role", "assistant"),
                "content": _message_text(message.get("content", "")),
                "timestamp": message.get("timestamp"),
                "name": message.get("name"),
                "tool_call_id": message.get("tool_call_id"),
            }
            for index, message in enumerate(session.messages)
        ],
    }
    return _with_cors(web.json_response(payload))


async def delete_session(request: web.Request) -> web.Response:
    session_id = request.match_info["session_id"]
    key = _session_key(session_id)
    locks = request.app["session_locks"]
    lock = locks.get(key)
    if lock and lock.locked():
        return _error_json(409, "Session is busy, please retry later", err_type="conflict_error")

    manager = request.app["session_manager"]
    deleted = manager.delete_session(key)
    if not deleted:
        return _error_json(404, f"Session '{session_id}' not found")
    return _with_cors(web.Response(status=204))


def register_session_routes(app: web.Application) -> None:
    app.router.add_route("OPTIONS", "/v1/sessions", handle_options)
    app.router.add_get("/v1/sessions", list_sessions)
    app.router.add_post("/v1/sessions", create_session)
    app.router.add_route("OPTIONS", "/v1/sessions/{session_id}", handle_options)
    app.router.add_get("/v1/sessions/{session_id}", get_session)
    app.router.add_delete("/v1/sessions/{session_id}", delete_session)
