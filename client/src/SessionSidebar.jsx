function formatSessionTime(value) {
  if (!value) {
    return "";
  }
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

export default function SessionSidebar({
  sessions,
  activeSessionId,
  loading,
  onCreate,
  onOpen,
  onDelete
}) {
  return (
    <section className="session-card">
      <div className="session-card-header">
        <div>
          <p className="session-card-kicker">Session Center</p>
          <h2>会话管理</h2>
          <p>新建、切换和删除历史会话都走后端 Session API。</p>
        </div>
      </div>

      <div className="session-toolbar">
        <button type="button" className="session-create-button" onClick={onCreate} disabled={loading}>
          新建会话
        </button>
      </div>

      <div className="session-list">
        {sessions.length === 0 ? (
          <div className="session-empty">还没有历史会话，先新建一个开始吧。</div>
        ) : null}

        {sessions.map((session) => (
          <article
            key={session.id}
            className={`session-item ${session.id === activeSessionId ? "active" : ""}`}
          >
            <button
              type="button"
              className="session-main"
              onClick={() => onOpen(session.id)}
              disabled={loading && session.id !== activeSessionId}
            >
              <div className="session-main-top">
                <strong>{session.title || "新会话"}</strong>
                <small>{formatSessionTime(session.updatedAt)}</small>
              </div>
              <span>{session.preview || "暂无消息"}</span>
              <div className="session-main-bottom">
                <small>{session.messageCount} 条消息</small>
              </div>
            </button>

            <div className="session-item-actions">
              <button
                type="button"
                className="session-delete"
                onClick={() => onDelete(session.id)}
                disabled={loading}
                aria-label={`删除会话 ${session.title || session.id}`}
              >
                删除
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
