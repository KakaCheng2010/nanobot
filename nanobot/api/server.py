"""OpenAI-compatible HTTP API server for a fixed nanobot session.

Provides /v1/chat/completions and /v1/models endpoints.
All requests route to a single persistent API session.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web
from loguru import logger

API_SESSION_KEY = "api:default"
API_CHAT_ID = "default"
_MAX_UPLOAD_SIZE = 20 * 1024 * 1024
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def _with_cors(resp: web.StreamResponse) -> web.StreamResponse:
    """统一给响应补上浏览器直连需要的 CORS 头。"""
    for key, value in _CORS_HEADERS.items():
        resp.headers[key] = value
    return resp


def _error_json(status: int, message: str, err_type: str = "invalid_request_error") -> web.Response:
    return _with_cors(web.json_response(
        {"error": {"message": message, "type": err_type, "code": status}},
        status=status,
    ))


def _chat_completion_response(content: str, model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _chat_completion_chunk(delta: str, model: str, *, finish_reason: str | None = None) -> dict[str, Any]:
    """构造流式响应片段，格式与 OpenAI SSE 兼容。"""
    payload: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": delta} if delta else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return payload


def _response_text(value: Any) -> str:
    """Normalize process_direct output to plain assistant text."""
    if value is None:
        return ""
    if hasattr(value, "content"):
        return str(getattr(value, "content") or "")
    return str(value)


def _parse_bool(value: Any) -> bool:
    """兼容 JSON / FormData 中的布尔字段。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_filename(name: str | None) -> str:
    """只保留文件名主体，避免路径穿越和奇怪字符。"""
    raw = Path(name or "upload.bin").name.strip() or "upload.bin"
    cleaned = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in raw)
    return cleaned or "upload.bin"


def _ensure_upload_dir(request: web.Request) -> Path:
    """把 API 上传统一落到 workspace 下，便于 agent 后续读取。"""
    agent_loop = request.app["agent_loop"]
    upload_dir = Path(agent_loop.workspace) / "uploads" / "api"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


async def _save_uploaded_file(part: Any, upload_dir: Path) -> str:
    """把浏览器上传的文件保存到本地，并返回绝对路径。"""
    original = _sanitize_filename(getattr(part, "filename", None))
    save_name = f"{int(time.time())}_{secrets.token_hex(4)}_{original}"
    file_path = upload_dir / save_name

    size = 0
    with file_path.open("wb") as fh:
        while True:
            chunk = await part.read_chunk()
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_UPLOAD_SIZE:
                fh.close()
                try:
                    file_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise ValueError(f"File '{original}' exceeds {_MAX_UPLOAD_SIZE // (1024 * 1024)}MB limit")
            fh.write(chunk)

    return str(file_path.resolve())


async def _parse_json_request(request: web.Request) -> tuple[str, str, str | None, list[str], bool]:
    """兼容原有 OpenAI 风格 JSON 请求。"""
    try:
        body = await request.json()
    except Exception:
        raise ValueError("Invalid JSON body") from None

    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        raise ValueError("Only a single user message is supported")

    message = messages[0]
    if not isinstance(message, dict) or message.get("role") != "user":
        raise ValueError("Only a single user message is supported")

    user_content = message.get("content", "")
    if isinstance(user_content, list):
        # 现有 API 仍以文本为主；图片上传改由 multipart/form-data 处理。
        user_content = " ".join(
            part.get("text", "") for part in user_content if part.get("type") == "text"
        )

    return (
        str(user_content or ""),
        str(body.get("session_id") or ""),
        body.get("model"),
        [],
        _parse_bool(body.get("stream")),
    )


async def _parse_multipart_request(request: web.Request) -> tuple[str, str, str | None, list[str], bool]:
    """支持浏览器表单上传：文本字段 + 多个文件字段。"""
    reader = await request.multipart()
    upload_dir = _ensure_upload_dir(request)

    user_content = ""
    session_id = ""
    model_name: str | None = None
    files: list[str] = []
    stream = False

    while True:
        part = await reader.next()
        if part is None:
            break

        if part.filename:
            files.append(await _save_uploaded_file(part, upload_dir))
            continue

        value = (await part.text()).strip()
        if part.name in {"message", "content", "text"}:
            user_content = value
        elif part.name == "session_id":
            session_id = value
        elif part.name == "model":
            model_name = value or None
        elif part.name == "stream":
            stream = _parse_bool(value)

    if not user_content and not files:
        raise ValueError("message or files is required")

    return user_content, session_id, model_name, files, stream


async def _parse_request(request: web.Request) -> tuple[str, str, str | None, list[str], bool]:
    """同时兼容 JSON 请求和浏览器 FormData 上传。"""
    content_type = (request.content_type or "").lower()
    if content_type.startswith("multipart/"):
        return await _parse_multipart_request(request)
    return await _parse_json_request(request)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_options(_request: web.Request) -> web.Response:
    """响应浏览器预检请求。"""
    return _with_cors(web.Response(status=204))


async def _write_sse(resp: web.StreamResponse, payload: dict[str, Any] | str) -> None:
    """把一条 SSE 事件写回浏览器。"""
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    await resp.write(f"data: {data}\n\n".encode("utf-8"))


