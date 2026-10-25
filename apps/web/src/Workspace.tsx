import { useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Check,
  ChevronDown,
  CircleHelp,
  FileCheck2,
  LockKeyhole,
  MessageCircle,
  Pause,
  Play,
  Send,
  ShieldCheck,
  Square,
  Users,
  X,
} from "lucide-react";
import { api } from "./api";
import MemorySources from "./MemorySources";
import {
  Avatar,
  Badge,
  BudgetCalculator,
  capabilityNames,
  isActive,
  modeNames,
  statusNames,
  timeText,
  Trace,
} from "./components";
import type { Budget, Detail, MessagePage, Run, State } from "./types";

export default function Workspace({
  id,
  state,
  refresh,
  back,
  notify,
}: {
  id: string;
  state: State;
  refresh: () => Promise<void>;
  back: () => void;
  notify: (message: string) => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [maxCalls, setMaxCalls] = useState(8);
  const [intent, setIntent] = useState("auto");
  const [showBudget, setShowBudget] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [tab, setTab] = useState("overview");
  const [mcpQuery, setMcpQuery] = useState("目标拆解");
  const [mcpBusy, setMcpBusy] = useState(false);
  const [mcpResult, setMcpResult] = useState<{ protocol: string; server: string; transport: string; hits: { id: string; title: string; excerpt: string; source: string }[] } | null>(null);
  const [mobileContext, setMobileContext] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const run = detail?.runs.at(-1);
  const conv = detail?.conversation;
  const session = useRef({ id, generation: 0, latest: null as string | null, loading: false });
  const [olderPage, setOlderPage] = useState<MessagePage["page"] | null>(null);
  const [olderBusy, setOlderBusy] = useState(false);
  const prepending = useRef(false);
  async function load() {
    const token = session.current;
    if (token.id !== id || token.loading) return;
    token.loading = true;
    try {
      const after = token.latest;
      const d = await api<Detail>(`/conversations/${id}?compact=true${after ? `&after=${encodeURIComponent(after)}` : ""}`);
      if (session.current !== token) return;
      token.latest = d.page?.newest || token.latest;
      if (!after) setOlderPage(d.page || null);
      setDetail((previous) => {
        if (!previous || !after) return d;
        const seen = new Set(previous.messages.map(m => m.id));
        return { ...d, messages: [...previous.messages, ...d.messages.filter(m => !seen.has(m.id))] };
      });
      setLoadError("");
    } catch (e) {
      if (session.current === token) setLoadError((e as Error).message);
    } finally {
      token.loading = false;
    }
  }
  async function loadOlder() {
    if (!olderPage?.has_more || !olderPage.oldest || olderBusy) return;
    const token = session.current;
    const container = document.querySelector(".conversation-messages");
    const height = container?.scrollHeight;
    const scrollTop = container?.scrollTop;
    setOlderBusy(true);
    try {
      const result = await api<MessagePage>(`/conversations/${id}/messages?before=${encodeURIComponent(olderPage.oldest)}`);
      if (session.current !== token) return;
      prepending.current = true;
      setDetail(previous => {
        if (!previous) return previous;
        const seen = new Set(previous.messages.map(m => m.id));
        return { ...previous, messages: [...result.messages.filter(m => !seen.has(m.id)), ...previous.messages] };
      });
      setOlderPage(result.page);
      requestAnimationFrame(() => {
        if (session.current === token && container && height !== undefined && scrollTop !== undefined) {
          container.scrollTop = scrollTop + container.scrollHeight - height;
        }
      });
    } catch (e) {
      if (session.current === token) notify((e as Error).message);
    } finally {
      if (session.current === token) setOlderBusy(false);
    }
  }
  useEffect(() => {
    session.current = { id, generation: session.current.generation + 1, latest: null, loading: false };
    setDetail(null);
    setOlderPage(null);
    setOlderBusy(false);
    setText("");
    setIntent("auto");
    void load();
    const timer = setInterval(() => void load(), 1200);
    return () => {
      session.current = { id: "", generation: session.current.generation + 1, latest: null, loading: false };
      clearInterval(timer);
    };
  }, [id]);
  useEffect(() => {
    if (prepending.current) { prepending.current = false; return; }
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [detail?.messages.length]);
  async function send(content = text, finance?: Budget) {
    if (!content.trim() || busy) return;
    setBusy(true);
    try {
      await api(`/conversations/${id}/messages`, "POST", {
        content,
        max_calls: maxCalls,
        intent,
        finance,
      });
      setText("");
      setIntent("auto");
      await Promise.all([load(), refresh()]);
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function control(operation: string) {
    setBusy(true);
    try {
      await api(
        `/runs/${run!.id}/${operation}`,
        "POST",
        operation === "resume"
          ? { max_calls: Math.min(12, Math.max(maxCalls, run!.calls + 3)) }
          : {},
      );
      await Promise.all([load(), refresh()]);
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function accept(accepted: boolean) {
    setBusy(true);
    try {
      await api(`/decisions/${run!.id}/acceptances`, "POST", {
        version: run!.version,
        accepted,
      });
      await Promise.all([load(), refresh()]);
      notify(accepted ? "你的确认已保存" : "已保留意见，可以继续讨论");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function proposeTasks() {
    setBusy(true);
    try {
      await api(`/runs/${run!.id}/tasks/proposal`, "POST", {});
      await Promise.all([load(), refresh()]);
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (loadError)
    return (
      <div className="page">
        <button className="text-button" onClick={back}>
          <ArrowLeft size={16} />
          返回
        </button>
        <div className="empty">
          <h3>暂时无法打开会话</h3>
          <p>{loadError}</p>
          <button className="secondary" onClick={load}>
            重新加载
          </button>
        </div>
      </div>
    );
  if (!detail || !conv)
    return <div className="page loading">正在打开这个空间…</div>;
  const meeting = conv.mode === "meeting";
  const members = state.members.filter((m) => conv.participants.includes(m.id));
  const chars = conv.character_snapshots;
  const starter =
    conv.mode === "meeting"
      ? "下个月安排一次家庭旅行，预算 8000 元，别影响孩子上课。请一起讨论可行方案。"
      : conv.mode === "planning"
        ? "我想在三个月内提升英语，每天可以投入半小时，帮我做一个可坚持的计划。"
        : conv.mode === "support"
          ? "最近总觉得很累，事情还没有做完就开始担心，我想先找人聊一聊。"
          : conv.mode === "finance"
            ? "帮我梳理本月收支，看看哪些支出可以调整。"
            : "帮我整理今天要做的事情，先找出最重要的一件。";
  const blocked = busy || isActive(run) || run?.status === "paused";
  const decisionProposal = run?.proposal?.kind === "decision";
  const proposalMembers = run?.proposal?.required_approvers
    ? members.filter((m) => run.proposal!.required_approvers!.includes(m.id))
    : members;
  const canApprove = proposalMembers.some((m) => m.id === state.user.id);
  const createdCount =
    run?.created_task_ids?.length ||
    state.tasks.filter((t) => t.source_run === run?.id).length;
  const canConvert =
    run?.status === "succeeded" &&
    run.owner_id === state.user.id &&
    !createdCount &&
    run.intent?.allow_actions &&
    run.results.at(-1)?.suggested_actions.length &&
    (!meeting || !!run.decision);

  return (
    <div className="workspace">
      <header className="workspace-header">
        <button className="icon-button" aria-label="返回列表" onClick={back}>
          <ArrowLeft size={19} />
        </button>
        <div className="workspace-title">
          <div className="inline">
            <h2>{conv.title}</h2>
            <Badge color={meeting ? "lavender" : "sage"}>
              {modeNames[conv.mode]}
            </Badge>
          </div>
          <p>
            {meeting ? <Users size={12} /> : <LockKeyhole size={12} />}{" "}
            {meeting
              ? `${members.map((m) => m.name).join("、")} 可见`
              : "仅自己可见"}
            <span>·</span>
            {chars.length} 位 AI 伙伴{meeting && " + 主持人"}
          </p>
        </div>
        <Badge>{statusNames[run?.status || conv.status]}</Badge>
        <button
          className="icon-button mobile-context-toggle"
          aria-label="查看协作与运行"
          onClick={() => setMobileContext(!mobileContext)}
        >
          <Activity size={18} />
        </button>
      </header>
      <div className="workspace-body">
        <section className="conversation-panel">
          <div className="conversation-messages">
            {!detail.messages.length && (
              <div className="conversation-welcome">
                <div className="avatar-stack">
                  {chars.map((c) => (
                    <Avatar character={c} key={c.id} />
                  ))}
                </div>
                <span className="eyebrow">A SPACE TO THINK TOGETHER</span>
                <h2>
                  {meeting
                    ? "好的讨论，从一个问题开始"
                    : conv.mode === "support"
                      ? "这里，可以慢慢说"
                      : "把想法，变成下一步"}
                </h2>
                <p>
                  {meeting
                    ? "主持人会邀请伙伴提供不同视角，再由真实成员决定。"
                    : conv.mode === "support"
                      ? "先倾听，或一起想办法。你可以选择此刻需要的陪伴。"
                      : "告诉伙伴你的目标和约束，一起找一个具体的起点。"}
                </p>
                <button
                  className="starter-prompt"
                  onClick={() => setText(starter)}
                >
                  {starter}
                  <Send size={16} />
                </button>
                {conv.mode === "finance" && (
                  <BudgetCalculator
                    onAnalyze={(b) =>
                      send(
                        "请根据本次录入的收入与支出分析预算，明确结余与可调整项。",
                        b,
                      )
                    }
                    notify={notify}
                  />
                )}
              </div>
            )}
            {!!conv?.constraints?.length && <div className="message-notes">
              <strong>本次空间的持续约束</strong>
              {conv.constraints.map((constraint, i) => <p key={i}>{constraint}</p>)}
              <p>每轮保留在输入中；AI 是否遵守仍需核对。</p>
            </div>}
            {olderPage?.has_more && <button className="text-button" onClick={() => void loadOlder()} disabled={olderBusy}>
              {olderBusy ? "正在加载…" : "加载更早的消息"}
            </button>}
            {detail.messages.map((message) => (
              <article
                className={`message ${message.actor_type === "human" ? "human" : ""}`}
                key={message.id}
              >
                {message.actor_type === "agent" ? (
                  <Avatar
                    small
                    character={{
                      name: message.actor_name,
                      color: message.color,
                    }}
                  />
                ) : (
                  <span className="human-avatar">{message.actor_name[0]}</span>
                )}
                <div className="message-body">
                  <div className="message-meta">
                    <strong>{message.actor_name}</strong>
                    {message.actor_type === "agent" && (
                      <span className="ai-label">
                        AI{message.provider === "demo" ? " · 示例" : ""}
                      </span>
                    )}
                    {message.stage === "summary" && <Badge>讨论汇总</Badge>}
                    <time>{timeText(message.created_at)}</time>
                  </div>
                  <p className="message-text">{message.content}</p>
                  {!!message.memory_sources?.length && <MemorySources conversationId={id} message={message} />}
                  {message.stage === "expert" && detail.conversation.orchestration === "independent" && <Badge>独立首轮观点</Badge>}
                  {!!message.result?.consensus?.length && (
                    <div className="message-notes">
                      <strong>AI 共同建议 · 尚待成员确认</strong>
                      {message.result.consensus.map((text, i) => <p key={i}>{text}</p>)}
                    </div>
                  )}
                  {!!message.result?.disagreements?.length && (
                    <div className="message-notes">
                      <strong>分歧与待澄清取舍 · AI 整理</strong>
                      <p>引用已核对本轮专家原文；是否构成分歧仍需判断。</p>
                      {message.result.disagreements.map((item, i) => (
                        <div key={i}>
                          <strong>{item.topic}</strong>
                          {item.positions.map((position, j) => <p key={j}>
                            {message.result?.expert_sources?.find((source) => source.source_id === position.source_id)?.actor_name || position.source_id}：{position.quote}
                          </p>)}
                          <p>请澄清：{item.question}</p>
                        </div>
                      ))}
                    </div>
                  )}
                  {message.result?.proposals &&
                    message.result.proposals.length > 0 && (
                      <ul className="proposal-list">
                        {message.result.proposals.map((p, i) => (
                          <li key={i}>{p}</li>
                        ))}
                      </ul>
                    )}
                  {message.result &&
                    (message.result.missing_information.length > 0 ||
                      message.result.assumptions.length > 0 ||
                      message.result.risks.length > 0) && (
                      <details className="message-notes">
                        <summary>
                          <CircleHelp size={13} />
                          待确认信息与依据
                        </summary>
                        {message.result.missing_information.map((p, i) => (
                          <p key={`m${i}`}>待确认：{p}</p>
                        ))}
                        {message.result.assumptions.map((p, i) => (
                          <p key={`a${i}`}>假设：{p}</p>
                        ))}
                        {message.result.risks.map((p, i) => (
                          <p key={`r${i}`}>注意：{p}</p>
                        ))}
                      </details>
                    )}
                </div>
              </article>
            ))}
            {isActive(run) && (
              <div className="typing">
                <span />
                <span />
                <span />
                <p>
                  {run?.pending_tool ? `正在执行 ${run.pending_tool.name}，可暂停或停止` : `${run?.steps[run.cursor]?.character.name || "伙伴"} 正在整理想法`}
                </p>
              </div>
            )}
            {run?.error && (
              <div className="inline-alert">
                {run.error}。可以从右侧恢复，或停止后重新尝试。
              </div>
            )}
            {run?.status === "waiting_approval" && run.proposal && (
              <div className="decision-card">
                <div className="inline">
                  <span className="decision-icon">
                    <FileCheck2 size={21} />
                  </span>
                  <div>
                    <span className="eyebrow">YOUR VOICE MATTERS</span>
                    <h3>
                      {decisionProposal
                        ? "讨论有了方向，决定交给你们"
                        : "确认后，让计划开始行动"}
                    </h3>
                  </div>
                </div>
                <p className="muted">
                  {decisionProposal
                    ? "每位参与成员都需要分别确认讨论结论。此次确认不会创建任何任务。"
                    : `这是独立的任务草案，确认后创建给 ${state.members.find((m) => m.id === run.owner_id)?.name || "发起人"} 的待办。`}
                </p>
                {decisionProposal && (
                  <p className="message-text">{run.proposal.summary}</p>
                )}
                {decisionProposal && run.proposal.actions.length > 0 && (
                  <p className="tiny muted">
                    潜在行动，仅供参考；需之后单独转成任务并确认。
                  </p>
                )}
                <ol>
                  {run.proposal.actions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ol>
                <div className="acceptance-list">
                  {proposalMembers.map((m) => (
                    <span
                      key={m.id}
                      className={run.approvals[m.id] ? "accepted" : ""}
                    >
                      {run.approvals[m.id] ? (
                        <Check size={13} />
                      ) : (
                        <ClockIcon />
                      )}
                      {m.name} · {run.approvals[m.id] ? "已确认" : "待确认"}
                    </span>
                  ))}
                </div>
                {!canApprove ? (
                  <p className="muted">等待发起人确认自己的任务草案。</p>
                ) : !run.approvals[state.user.id] ? (
                  <div className="button-row">
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() => accept(true)}
                    >
                      <Check size={16} />
                      {decisionProposal ? "确认讨论结论" : "确认并创建任务"}
                    </button>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => accept(false)}
                    >
                      还想调整一下
                    </button>
                  </div>
                ) : (
                  <p className="success-line">
                    <Check size={15} />
                    你已确认，等待其他成员。可在设置中切换体验账号测试。
                  </p>
                )}
              </div>
            )}
            {run?.status === "succeeded" && createdCount > 0 && (
              <div className="success-banner">
                <Check size={18} />
                已确认并创建 {createdCount} 项任务，可在任务清单查看。
              </div>
            )}
            {run?.status === "succeeded" && run.decision && !createdCount && (
              <div className="success-banner">
                <Check size={18} />
                讨论结论已确认，尚未创建任务。
              </div>
            )}
            {!!canConvert && (
              <div className="optional-actions">
                <h3>
                  {meeting
                    ? "要把潜在行动转成自己的待办吗？"
                    : "这些建议，需要变成待办吗？"}
                </h3>
                <p className="muted">
                  可选操作。点击后先预览草案，再确认创建；不会重新调用模型。
                </p>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={proposeTasks}
                >
                  <FileCheck2 size={15} />
                  整理为任务草案
                </button>
              </div>
            )}
            {run?.status === "waiting_user" && (
              <div className="inline-alert">
                成员提出了修改意见。请补充想法，开启新一轮讨论。
              </div>
            )}
            <div ref={bottom} />
          </div>
          <div className="composer-area">
            {showBudget && conv.mode === "finance" && (
              <div className="composer-budget">
                <button
                  className="icon-button"
                  aria-label="收起预算工具"
                  onClick={() => setShowBudget(false)}
                >
                  <X size={16} />
                </button>
                <BudgetCalculator
                  onAnalyze={(b) =>
                    send(
                      "请分析这份本期收支预算，列出事实、假设和行动建议。",
                      b,
                    )
                  }
                  notify={notify}
                />
              </div>
            )}
            <div className="composer">
              <textarea
                aria-label="输入消息"
                placeholder={
                  meeting
                    ? "分享你的议题、约束或不同意见…"
                    : "说说你现在的想法…"
                }
                value={text}
                maxLength={8000}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    if (!blocked) void send();
                  }
                }}
              />
              <div className="intent-picker">
                <label htmlFor="message-intent">本条消息</label>
                <select
                  id="message-intent"
                  aria-label="本条消息的处理方式"
                  value={intent}
                  disabled={blocked}
                  onChange={(e) => setIntent(e.target.value)}
                >
                  <option value="auto">自动判断</option>
                  <option value="question">只解释问题</option>
                  <option value="support">先听我说</option>
                  <option value="advice">给我建议</option>
                  <option value="planning">制定计划</option>
                  <option value="task">准备任务草案</option>
                  {meeting && <option value="discussion">共同讨论</option>}
                </select>
              </div>
              <div className="composer-toolbar">
                <div className="inline">
                  <span className="tiny muted">
                    Enter 发送 · Shift + Enter 换行
                  </span>
                  {conv.mode === "finance" && (
                    <button
                      className="text-button"
                      onClick={() => setShowBudget(!showBudget)}
                    >
                      预算工具
                    </button>
                  )}
                </div>
                <button
                  className="send-button"
                  aria-label="发送消息"
                  disabled={blocked || !text.trim()}
                  onClick={() => send()}
                >
                  <Send size={17} />
                </button>
              </div>
            </div>
            <p className="composer-note">
              <ShieldCheck size={12} />
              {state.provider === "demo"
                ? "演示模式：示例回复用于验证流程，任务和记录会真实保存。"
                : "AI 建议需要你的判断，外部动作不会自动执行。"}
              {conv.mode === "support" && " 不读取长期记忆。"}
            </p>
          </div>
        </section>
        <aside
          className={`context-panel ${mobileContext ? "mobile-visible" : ""}`}
        >
          <button
            className="icon-button mobile-context-close"
            aria-label="关闭协作面板"
            onClick={() => setMobileContext(false)}
          >
            <X size={18} />
          </button>
          <div className="context-tabs">
            <button
              className={tab === "overview" ? "active" : ""}
              onClick={() => setTab("overview")}
            >
              协作空间
            </button>
            <button
              className={tab === "trace" ? "active" : ""}
              onClick={() => setTab("trace")}
            >
              运行观察
            </button>
          </div>
          {tab === "overview" ? (
            <>
              <div className="context-section">
                <span className="eyebrow">YOUR COMPANIONS</span>
                <h3>这次，一起想</h3>
                {meeting && (
                  <div className="companion-row">
                    <Avatar
                      small
                      character={state.characters.find((c) => c.id === "host")}
                    />
                    <div>
                      <strong>知和</strong>
                      <p>主持讨论 · 整理共识与分歧</p>
                    </div>
                    <span className="status-dot" />
                  </div>
                )}
                {chars.map((c) => (
                  <div className="companion-row" key={c.id}>
                    <Avatar small character={c} />
                    <div>
                      <strong>{c.name}</strong>
                      <p>
                        {capabilityNames[c.capability]} · v{c.version}
                      </p>
                    </div>
                    <span className="status-dot" />
                  </div>
                ))}
              </div>
              <div className="context-section">
                {run?.intent && (
                  <div className="current-intent">
                    <Badge>{run.intent.label}</Badge>
                    <p className="tiny muted">{run.intent.reason}</p>
                  </div>
                )}
                <span className="eyebrow">SPACE BOUNDARIES</span>
                <h3>安心的边界</h3>
                <div className="boundary-row">
                  <LockKeyhole size={16} />
                  <p>
                    {meeting
                      ? "只有受邀家庭成员可见。个人私密聊天不会进入会议。"
                      : "这是你的个人空间。其他家庭成员无法查看。"}
                  </p>
                </div>
                <div className="boundary-row">
                  <ShieldCheck size={16} />
                  <p>
                    {conv.use_memory
                      ? "已开启个人记忆，按本轮问题匹配相关记录；无匹配则不注入。"
                      : "本会话不读取长期记忆。"}
                  </p>
                </div>
              </div>
              {!!state.tool_catalog?.length && <div className="context-section">
                <h3>工具权限</h3>
                <p className="tiny muted">目录仅说明能力，执行仍由服务端检查。</p>
                {state.tool_catalog.map((tool) => <div key={tool.name} className="message-notes">
                  <strong>{tool.name}</strong>
                  <p>{tool.description}</p>
                  <Badge>{tool.requires_approval ? "仅用户确认后执行" : run?.available_tools?.includes(tool.name) ? "本次角色可请求" : "按角色与空间授权"}</Badge>
                </div>)}
              </div>}
              <div className="context-section">
                <h3>MCP 本地参考库</h3>
                <p className="tiny muted">仅检索内置参考资料，不读取个人数据库、不调用模型。</p>
                <label>检索词<input aria-label="MCP 检索词" value={mcpQuery} maxLength={120}
                  onChange={(e) => { setMcpQuery(e.target.value); setMcpResult(null); }} /></label>
                <button className="secondary" disabled={mcpBusy || mcpQuery.trim().length < 2}
                  onClick={async () => {
                    setMcpBusy(true); setMcpResult(null);
                    try { setMcpResult(await api("/mcp/reference/probe", "POST", { query: mcpQuery, limit: 3 })); }
                    catch (e) { notify((e as Error).message); }
                    finally { setMcpBusy(false); }
                  }}>{mcpBusy ? "正在检索…" : "检索本地 MCP 参考库"}</button>
                {mcpResult && <div className="message-notes">
                  <p>MCP 连接与检索成功 · {mcpResult.transport} · {mcpResult.protocol}</p>
                  {!mcpResult.hits.length && <p>没有匹配资料。</p>}
                  {mcpResult.hits.map((hit) => <div key={hit.id}>
                    <strong>{hit.title}</strong><p>{hit.excerpt}</p><small>来源：{hit.source}</small>
                  </div>)}
                </div>}
              </div>
              {run && (
                <div className="context-section">
                  <span className="eyebrow">BOUNDED & RESUMABLE</span>
                  <h3>有边界地协作</h3>
                  <div className="meter-label">
                    <span>模型调用</span>
                    <strong>
                      {run.calls} / {run.max_calls}
                    </strong>
                  </div>
                  <div className="meter">
                    <span
                      style={{ width: `${(run.calls / run.max_calls) * 100}%` }}
                    />
                  </div>
                  <div className="meter-label">
                    <span>已保存步骤</span>
                    <strong>
                      {run.cursor} / {run.steps.length}
                    </strong>
                  </div>
                  <div className="run-controls">
                    {run.owner_id === state.user.id && (
                      <>
                        {isActive(run) && (
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => control("pause")}
                          >
                            <Pause size={13} />
                            暂停
                          </button>
                        )}
                        {["paused", "failed", "exhausted"].includes(
                          run.status,
                        ) && (
                          <button
                            className="primary"
                            disabled={busy || run.calls >= 12}
                            onClick={() => control("resume")}
                          >
                            <Play size={13} />
                            继续
                          </button>
                        )}
                        {!["succeeded", "cancelled"].includes(run.status) && (
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() => control("cancel")}
                          >
                            <Square size={12} />
                            停止
                          </button>
                        )}
                      </>
                    )}
                  </div>
                  <p className="tiny muted">
                    已完成步骤会保存，恢复时继续后续工作。单次运行最多 12
                    次调用。
                  </p>
                </div>
              )}
              <div className="context-section">
                <label className="budget-select">
                  本次模型调用上限
                  <select
                    aria-label="模型调用上限"
                    value={maxCalls}
                    onChange={(e) => setMaxCalls(Number(e.target.value))}
                  >
                    {[1, 3, 5, 8, 12].map((n) => (
                      <option key={n} value={n}>
                        {n} 次
                      </option>
                    ))}
                  </select>
                </label>
                <p className="tiny muted">可设为 1 次测试预算耗尽与恢复。</p>
              </div>
            </>
          ) : run ? (
            <>
              <div className="context-section">
                <div className="meter-label">
                  <span>模式</span>
                  <strong>
                    {run.provider === "demo" ? "确定性演示" : "真实模型"}
                  </strong>
                </div>
                <div className="meter-label">
                  <span>累计 Token</span>
                  <strong>
                    {run.usage.input_tokens + run.usage.output_tokens}
                  </strong>
                </div>
                <p className="tiny muted">
                  显示步骤和工具结果，不展示隐藏思维链。
                </p>
              </div>
              <Trace run={run} />
            </>
          ) : (
            <p className="context-section muted">
              发送第一条消息后，这里会记录执行事件。
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

function ClockIcon() {
  return <span className="pending-circle" />;
}
