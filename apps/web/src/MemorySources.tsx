import { useEffect, useState } from "react";
import { api } from "./api";
import { timeText } from "./components";
import type { Message } from "./types";

// Only an expanded panel polls; cached message bodies do not freeze source ACLs.
export default function MemorySources({ conversationId, message }: { conversationId: string; message: Message }) {
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<Message["memory_sources"]>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let disposed = false;
    let loading = false;
    setSources(undefined);
    const load = async () => {
      if (loading) return;
      loading = true;
      try {
        const result = await api<Message["memory_sources"]>(`/conversations/${conversationId}/messages/${message.id}/sources`);
        if (!disposed) { setSources(result); setError(""); }
      } catch {
        if (!disposed) { setSources(undefined); setError("暂时无法核对来源，请重新展开重试。"); }
      } finally { loading = false; }
    };
    void load();
    const timer = setInterval(load, 2500);
    return () => { disposed = true; clearInterval(timer); };
  }, [open, conversationId, message.id]);
  return <details className="message-notes" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>本次输入参考记忆（{message.memory_sources?.length || 0} 条）</summary>
    <p>这些记录已加入本次调用输入，不代表回答逐条引用或内容已核实。匹配分数仅用于排序。</p>
    {error ? <p>{error}</p> : !sources ? <p>正在核对来源…</p> : sources.map(source => <div key={source.id}>
      {source.status === "available" ? <>
        <p>{source.content}</p>
        <small>来源：{source.source} · 匹配分数 {source.score}{source.created_at ? ` · ${timeText(source.created_at)}` : ""}</small>
      </> : <p>{source.status === "changed" ? "原记忆已变更，本次来源不再展示原文。" : "原记忆已删除或不可访问。"}</p>}
    </div>)}
  </details>;
}
