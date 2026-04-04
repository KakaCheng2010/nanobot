const DEFAULT_HEADERS = {
  Accept: "application/json"
};

function trimBase(apiBase) {
  return apiBase.replace(/\/$/, "");
}

async function parseJson(response) {
  if (response.status === 204) {
    return null;
  }
  return response.json().catch(() => null);
}

async function ensureOk(response, fallbackMessage) {
  if (response.ok) {
    return parseJson(response);
  }
  const data = await parseJson(response);
  throw new Error(data?.error?.message || fallbackMessage);
}

function normalizeText(content) {
  return String(content || "")
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function firstMeaningfulLine(content, maxLength = 88) {
  const text = normalizeText(content);
  if (!text) {
    return "";
  }

  const firstLine = text
    .split(/(?<=[。！？!?])\s+|\s*\n+\s*/)
    .map((item) => item.trim())
    .find(Boolean);

  const value = firstLine || text;
  return value.length > maxLength ? `${value.slice(0, maxLength).trim()}...` : value;
}

function buildSessionTitle(item) {
  const title = firstMeaningfulLine(item.title, 34);
  if (title) {
    return title;
  }

  const preview = firstMeaningfulLine(item.preview, 34);
  return preview || "新会话";
}

export function mapSessionSummary(item) {
  return {
    id: item.id,
    title: buildSessionTitle(item),
    preview: firstMeaningfulLine(item.preview, 72),
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    messageCount: item.message_count || 0,
    metadata: item.metadata || {}
  };
}

export function mapSessionMessage(sessionId, item) {
  return {
    id: `${sessionId}-${item.id}`,
    role: item.role,
    content: item.content || "",
    files: [],
    createdAt: item.timestamp || new Date().toISOString()
  };
}

export async function listSessions(apiBase) {
  const response = await fetch(`${trimBase(apiBase)}/v1/sessions`, {
    headers: DEFAULT_HEADERS
  });
  const data = await ensureOk(response, "获取会话列表失败");
  return (data?.data || []).map(mapSessionSummary);
}

export async function createSession(apiBase, payload = {}) {
  const response = await fetch(`${trimBase(apiBase)}/v1/sessions`, {
    method: "POST",
    headers: {
      ...DEFAULT_HEADERS,
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
  const data = await ensureOk(response, "创建会话失败");
  return mapSessionSummary(data || {});
}

export async function getSession(apiBase, sessionId) {
  const response = await fetch(`${trimBase(apiBase)}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    headers: DEFAULT_HEADERS
  });
  const data = await ensureOk(response, "加载会话失败");
  const previewSource = data.messages?.[0]?.content || data.messages?.[data.messages.length - 1]?.content || "";
  return {
    session: mapSessionSummary({
      id: data.id,
      title: data.title,
      preview: previewSource,
      created_at: data.created_at,
      updated_at: data.updated_at,
      message_count: data.message_count,
      metadata: data.metadata
    }),
    messages: (data.messages || []).map((item) => mapSessionMessage(sessionId, item))
  };
}

export async function deleteSession(apiBase, sessionId) {
  const response = await fetch(`${trimBase(apiBase)}/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    headers: DEFAULT_HEADERS
  });
  await ensureOk(response, "删除会话失败");
}
