import { useEffect, useMemo, useRef, useState } from "react";

const DEFAULT_API_BASE = "http://127.0.0.1:8900";

function createSessionId() {
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

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
  const [sessionId, setSessionId] = useState(createSessionId());
  const [model, setModel] = useState("");
  const [input, setInput] = useState("");
  const [files, setFiles] = useState([]);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
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

  const previews = useMemo(() => {
    return files
      .filter((file) => file.type.startsWith("image/"))
      .map((file) => ({
        name: file.name,
        url: URL.createObjectURL(file)
      }));
  }, [files]);

  useEffect(() => {
    return () => {
      previews.forEach((item) => URL.revokeObjectURL(item.url));
    };
  }, [previews]);

  async function appendStreamResponse(response, assistantId) {
    if (!response.body) {
      throw new Error("浏览器不支持流式读取");
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

        // 流式模式下只更新最后一条 assistant 消息，避免不断创建新卡片。
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

  async function handleSubmit(event) {
    event.preventDefault();
    if (loading) {
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

    // 先把用户消息和一个空的 assistant 占位都放进来，方便后面流式追加。
    setMessages((prev) => [
      ...prev,
      {
        id: `user-${now}`,
        role: "user",
        content: text,
        files: localFiles,
        createdAt: now
      },
      {
        id: assistantId,
        role: "assistant",
        content: "",
        files: [],
        createdAt: now + 1
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
    const nextFiles = Array.from(event.target.files || []);
    setFiles(nextFiles);
  }

  return (
    <div className="app-shell">
      <aside className="side-panel">
        <h1>nanobot Client</h1>
        <p className="panel-copy">
          这个页面会直接调用 nanobot 的 <code>/v1/chat/completions</code>。
          现在已经支持文本流式输出，上传的图片和文件会先由 API server 落到本地，再进入 agent 主流程。
        </p>

        <label className="field">
          <span>API 地址</span>
          <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} />
        </label>

        <label className="field">
          <span>Session ID</span>
          <div className="row">
            <input value={sessionId} onChange={(e) => setSessionId(e.target.value)} />
            <button
              type="button"
              className="secondary"
              onClick={() => setSessionId(createSessionId())}
            >
              新建
            </button>
          </div>
        </label>

        <label className="field">
          <span>模型名</span>
          <input
            placeholder="可留空，使用 nanobot 当前配置的模型"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </label>

        <label className="field checkbox-field">
          <input
            type="checkbox"
            checked={useStream}
            onChange={(e) => setUseStream(e.target.checked)}
          />
          <span>启用流式输出</span>
        </label>

        <div className="tips">
          <p>注意事项</p>
          <ul>
            <li>图片会作为多模态输入传给模型。</li>
            <li>普通文件会先上传到本地，后续由 agent 按需读取。</li>
            <li>流式输出使用 SSE，界面会边接收边追加回复。</li>
          </ul>
        </div>
      </aside>

      <main className="chat-panel">
        <div className="message-list" ref={listRef}>
          {messages.length === 0 ? (
            <div className="empty-state">
              <h2>开始和 nanobot 对话</h2>
              <p>你可以直接发文本，也可以上传图片或文件后一起提问。</p>
            </div>
          ) : null}

          {messages.map((message) => (
            <article key={message.id} className={`message-card ${message.role}`}>
              <header>
                <strong>{message.role === "user" ? "你" : "nanobot"}</strong>
                <time>{formatTime(message.createdAt)}</time>
              </header>
              <div className="message-content">{message.content || (loading && message.role === "assistant" ? "..." : "（无文本）")}</div>
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

          {loading ? <div className="loading">nanobot 正在处理...</div> : null}
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            rows={4}
            placeholder="输入问题，或上传图片/文件后一起提问"
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />

          <div className="composer-bar">
            <label className="upload-button">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                onChange={handleFileChange}
              />
              选择文件
            </label>
            <button type="submit" disabled={loading}>
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
