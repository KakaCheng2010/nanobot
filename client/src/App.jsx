import { useEffect, useMemo, useRef, useState } from "react";
import SessionSidebar from "./SessionSidebar.jsx";
import { createSession, deleteSession, getSession, listSessions } from "./sessionApi.js";

const DEFAULT_API_BASE = "http://127.0.0.1:8900";

function formatTime(value) {
  return new Date(value).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  });
}

function parseSseEvents(buffer) {
  const events = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() || "";

  for (const part of parts) {
    const lines = part
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const dataLines = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim());
    if (dataLines.length) {
      events.push(dataLines.join("\n"));
    }
  }

  return { events, rest };
}

export default function App() {
  const [apiBase, setApiBase] = useState(DEFAULT_API_BASE);
  const [sessionId, setSessionId] = useState("");
  const [sessions, setSessions] = useState([]);
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [files, setFiles] = useState([]);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [error, setError] = useState("");
  const [useStream, setUseStream] = useState(true);
  const fileInputRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => {
    if (!listRef.current) {
      return;
    }
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages, loading]);

  const previews = useMemo(
    () =>
      files
        .filter((file) => file.type.startsWith("image/"))
        .map((file) => ({
          name: file.name,
          url: URL.createObjectURL(file)
        })),
    [files]
  );

  useEffect(
    () => () => {
      previews.forEach((item) => URL.revokeObjectURL(item.url));
    },
    [previews]
  );

  async function refreshSessions(preferredSessionId = sessionId) {
    const nextSessions = await listSessions(apiBase);
    setSessions(nextSessions);

    if (!nextSessions.length) {
      const created = await createSession(apiBase);
      setSessions([created]);
      setSessionId(created.id);
      setMessages([]);
      return created.id;
    }

    const hasPreferred = preferredSessionId && nextSessions.some((item) => item.id === preferredSessionId);
    const nextActiveSessionId = hasPreferred ? preferredSessionId : nextSessions[0].id;

    if (nextActiveSessionId !== sessionId) {
      setSessionId(nextActiveSessionId);
    }
    return nextActiveSessionId;
  }

  async function loadSession(targetSessionId) {
    setSessionLoading(true);
    setError("");
    try {
      const data = await getSession(apiBase, targetSessionId);
      setSessionId(data.session.id);
      setMessages(data.messages);
      setSessions((prev) => {
        const others = prev.filter((item) => item.id !== data.session.id);
        return [data.session, ...others];
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载会话失败");
    } finally {
      setSessionLoading(false);
    }
  }

  async function initializeSessions() {
    setSessionLoading(true);
    setError("");
    try {
      const activeSessionId = await refreshSessions("");
      if (activeSessionId) {
        const data = await getSession(apiBase, activeSessionId);
        setMessages(data.messages);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "初始化会话失败");
    } finally {
      setSessionLoading(false);
    }
  }

  useEffect(() => {
    initializeSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase]);

  async function appendStreamResponse(response, assistantId) {
    if (!response.body) {
      throw new Error("当前浏览器不支持流式读取");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseEvents(buffer);
      buffer = parsed.rest;

      for (const event of parsed.events) {
        if (event === "[DONE]") {
          return;
        }

        let payload;
        try {
          payload = JSON.parse(event);
        } catch {
          continue;
        }

        const delta = payload?.choices?.[0]?.delta?.content || "";
        if (!delta) {
          continue;
        }

        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantId
              ? { ...message, content: `${message.content}${delta}` }
              : message
          )
        );
      }
    }
  }

  async function handleCreateSession() {
    setSessionLoading(true);
    setError("");
    try {
      const created = await createSession(apiBase);
      setSessions((prev) => [created, ...prev]);
      setSessionId(created.id);
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建会话失败");
    } finally {
      setSessionLoading(false);
    }
  }

  async function handleDeleteSession(targetSessionId) {
    if (!window.confirm("确认删除这个历史会话吗？")) {
      return;
    }

    setSessionLoading(true);
    setError("");
    try {
      await deleteSession(apiBase, targetSessionId);
      const nextActive = await refreshSessions(targetSessionId === sessionId ? "" : sessionId);
      if (targetSessionId === sessionId && nextActive) {
        const data = await getSession(apiBase, nextActive);
        setMessages(data.messages);
      } else if (!sessions.length) {
        setMessages([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除会话失败");
    } finally {
      setSessionLoading(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (loading || sessionLoading) {
      return;
    }

    const text = input.trim();
    if (!text && files.length === 0) {
      return;
    }

    const now = Date.now();
    const assistantId = `assistant-${now + 1}`;
    const localFiles = files.map((file) => ({
      name: file.name,
      type: file.type || "application/octet-stream",
      size: file.size
    }));

    setMessages((prev) => [
      ...prev,
      {
        id: `user-${now}`,
        role: "user",
        content: text,
        files: localFiles,
        createdAt: new Date(now).toISOString()
      },
      {
        id: assistantId,
        role: "assistant",
        content: "",
        files: [],
        createdAt: new Date(now + 1).toISOString()
      }
    ]);

    const pendingFiles = [...files];
    setInput("");
    setFiles([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
    setError("");
    setLoading(true);

    try {
      const formData = new FormData();
      formData.append("message", text);
      formData.append("session_id", sessionId);
      formData.append("stream", String(useStream));
      if (model.trim()) {
        formData.append("model", model.trim());
      }
      pendingFiles.forEach((file) => formData.append("files", file));

      const response = await fetch(`${apiBase.replace(/\/$/, "")}/v1/chat/completions`, {
        method: "POST",
        body: formData
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data?.error?.message || "请求失败");
      }

      if (useStream) {
        await appendStreamResponse(response, assistantId);
      } else {
        const data = await response.json();
        const reply = data?.choices?.[0]?.message?.content || "";
        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantId
              ? { ...message, content: reply }
              : message
          )
        );
      }

      await refreshSessions(sessionId);
    } catch (err) {
      const message = err instanceof Error ? err.message : "未知错误";
      setError(message);
      setMessages((prev) =>
        prev.map((item) =>
          item.id === assistantId
            ? { ...item, content: `请求失败：${message}` }
            : item
        )
      );
    } finally {
      setLoading(false);
    }
  }

  function handleFileChange(event) {
    setFiles(Array.from(event.target.files || []));
  }

  return (
    <div className="app-shell">
      <aside className="side-panel">
        <div className="brand-block">
          <p className="brand-kicker">Nanobot Workspace</p>
          <h1>会话与审计控制台</h1>
          <p className="panel-copy">
            左侧管理 SQLite 会话，右侧专注聊天与文件交互。整体保持一页完成，不再把操作挤成细长条。
          </p>
        </div>

        <section className="settings-card">
          <label className="field">
            <span>API 地址</span>
            <input value={apiBase} onChange={(event) => setApiBase(event.target.value)} />
          </label>

          <div className="settings-grid">
            <label className="field">
              <span>当前 Session ID</span>
              <input value={sessionId} readOnly />
            </label>

            <label className="field">
              <span>模型名</span>
              <input
                placeholder="可留空，使用当前配置模型"
                value={model}
                onChange={(event) => setModel(event.target.value)}
              />
            </label>
          </div>

          <label className="toggle-row">
            <input
              type="checkbox"
              checked={useStream}
              onChange={(event) => setUseStream(event.target.checked)}
            />
            <div>
              <strong>启用流式输出</strong>
              <p>边生成边显示，更适合长回复和工具执行反馈。</p>
            </div>
          </label>
        </section>

        <SessionSidebar
          sessions={sessions}
          activeSessionId={sessionId}
          loading={sessionLoading || loading}
          onCreate={handleCreateSession}
          onOpen={loadSession}
          onDelete={handleDeleteSession}
        />
      </aside>

      <main className="chat-panel">
        <div className="chat-header">
          <div>
            <p className="chat-kicker">Active Session</p>
            <h2>{sessionId || "未选择会话"}</h2>
          </div>
          <div className="chat-status-group">
            {loading ? <span className="status-pill">处理中</span> : null}
            {sessionLoading ? <span className="status-pill muted">同步会话</span> : null}
          </div>
        </div>

        <div className="message-list" ref={listRef}>
          {messages.length === 0 ? (
            <div className="empty-state">
              <h2>开始和 nanobot 对话</h2>
              <p>先在左侧创建或打开一个会话，然后发送文本、图片或文件。</p>
            </div>
          ) : null}

          {messages.map((message) => (
            <article key={message.id} className={`message-card ${message.role}`}>
              <header>
                <strong>{message.role === "user" ? "你" : "nanobot"}</strong>
                <time>{formatTime(message.createdAt)}</time>
              </header>
              <div className="message-content">
                {message.content || (loading && message.role === "assistant" ? "..." : "（无文本）")}
              </div>
              {message.files?.length ? (
                <ul className="file-list">
                  {message.files.map((file) => (
                    <li key={`${message.id}-${file.name}`}>
                      {file.name}
                      <span>{Math.max(1, Math.round(file.size / 1024))} KB</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))}
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            rows={4}
            placeholder="输入问题，或上传图片 / 文件后一并提问"
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />

          <div className="composer-bar">
            <label className="upload-button">
              <input ref={fileInputRef} type="file" multiple onChange={handleFileChange} />
              选择文件
            </label>
            <button type="submit" disabled={loading || sessionLoading || !sessionId}>
              {loading ? "发送中..." : "发送"}
            </button>
          </div>

          {files.length ? (
            <div className="attachment-area">
              <div className="attachment-list">
                {files.map((file) => (
                  <div className="attachment-chip" key={`${file.name}-${file.size}`}>
                    <span>{file.name}</span>
                    <button
                      type="button"
                      onClick={() =>
                        setFiles((prev) =>
                          prev.filter((item) => !(item.name === file.name && item.size === file.size))
                        )
                      }
                    >
                      删除
                    </button>
                  </div>
                ))}
              </div>

              {previews.length ? (
                <div className="preview-grid">
                  {previews.map((item) => (
                    <figure key={item.url} className="preview-card">
                      <img src={item.url} alt={item.name} />
                      <figcaption>{item.name}</figcaption>
                    </figure>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          {error ? <div className="error-banner">{error}</div> : null}
        </form>
      </main>
    </div>
  );
}