async def _stream_chat_completions(
    request: web.Request,
    *,
    agent_loop: Any,
    model_name: str,
    session_key: str,
    session_lock: asyncio.Lock,
    timeout_s: float,
    user_content: str,
    media_paths: list[str],
) -> web.StreamResponse:
    """把 agent 的增量输出转成浏览器可消费的 SSE。"""
    resp = _with_cors(web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    ))
    await resp.prepare(request)

    emitted_chunks = 0

    async def on_stream(delta: str) -> None:
        nonlocal emitted_chunks
        if not delta:
            return
        emitted_chunks += 1
        # 这里只向前端发送纯增量文本，tool-call 分段切换由 on_stream_end 内部消化。
        await _write_sse(resp, _chat_completion_chunk(delta, model_name))

    async def on_stream_end(*, resuming: bool = False) -> None:
        # resuming=True 表示模型即将转去执行工具，前端不需要额外展示这个边界。
        if resuming:
            return

    try:
        async with session_lock:
            response = await asyncio.wait_for(
                agent_loop.process_direct(
                    content=user_content,
                    session_key=session_key,
                    channel="api",
                    chat_id=API_CHAT_ID,
                    media=media_paths,
                    on_stream=on_stream,
                    on_stream_end=on_stream_end,
                ),
                timeout=timeout_s,
            )
            final_text = _response_text(response)

            # 某些 provider 还没有原生流式实现，可能最后只返回整段文本。
            if final_text and emitted_chunks == 0:
                await _write_sse(resp, _chat_completion_chunk(final_text, model_name))

        await _write_sse(resp, _chat_completion_chunk("", model_name, finish_reason="stop"))
        await _write_sse(resp, "[DONE]")
    except asyncio.TimeoutError:
        await _write_sse(resp, _chat_completion_chunk(
            f"\n[Timed out after {timeout_s}s]",
            model_name,
            finish_reason="stop",
        ))
        await _write_sse(resp, "[DONE]")
    except Exception as exc:
        logger.exception("Streaming API error for session {}", session_key)
        await _write_sse(resp, _chat_completion_chunk(
            f"\n[Error: {exc}]",
            model_name,
            finish_reason="stop",
        ))
        await _write_sse(resp, "[DONE]")
    finally:
        await resp.write_eof()

    return resp


async def handle_chat_completions(request: web.Request) -> web.Response:
    """POST /v1/chat/completions"""
    try:
        user_content, session_id, requested_model, media_paths, wants_stream = await _parse_request(request)
    except ValueError as exc:
        return _error_json(400, str(exc))

    agent_loop = request.app["agent_loop"]
    timeout_s: float = request.app.get("request_timeout", 120.0)
    model_name: str = request.app.get("model_name", "nanobot")
    if requested_model and requested_model != model_name:
        return _error_json(400, f"Only configured model '{model_name}' is available")

    session_key = f"api:{session_id}" if session_id else API_SESSION_KEY
    session_locks: dict[str, asyncio.Lock] = request.app["session_locks"]
    session_lock = session_locks.setdefault(session_key, asyncio.Lock())

    logger.info(
        "API request session_key={} content={} files={} stream={}",
        session_key,
        user_content[:80],
        len(media_paths),
        wants_stream,
    )

    if wants_stream:
        return await _stream_chat_completions(
            request,
            agent_loop=agent_loop,
            model_name=model_name,
            session_key=session_key,
            session_lock=session_lock,
            timeout_s=timeout_s,
            user_content=user_content,
            media_paths=media_paths,
        )

    _FALLBACK = "I've completed processing but have no response to give."

    try:
        async with session_lock:
            try:
                response = await asyncio.wait_for(
                    agent_loop.process_direct(
                        content=user_content,
                        session_key=session_key,
                        channel="api",
                        chat_id=API_CHAT_ID,
                        media=media_paths,
                    ),
                    timeout=timeout_s,
                )
                response_text = _response_text(response)

                if not response_text or not response_text.strip():
                    logger.warning(
                        "Empty response for session {}, retrying",
                        session_key,
                    )
                    retry_response = await asyncio.wait_for(
                        agent_loop.process_direct(
                            content=user_content,
                            session_key=session_key,
                            channel="api",
                            chat_id=API_CHAT_ID,
                            media=media_paths,
                        ),
                        timeout=timeout_s,
                    )
                    response_text = _response_text(retry_response)
                    if not response_text or not response_text.strip():
                        logger.warning(
                            "Empty response after retry for session {}, using fallback",
                            session_key,
                        )
                        response_text = _FALLBACK

            except asyncio.TimeoutError:
                return _error_json(504, f"Request timed out after {timeout_s}s")
            except Exception:
                logger.exception("Error processing request for session {}", session_key)
                return _error_json(500, "Internal server error", err_type="server_error")
    except Exception:
        logger.exception("Unexpected API lock error for session {}", session_key)
        return _error_json(500, "Internal server error", err_type="server_error")

    return _with_cors(web.json_response(_chat_completion_response(response_text, model_name)))


async def handle_models(request: web.Request) -> web.Response:
    """GET /v1/models"""
    model_name = request.app.get("model_name", "nanobot")
    return _with_cors(web.json_response({
        "object": "list",
        "data": [
            {
                "id": model_name,
                "object": "model",
                "created": 0,
                "owned_by": "nanobot",
            }
        ],
    }))


async def handle_health(_request: web.Request) -> web.Response:
    """GET /health"""
    return _with_cors(web.json_response({"status": "ok"}))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(agent_loop, model_name: str = "nanobot", request_timeout: float = 120.0) -> web.Application:
    """Create the aiohttp application.

    Args:
        agent_loop: An initialized AgentLoop instance.
        model_name: Model name reported in responses.
        request_timeout: Per-request timeout in seconds.
    """
    app = web.Application(client_max_size=_MAX_UPLOAD_SIZE)
    app["agent_loop"] = agent_loop
    app["model_name"] = model_name
    app["request_timeout"] = request_timeout
    app["session_locks"] = {}  # per-user locks, keyed by session_key

    app.router.add_route("OPTIONS", "/v1/chat/completions", handle_options)
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_route("OPTIONS", "/v1/models", handle_options)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_route("OPTIONS", "/health", handle_options)
    app.router.add_get("/health", handle_health)
    return app
