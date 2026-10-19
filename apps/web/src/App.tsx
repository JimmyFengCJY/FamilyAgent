import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowDown,
  ArrowRight,
  Bell,
  BookOpen,
  CalendarDays,
  Check,
  CheckCheck,
  ChevronRight,
  Circle,
  Clock3,
  Coffee,
  Compass,
  Heart,
  House,
  Leaf,
  ListTodo,
  LockKeyhole,
  LogOut,
  Menu,
  MessageCircle,
  MoreHorizontal,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sprout,
  Trash2,
  Users,
  Wallet,
  X,
} from "lucide-react";
import { api } from "./api";
import {
  Avatar,
  Badge,
  capabilityNames,
  Dialog,
  Empty,
  isActive,
  Logo,
  modeNames,
  statusNames,
  timeText,
  Trace,
  BudgetCalculator,
} from "./components";
import { CharacterForm, ConversationForm, MemoryForm, TaskForm } from "./forms";
import Workspace from "./Workspace";
import type {
  Budget,
  Character,
  Conversation,
  Mode,
  Run,
  State,
  Task,
  View,
} from "./types";

const nav: { id: View; label: string; icon: typeof House; section?: string }[] =
  [
    { id: "home", label: "今天", icon: House },
    { id: "chat", label: "伙伴聊天", icon: MessageCircle },
    { id: "meetings", label: "家庭会议", icon: Users },
    { id: "planning", label: "个人规划", icon: Compass },
    { id: "finance", label: "预算分析", icon: Wallet },
    { id: "tasks", label: "任务清单", icon: ListTodo },
    { id: "roles", label: "角色工作室", icon: Sparkles, section: "我的空间" },
    { id: "memory", label: "记忆空间", icon: BookOpen },
    { id: "runs", label: "运行观察", icon: Activity },
  ];
const viewMeta: Record<View, [string, string]> = {
  home: ["今天", "慢慢来，也是在向前走。"],
  chat: ["伙伴聊天", "让想法有回应，让心事有地方安放。"],
  meetings: ["家庭会议", "一起讨论，把每个人的声音放进决定里。"],
  planning: ["个人规划", "从一个小目标开始，走向你想要的生活。"],
  finance: ["预算分析", "多一点清晰，少一点对数字的焦虑。"],
  tasks: ["任务清单", "把想做的事，变成做得到的小步。"],
  roles: ["角色工作室", "不同的性格与视角，陪你想得更周全。"],
  memory: ["记忆空间", "被记住的内容，由你决定。"],
  runs: ["运行观察", "看见协作过程，让每一步都可追溯。"],
  settings: ["设置", "为你的空间，找到舒服的使用方式。"],
};

export default function App() {
  const [state, setState] = useState<State | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>("home");
  const [selected, setSelected] = useState<string | null>(null);
  const [dialog, setDialog] = useState<
    "conversation" | "character" | "task" | "memory" | null
  >(null);
  const [initialMode, setInitialMode] = useState<Mode>("meeting");
  const [editingCharacter, setEditingCharacter] = useState<
    Character | undefined
  >();
  const [toast, setToast] = useState("");
  const [search, setSearch] = useState("");
  const [mobileNav, setMobileNav] = useState(false);
  const [notifications, setNotifications] = useState(false);
  const [taskFilter, setTaskFilter] = useState("todo");
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [invite, setInvite] = useState("");
  const notify = useCallback((message: string) => setToast(message), []);
  const close = useCallback(() => setDialog(null), []);
  const refresh = useCallback(async () => {
    try {
      setState(await api<State>("/state"));
    } catch (e) {
      if ((e as Error).message === "请先登录") setState(null);
      else throw e;
    }
  }, []);
  useEffect(() => {
    void refresh()
      .catch((e) => notify(e.message))
      .finally(() => setLoading(false));
  }, [refresh, notify]);
  useEffect(() => {
    if (!state) return;
    const timer = setInterval(() => {
      void refresh().catch(() => {});
    }, 2500);
    return () => clearInterval(timer);
  }, [!!state, refresh]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 5000);
    return () => clearTimeout(timer);
  }, [toast]);
  function navigate(v: View) {
    setView(v);
    setSelected(null);
    setSearch("");
    setMobileNav(false);
  }
  function newConversation(mode: Mode) {
    setInitialMode(mode);
    setDialog("conversation");
  }
  function openConversation(c: Conversation) {
    setView(
      c.mode === "meeting"
        ? "meetings"
        : c.mode === "planning"
          ? "planning"
          : c.mode === "finance"
            ? "finance"
            : "chat",
    );
    setSelected(c.id);
  }
  async function mutate(path: string, method: string, data?: unknown) {
    try {
      await api(path, method, data);
      await refresh();
      return true;
    } catch (e) {
      notify((e as Error).message);
      return false;
    }
  }
  async function createConversation(data: unknown, prompt: string) {
    try {
      const c = await api<Conversation>("/conversations", "POST", data);
      close();
      openConversation(c);
      await refresh();
      if (prompt.trim()) {
        await api(`/conversations/${c.id}/messages`, "POST", {
          content: prompt,
          max_calls: 8,
        });
        await refresh();
      }
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function chatWith(c: Character) {
    const mode: Mode =
      c.capability === "host"
        ? "meeting"
        : c.capability === "planner"
          ? "planning"
          : c.capability === "support"
            ? "support"
            : c.capability === "finance"
              ? "finance"
              : "assistant";
    await createConversation(
      {
        title: `和${c.name}聊一聊`,
        mode,
        character_ids: mode === "meeting" ? ["planner", "finance"] : [c.id],
        participants: [],
      },
      "",
    );
  }
  async function budgetAnalyze(budget: Budget) {
    try {
      const c = await api<Conversation>("/conversations", "POST", {
        title: "我的本期预算分析",
        mode: "finance",
      });
      await api(`/conversations/${c.id}/messages`, "POST", {
        content:
          "请根据本次录入数据分析收支与结余，区分事实和假设，并给出可执行建议。",
        finance: budget,
      });
      openConversation(c);
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    }
  }
  if (loading)
    return (
      <div className="boot">
        <Logo />
        <span>正在为你准备空间…</span>
      </div>
    );
  if (!state)
    return (
      <>
        <Login
          onLogin={async () => {
            setSelected(null);
            setView("home");
            await refresh();
          }}
          notify={notify}
        />
        {toast && (
          <div role="alert" className="toast">
            {toast}
            <button aria-label="关闭提示" onClick={() => setToast("")}>
              <X size={15} />
            </button>
          </div>
        )}
      </>
    );
  const todo = state.tasks.filter((t) => t.status === "todo");
  const done = state.tasks.filter((t) => t.status === "done");
  const pending = state.runs.filter((r) => r.status === "waiting_approval");
  const active = state.runs.filter(isActive);
  const unread = state.notifications.filter((n) => !n.read);
  const date = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Hong_Kong",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
  const displayed = state.conversations.filter(
    (c) =>
      (view === "meetings"
        ? c.mode === "meeting"
        : view === "planning"
          ? c.mode === "planning"
          : view === "finance"
            ? c.mode === "finance"
            : ["assistant", "support"].includes(c.mode)) &&
      c.title.toLowerCase().includes(search.toLowerCase()),
  );
  const listMode: Mode =
    view === "meetings"
      ? "meeting"
      : view === "planning"
        ? "planning"
        : view === "finance"
          ? "finance"
          : "assistant";
  const recentRuns = [...state.runs].reverse();
  const traceRun =
    state.runs.find((r) => r.id === selectedRun) || recentRuns[0];
  return (
    <div className="app-shell">
      {mobileNav && (
        <div className="mobile-shade" onClick={() => setMobileNav(false)} />
      )}
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <button className="brand" onClick={() => navigate("home")}>
          <Logo />
          <span>
            Family<span className="brand-light">Agent</span>
            <small>生活有伴，想法有回应</small>
          </span>
        </button>
        <div className="space-switch">
          <span className="space-emblem">
            <House size={17} />
          </span>
          <div>
            <strong>
              {state.user.id.startsWith("demo-")
                ? "我们的生活空间"
                : `${state.user.name}的生活空间`}
            </strong>
            <small>{state.members.length} 位家庭成员</small>
          </div>
          <ChevronRight size={14} />
        </div>
        <nav aria-label="主导航">
          {nav.map((item) => (
            <div key={item.id}>
              {item.section && <p className="nav-section">{item.section}</p>}
              <button
                aria-label={item.label}
                className={`nav-item ${view === item.id ? "active" : ""}`}
                onClick={() => navigate(item.id)}
              >
                <item.icon size={18} strokeWidth={1.7} />
                <span>{item.label}</span>
                {item.id === "tasks" && todo.length > 0 && (
                  <span className="nav-count">{todo.length}</span>
                )}
                {item.id === "meetings" &&
                  pending.some((r) => r.mode === "meeting") && (
                    <span className="notification-dot" />
                  )}
                {item.id === "runs" && active.length > 0 && (
                  <span className="status-dot pulse" />
                )}
              </button>
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="gentle-note">
            <Sprout size={20} />
            <p>
              生活不用一次想明白。
              <br />
              <strong>我们一起，一件件来。</strong>
            </p>
          </div>
          <button
            className={`nav-item ${view === "settings" ? "active" : ""}`}
            onClick={() => navigate("settings")}
          >
            <Settings2 size={17} />
            <span>空间设置</span>
          </button>
          <button className="user-profile" onClick={() => navigate("settings")}>
            <span className="profile-avatar">{state.user.name[0]}</span>
            <span>
              <strong>{state.user.name}</strong>
              <small>个人与家庭空间</small>
            </span>
            <MoreHorizontal size={18} />
          </button>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <div className="inline">
            <button
              className="icon-button mobile-menu"
              aria-label="打开导航"
              onClick={() => setMobileNav(true)}
            >
              <Menu size={21} />
            </button>
            <span className="breadcrumb">
              我的空间 <ChevronRight size={12} />{" "}
              <strong>{selected ? "协作空间" : viewMeta[view][0]}</strong>
            </span>
          </div>
          <div className="topbar-right">
            <span
              className={`mode-indicator ${state.provider === "demo" ? "demo" : ""}`}
            >
              <span />
              {state.provider === "demo" ? "演示模式" : "真实模型"}
            </span>
            <div className="notification-wrap">
              <button
                className="icon-button bell"
                aria-label="通知"
                onClick={() => setNotifications(!notifications)}
              >
                <Bell size={19} />
                {unread.length > 0 && <i />}
              </button>
              {notifications && (
                <div className="notification-menu">
                  <h3>你的提醒</h3>
                  {state.notifications.length ? (
                    [...state.notifications]
                      .reverse()
                      .slice(0, 10)
                      .map((n) => (
                        <button
                          key={n.id}
                          onClick={async () => {
                            await mutate(
                              `/notifications/${n.id}/read`,
                              "POST",
                              {},
                            );
                          }}
                        >
                          <span className={n.read ? "" : "unread-dot"} />
                          <div>
                            <strong>{n.title}</strong>
                            <p>{n.content}</p>
                            <small>{timeText(n.created_at)}</small>
                          </div>
                        </button>
                      ))
                  ) : (
                    <p className="muted">
                      暂时没有提醒。为任务设置时间，到期后会出现在这里。
                    </p>
                  )}
                </div>
              )}
            </div>
            <span className="topbar-divider" />
            <span className="profile-avatar small-profile">
              {state.user.name[0]}
            </span>
          </div>
        </header>
        {selected ? (
          <Workspace
            key={selected}
            id={selected}
            state={state}
            refresh={refresh}
            back={() => setSelected(null)}
            notify={notify}
          />
        ) : (
          <div className={`page ${view === "home" ? "home-page" : ""}`}>
            {view === "home" ? (
              <>
                <div className="welcome-row">
                  <div>
                    <p className="eyebrow">YOUR EVERYDAY, A LITTLE LIGHTER</p>
                    <h1>
                      你好，{state.user.name}{" "}
                      <span className="greeting-leaf">✳</span>
                    </h1>
                    <p className="muted">
                      {date} <span className="date-dot">·</span>{" "}
                      {viewMeta.home[1]}
                    </p>
                  </div>
                  <button
                    className="secondary"
                    onClick={() => {
                      setDialog("task");
                    }}
                  >
                    <Plus size={16} />
                    添加待办
                  </button>
                </div>
                <section className="home-hero">
                  <div className="hero-copy">
                    <span className="hero-label">
                      <span /> 为你，也为你在乎的人
                    </span>
                    <h2>
                      把生活的大小事，
                      <br />
                      一起理清楚<span>。</span>
                    </h2>
                    <p>
                      让不同的视角相遇，让每一个决定更从容。
                      <br />
                      你的 AI 伙伴们，随时准备好倾听与同行。
                    </p>
                    <button
                      className="dark-button"
                      onClick={() => newConversation("meeting")}
                    >
                      开启一次家庭会议 <ArrowRight size={16} />
                    </button>
                  </div>
                  <div className="hero-art" aria-hidden="true">
                    <div className="orbit orbit-one" />
                    <div className="orbit orbit-two" />
                    <div className="orbit orbit-three" />
                    <div className="art-spark s1">✳</div>
                    <div className="art-spark s2">✦</div>
                    <div className="art-bubble b1">
                      <Avatar
                        character={state.characters.find(
                          (c) => c.id === "host",
                        )}
                      />
                      <span>让每个声音被听见</span>
                    </div>
                    <div className="art-bubble b2">
                      <Avatar
                        character={state.characters.find(
                          (c) => c.id === "planner",
                        )}
                      />
                    </div>
                    <div className="art-bubble b3">
                      <Avatar
                        character={state.characters.find(
                          (c) => c.id === "support",
                        )}
                      />
                    </div>
                    <div className="art-note">
                      <span>
                        <Check size={13} />
                      </span>
                      一个想法，一起实现。
                    </div>
                  </div>
                </section>
                <section className="quick-section">
                  <div className="section-heading">
                    <h2>今天，想一起做点什么？</h2>
                    <span className="tiny muted">从一个小小的开始</span>
                  </div>
                  <div className="quick-grid">
                    <QuickCard
                      icon={<Users size={22} />}
                      color="sage"
                      tag="一起商量"
                      title="开个家庭会议"
                      description="旅行、分工、重要决定，一起聊聊。"
                      onClick={() => newConversation("meeting")}
                    />
                    <QuickCard
                      icon={<Compass size={22} />}
                      color="lavender"
                      tag="往前一步"
                      title="制定个人计划"
                      description="把远处的目标，变成今天的一小步。"
                      onClick={() => newConversation("planning")}
                    />
                    <QuickCard
                      icon={<Heart size={22} />}
                      color="peach"
                      tag="说说心里话"
                      title="找个伙伴聊聊"
                      description="不用想好怎么说，想说什么都可以。"
                      onClick={() => newConversation("support")}
                    />
                  </div>
                </section>
                <div className="home-columns">
                  <section className="surface today-tasks">
                    <div className="section-heading">
                      <h2>
                        <span className="heading-dot sage" />
                        留给今天的小事 <Badge>{todo.length}</Badge>
                      </h2>
                      <button
                        className="text-button"
                        onClick={() => navigate("tasks")}
                      >
                        全部任务
                        <ChevronRight size={14} />
                      </button>
                    </div>
                    {todo.length ? (
                      todo
                        .slice(0, 4)
                        .map((t) => (
                          <TaskRow
                            key={t.id}
                            task={t}
                            toggle={() =>
                              mutate(`/tasks/${t.id}`, "PATCH", {
                                status: "done",
                              })
                            }
                          />
                        ))
                    ) : (
                      <div className="small-empty">
                        <span>
                          <Coffee size={26} />
                        </span>
                        <h3>留一点空间给今天</h3>
                        <p>
                          还没有待办。可以添加一件小事，
                          <br />
                          也可以让伙伴帮你整理计划。
                        </p>
                        <button
                          className="text-button"
                          onClick={() => setDialog("task")}
                        >
                          <Plus size={14} />
                          添加第一件事
                        </button>
                      </div>
                    )}
                    <button
                      className="add-task-inline"
                      onClick={() => setDialog("task")}
                    >
                      <Plus size={16} />
                      再添一件小事
                    </button>
                  </section>
                  <section className="surface recent-space">
                    <div className="section-heading">
                      <h2>
                        <span className="heading-dot lavender" />
                        继续上次的话题
                      </h2>
                      <button
                        className="text-button"
                        onClick={() => navigate("meetings")}
                      >
                        查看空间
                        <ChevronRight size={14} />
                      </button>
                    </div>
                    {state.conversations.length ? (
                      state.conversations.slice(0, 3).map((c) => (
                        <button
                          className="recent-item"
                          key={c.id}
                          onClick={() => openConversation(c)}
                        >
                          <span
                            className={`recent-icon ${c.mode === "meeting" ? "sage" : c.mode === "support" ? "peach" : "lavender"}`}
                          >
                            {c.mode === "meeting" ? (
                              <Users size={19} />
                            ) : (
                              <MessageCircle size={19} />
                            )}
                          </span>
                          <span>
                            <strong>{c.title}</strong>
                            <small>
                              {modeNames[c.mode]} ·{" "}
                              {statusNames[c.status] || "进行中"}
                            </small>
                          </span>
                          <ChevronRight size={15} />
                        </button>
                      ))
                    ) : (
                      <div className="small-empty">
                        <span>
                          <MessageCircle size={26} />
                        </span>
                        <h3>每次交流，都从这里延续</h3>
                        <p>
                          创建一次聊天或会议，
                          <br />
                          之后随时回来继续。
                        </p>
                        <button
                          className="text-button"
                          onClick={() => newConversation("assistant")}
                        >
                          开启第一段对话
                          <ArrowRight size={14} />
                        </button>
                      </div>
                    )}
                  </section>
                </div>
                <section className="companions-section">
                  <div className="section-heading">
                    <h2>你的伙伴，各有所长</h2>
                    <button
                      className="text-button"
                      onClick={() => navigate("roles")}
                    >
                      认识所有伙伴
                      <ArrowRight size={14} />
                    </button>
                  </div>
                  <div className="companion-strip">
                    {state.characters.slice(0, 5).map((c) => (
                      <button key={c.id} onClick={() => chatWith(c)}>
                        <Avatar character={c} />
                        <span>
                          <strong>{c.name}</strong>
                          <small>{capabilityNames[c.capability]}</small>
                        </span>
                        <ChevronRight size={14} />
                      </button>
                    ))}
                  </div>
                </section>
                <p className="page-footer">
                  <Leaf size={13} />
                  陪你做决定，也尊重你的每一个决定。
                </p>
              </>
            ) : (
              <>
                <div className="page-heading">
                  <div>
                    <p className="eyebrow">
                      {view === "roles"
                        ? "DIFFERENT MINDS, ONE SHARED SPACE"
                        : view === "runs"
                          ? "VISIBLE, BOUNDED, RESUMABLE"
                          : "MAKE ROOM FOR WHAT MATTERS"}
                    </p>
                    <h1>{viewMeta[view][0]}</h1>
                    <p className="muted">{viewMeta[view][1]}</p>
                  </div>
                  {["chat", "meetings", "planning", "finance"].includes(
                    view,
                  ) && (
                    <button
                      className="primary"
                      onClick={() => newConversation(listMode)}
                    >
                      <Plus size={16} />
                      {view === "meetings" ? "发起会议" : "创建空间"}
                    </button>
                  )}
                  {view === "tasks" && (
                    <button
                      className="primary"
                      onClick={() => setDialog("task")}
                    >
                      <Plus size={16} />
                      添加任务
                    </button>
                  )}
                  {view === "roles" && (
                    <button
                      className="primary"
                      onClick={() => {
                        setEditingCharacter(undefined);
                        setDialog("character");
                      }}
                    >
                      <Plus size={16} />
                      创造新伙伴
                    </button>
                  )}
                  {view === "memory" && (
                    <button
                      className="primary"
                      onClick={() => setDialog("memory")}
                    >
                      <Plus size={16} />
                      保存一条记忆
                    </button>
                  )}
                </div>
                {view === "planning" && state.plans.length > 0 && (
                  <section className="plan-section">
                    <div className="section-heading">
                      <h2>已确认的成长计划</h2>
                      <span className="tiny muted">进度由任务完成情况计算</span>
                    </div>
                    <div className="plan-grid">
                      {state.plans.map((plan) => {
                        const tasks = state.tasks.filter(
                          (t) => t.source_run === plan.source_run,
                        );
                        const done = tasks.filter(
                          (t) => t.status === "done",
                        ).length;
                        return (
                          <article className="surface plan-card" key={plan.id}>
                            <div className="inline">
                              <Compass size={18} />
                              <Badge color="lavender">
                                计划 v{plan.version}
                              </Badge>
                              <span className="tiny muted">
                                {done}/{tasks.length} 项完成
                              </span>
                            </div>
                            <h3>{plan.title}</h3>
                            <div className="meter">
                              <span
                                style={{
                                  width: `${tasks.length ? (done / tasks.length) * 100 : 0}%`,
                                }}
                              />
                            </div>
                            <ol>
                              {plan.milestones.map((m, i) => (
                                <li key={i}>{m}</li>
                              ))}
                            </ol>
                            <button
                              className="text-button"
                              onClick={() => {
                                setSelected(plan.conversation_id);
                              }}
                            >
                              继续讨论与复盘
                              <ArrowRight size={14} />
                            </button>
                          </article>
                        );
                      })}
                    </div>
                  </section>
                )}
                {["chat", "meetings", "planning", "finance"].includes(view) && (
                  <>
                    {view === "finance" && (
                      <BudgetCalculator
                        onAnalyze={budgetAnalyze}
                        notify={notify}
                      />
                    )}
                    <div className="list-toolbar">
                      <span>
                        {displayed.length} 个
                        {view === "meetings" ? "会议" : "空间"}
                      </span>
                      <label className="search-input">
                        <Search size={15} />
                        <input
                          aria-label="搜索空间"
                          placeholder="搜索话题…"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                        />
                      </label>
                    </div>
                    {displayed.length ? (
                      <div className="conversation-grid">
                        {displayed.map((c) => (
                          <button
                            className="conversation-card"
                            key={c.id}
                            onClick={() => openConversation(c)}
                          >
                            <div className="inline">
                              <Badge
                                color={
                                  c.mode === "meeting"
                                    ? "sage"
                                    : c.mode === "support"
                                      ? "peach"
                                      : "lavender"
                                }
                              >
                                {modeNames[c.mode]}
                              </Badge>
                              <span className="tiny muted">
                                {statusNames[c.status] || c.status}
                              </span>
                            </div>
                            <h3>{c.title}</h3>
                            <div className="card-companions">
                              <div className="avatar-stack">
                                {c.character_snapshots.map((a) => (
                                  <Avatar small key={a.id} character={a} />
                                ))}
                              </div>
                              <span>
                                {c.character_snapshots
                                  .map((a) => a.name)
                                  .join("、")}
                              </span>
                            </div>
                            <div className="card-footer">
                              <span>
                                {c.scope === "family" ? (
                                  <Users size={12} />
                                ) : (
                                  <LockKeyhole size={12} />
                                )}{" "}
                                {c.scope === "family"
                                  ? `${c.participants.length} 位成员可见`
                                  : "仅自己可见"}
                              </span>
                              <ChevronRight size={16} />
                            </div>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <Empty
                        icon={
                          view === "meetings" ? (
                            <Users size={31} />
                          ) : view === "planning" ? (
                            <Compass size={31} />
                          ) : (
                            <MessageCircle size={31} />
                          )
                        }
                        title={
                          search
                            ? "没有找到这个话题"
                            : view === "meetings"
                              ? "把大家的想法，放在一起"
                              : "给想法留一个空间"
                        }
                        description={
                          search
                            ? "换一个关键词试试。"
                            : "从一个具体的问题开始，邀请伙伴陪你一起想。"
                        }
                        action={
                          !search && (
                            <button
                              className="secondary"
                              onClick={() => newConversation(listMode)}
                            >
                              <Plus size={16} />
                              创建第一个空间
                            </button>
                          )
                        }
                      />
                    )}
                  </>
                )}
                {view === "tasks" && (
                  <>
                    <div className="stats-grid">
                      <Stat
                        label="待完成"
                        value={todo.length}
                        icon={<ListTodo size={20} />}
                      />
                      <Stat
                        label="已完成"
                        value={done.length}
                        icon={<CheckCheck size={20} />}
                      />
                      <Stat
                        label="已设置提醒"
                        value={todo.filter((t) => t.due_at).length}
                        icon={<Bell size={20} />}
                      />
                    </div>
                    <div className="filter-tabs">
                      {[
                        ["todo", "待完成"],
                        ["done", "已完成"],
                        ["dismissed", "已归档"],
                      ].map(([key, label]) => (
                        <button
                          className={taskFilter === key ? "active" : ""}
                          key={key}
                          onClick={() => setTaskFilter(key)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    <section className="surface task-list">
                      {state.tasks.filter((t) => t.status === taskFilter)
                        .length ? (
                        state.tasks
                          .filter((t) => t.status === taskFilter)
                          .map((t) => (
                            <TaskRow
                              key={t.id}
                              task={t}
                              toggle={() =>
                                mutate(`/tasks/${t.id}`, "PATCH", {
                                  status: t.status === "done" ? "todo" : "done",
                                })
                              }
                              archive={() =>
                                mutate(`/tasks/${t.id}`, "PATCH", {
                                  status: "dismissed",
                                })
                              }
                            />
                          ))
                      ) : (
                        <Empty
                          title={
                            taskFilter === "done"
                              ? "进步会慢慢出现在这里"
                              : "这里暂时是空的"
                          }
                          description="添加一件具体的小事，或者从讨论草案中生成任务。"
                        />
                      )}
                    </section>
                  </>
                )}
                {view === "roles" && (
                  <>
                    <div className="info-strip">
                      <Sparkles size={17} />
                      <span>
                        人格决定如何交流，专业能力决定做什么。每位伙伴的工具权限都由系统控制。
                      </span>
                    </div>
                    <div className="role-grid">
                      {state.characters.map((c) => (
                        <article className="role-card" key={c.id}>
                          <div className={`role-art ${c.color}`}>
                            <span className="role-orbit" />
                            <Avatar character={c} />
                            <span className="role-star">✦</span>
                            <Badge>
                              {c.builtin
                                ? "预设伙伴"
                                : `自定义 · v${c.version}`}
                            </Badge>
                          </div>
                          <div className="role-info">
                            <div className="inline">
                              <h2>{c.name}</h2>
                              <Badge>{capabilityNames[c.capability]}</Badge>
                            </div>
                            <p>{c.description || "一个由你定义的 AI 伙伴"}</p>
                            <blockquote>{c.tone}</blockquote>
                            <div className="button-row">
                              <button
                                className="secondary"
                                onClick={() => chatWith(c)}
                              >
                                <MessageCircle size={14} />和 TA 聊聊
                              </button>
                              <button
                                className="text-button"
                                onClick={() => {
                                  setEditingCharacter(c);
                                  setDialog("character");
                                }}
                              >
                                {c.builtin ? "复制并定制" : "编辑角色"}
                              </button>
                            </div>
                          </div>
                        </article>
                      ))}
                    </div>
                  </>
                )}
                {view === "memory" && (
                  <>
                    <div className="privacy-banner">
                      <span>
                        <LockKeyhole size={24} />
                      </span>
                      <div>
                        <h3>记忆属于你，边界也由你决定</h3>
                        <p>
                          只有你主动保存的内容才进入长期记忆。家庭会议与情绪支持不会读取这些记录。
                        </p>
                      </div>
                      <Badge color="sage">个人私密</Badge>
                    </div>
                    {state.memories.length ? (
                      <div className="memory-grid">
                        {state.memories.map((m) => (
                          <article className="surface memory-card" key={m.id}>
                            <BookOpen size={19} />
                            <p>{m.content}</p>
                            <div>
                              <span>{timeText(m.created_at)} · 主动保存</span>
                              <button
                                className="icon-button"
                                aria-label="删除这条记忆"
                                onClick={async () => {
                                  if (
                                    await mutate(`/memories/${m.id}`, "DELETE")
                                  )
                                    notify(
                                      "已删除，未来会话不再读取；历史发言不会因此撤回",
                                    );
                                }}
                              >
                                <Trash2 size={15} />
                              </button>
                            </div>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <Empty
                        icon={<BookOpen size={30} />}
                        title="可以记住，也可以不记住"
                        description="保存一条偏好或长期目标，让未来的个人会话少一些重复介绍。"
                        action={
                          <button
                            className="secondary"
                            onClick={() => setDialog("memory")}
                          >
                            保存第一条记忆
                          </button>
                        }
                      />
                    )}
                    <p className="tiny muted">
                      当前实现读取最近 6
                      条授权记忆，尚未接入向量检索。删除不自动修改已生成的会话内容。
                    </p>
                  </>
                )}
                {view === "runs" && (
                  <>
                    <div className="stats-grid">
                      <Stat
                        label="累计运行"
                        value={state.runs.length}
                        icon={<Activity size={20} />}
                      />
                      <Stat
                        label="正在运行"
                        value={active.length}
                        icon={<Sparkles size={20} />}
                      />
                      <Stat
                        label="等待确认"
                        value={pending.length}
                        icon={<Clock3 size={20} />}
                      />
                    </div>
                    {state.runs.length ? (
                      <div className="observer-layout">
                        <div className="surface run-list">
                          {recentRuns.map((r) => (
                            <button
                              key={r.id}
                              className={
                                traceRun?.id === r.id ? "selected" : ""
                              }
                              onClick={() => setSelectedRun(r.id)}
                            >
                              <div>
                                <strong>
                                  {
                                    state.conversations.find(
                                      (c) => c.id === r.conversation_id,
                                    )?.title
                                  }
                                </strong>
                                <small>
                                  {modeNames[r.mode]} · {r.calls}/{r.max_calls}{" "}
                                  次调用 · {timeText(r.created_at)}
                                </small>
                              </div>
                              <Badge color={isActive(r) ? "sage" : ""}>
                                {statusNames[r.status]}
                              </Badge>
                            </button>
                          ))}
                        </div>
                        <div className="surface observer-detail">
                          {traceRun && (
                            <>
                              <div className="observer-summary">
                                <div>
                                  <span className="eyebrow">RUN DETAILS</span>
                                  <h3>每一步，都有记录</h3>
                                </div>
                                <button
                                  className="text-button"
                                  onClick={() => {
                                    const c = state.conversations.find(
                                      (c) => c.id === traceRun.conversation_id,
                                    );
                                    if (c) openConversation(c);
                                  }}
                                >
                                  打开会话
                                  <ArrowRight size={14} />
                                </button>
                              </div>
                              <p className="tiny muted mono">{traceRun.id}</p>
                              <div className="inline observer-badges">
                                <Badge>
                                  {traceRun.provider === "demo"
                                    ? "演示模式"
                                    : "真实模型"}
                                </Badge>
                                <Badge>
                                  检查点 {traceRun.cursor}/
                                  {traceRun.steps.length}
                                </Badge>
                                <Badge>
                                  {traceRun.usage.input_tokens +
                                    traceRun.usage.output_tokens}{" "}
                                  Token
                                </Badge>
                              </div>
                              <Trace run={traceRun} />
                            </>
                          )}
                        </div>
                      </div>
                    ) : (
                      <Empty
                        icon={<Activity size={30} />}
                        title="协作的过程，也值得被看见"
                        description="发起一次聊天或会议后，可以在这里检查角色发言、工具执行、审批与恢复。"
                      />
                    )}
                  </>
                )}
                {view === "settings" && (
                  <div className="settings-grid">
                    <section className="surface settings-card">
                      <span className="eyebrow">YOUR ACCOUNT</span>
                      <h2>账号与家庭</h2>
                      <div className="account-large">
                        <span className="profile-avatar">
                          {state.user.name[0]}
                        </span>
                        <div>
                          <strong>{state.user.name}</strong>
                          <p>{state.user.email}</p>
                        </div>
                      </div>
                      <h3>家庭成员</h3>
                      {state.members.map((m) => (
                        <div className="setting-member" key={m.id}>
                          <span className="human-avatar">{m.name[0]}</span>
                          <strong>{m.name}</strong>
                          <span className="tiny muted">
                            {m.id === state.user.id
                              ? "当前账号"
                              : "独立账号 · 私密数据隔离"}
                          </span>
                        </div>
                      ))}
                      <button
                        className="secondary"
                        onClick={async () => {
                          try {
                            const v = await api<{
                              token: string;
                              expires_in: string;
                            }>("/invitations", "POST", {});
                            setInvite(v.token);
                          } catch (e) {
                            notify((e as Error).message);
                          }
                        }}
                      >
                        <Plus size={15} />
                        生成家庭邀请码
                      </button>
                      {invite && (
                        <div className="invite-result">
                          <code>{invite}</code>
                          <p className="tiny muted">
                            注册时填写；24 小时有效，只能使用一次。
                          </p>
                        </div>
                      )}
                      <button
                        className="text-button logout"
                        onClick={async () => {
                          await api("/auth/logout", "POST", {});
                          setState(null);
                          setSelected(null);
                        }}
                      >
                        <LogOut size={15} />
                        退出登录
                      </button>
                    </section>
                    <section className="surface settings-card">
                      <span className="eyebrow">MODEL & RUNTIME</span>
                      <h2>运行方式</h2>
                      <div className="setting-line">
                        <span>当前模型模式</span>
                        <Badge color="lavender">
                          {state.provider === "demo"
                            ? "确定性演示"
                            : "真实模型"}
                        </Badge>
                      </div>
                      <p className="muted">
                        {state.provider === "demo"
                          ? "回复使用明确标记的示例，适合测试交互、权限与工作流。账号、任务、记忆和运行记录真实保存在本地数据库。"
                          : "模型通过服务端连接。浏览器不会持有 API 密钥。"}
                      </p>
                      <div className="setting-line">
                        <span>数据存储</span>
                        <strong>本地 SQLite</strong>
                      </div>
                      <div className="setting-line">
                        <span>提醒方式</span>
                        <strong>应用内通知</strong>
                      </div>
                      <div className="setting-line">
                        <span>单次调用硬上限</span>
                        <strong>12 次</strong>
                      </div>
                      <div className="setting-line">
                        <span>界面时间</span>
                        <strong>Asia/Hong_Kong</strong>
                      </div>
                      <p className="tiny muted">
                        真实模型可通过服务端环境变量启用，配置方式见项目
                        README。当前未连接外部日历、银行、邮件或向量数据库。
                      </p>
                      {state.demo_login && (
                        <>
                          <h3>测试另一位家庭成员</h3>
                          <p className="tiny muted">
                            体验账号用于本机功能测试，切换后可验证私密空间隔离和会议共同确认。
                          </p>
                          <div className="button-row">
                            {[
                              ["demo-jimmy", "Jimmy"],
                              ["demo-lin", "林悦"],
                            ].map(([id, name]) => (
                              <button
                                key={id}
                                className="secondary"
                                disabled={state.user.id === id}
                                onClick={async () => {
                                  try {
                                    await api(
                                      `/auth/demo?account=${id}`,
                                      "POST",
                                      {},
                                    );
                                    setSelected(null);
                                    setSelectedRun(null);
                                    setView("home");
                                    await refresh();
                                    notify(`已切换至 ${name}`);
                                  } catch (e) {
                                    notify((e as Error).message);
                                  }
                                }}
                              >
                                切换到 {name}
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                    </section>
                    <section className="surface settings-card feature-status">
                      <span className="eyebrow">THIS FIRST CHAPTER</span>
                      <h2>这一版，已经可以做什么</h2>
                      {[
                        "多角色会议与成员分别确认",
                        "私密聊天、规划和情绪支持",
                        "预算精确计算与解释草案",
                        "任务、应用内提醒、可管理记忆",
                        "角色定制与版本快照",
                        "运行预算、暂停、恢复、事件记录",
                      ].map((s) => (
                        <p key={s}>
                          <Check size={15} />
                          {s}
                        </p>
                      ))}
                      <p className="tiny muted">
                        暂未包含：语音、文件导入、外部工具写入、向量
                        RAG、生产级部署和完整危机评审。这些功能保留在后续迭代中。
                      </p>
                    </section>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </main>
      {toast && (
        <div role="alert" className="toast">
          {toast}
          <button aria-label="关闭提示" onClick={() => setToast("")}>
            <X size={15} />
          </button>
        </div>
      )}
      {dialog && (
        <Dialog
          close={close}
          title={
            dialog === "conversation"
              ? "开启一个新的空间"
              : dialog === "character"
                ? editingCharacter && !editingCharacter.builtin
                  ? "调整你的伙伴"
                  : "创造一位新伙伴"
                : dialog === "task"
                  ? "给今天，添一件小事"
                  : "留下一条想被记住的事"
          }
          subtitle={
            dialog === "conversation"
              ? "选择参与者，说说你想一起解决的问题。"
              : undefined
          }
        >
          {dialog === "conversation" && (
            <ConversationForm
              state={state}
              initialMode={initialMode}
              submit={createConversation}
            />
          )}{" "}
          {dialog === "character" && (
            <CharacterForm
              character={editingCharacter}
              submit={async (data, key) => {
                if (
                  await mutate(
                    key ? `/characters/${key}` : "/characters",
                    key ? "PUT" : "POST",
                    data,
                  )
                ) {
                  close();
                  notify("角色已保存，已有会话继续使用原版本");
                }
              }}
            />
          )}{" "}
          {dialog === "task" && (
            <TaskForm
              submit={async (data) => {
                if (await mutate("/tasks", "POST", data)) {
                  close();
                  notify("任务已保存");
                }
              }}
            />
          )}{" "}
          {dialog === "memory" && (
            <MemoryForm
              submit={async (data) => {
                if (await mutate("/memories", "POST", data)) {
                  close();
                  notify("记忆已保存，仅自己可见");
                }
              }}
            />
          )}
        </Dialog>
      )}
    </div>
  );
}

function QuickCard({
  icon,
  color,
  tag,
  title,
  description,
  onClick,
}: {
  icon: ReactNode;
  color: string;
  tag: string;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button className={`quick-card ${color}`} onClick={onClick}>
      <div className="quick-top">
        <span className="quick-icon">{icon}</span>
        <span>{tag}</span>
        <ArrowRight size={17} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
    </button>
  );
}
function Stat({
  label,
  value,
  icon,
}: {
  label: string;
  value: number;
  icon: ReactNode;
}) {
  return (
    <div className="surface stat-card">
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <span>{icon}</span>
    </div>
  );
}
function TaskRow({
  task,
  toggle,
  archive,
}: {
  task: Task;
  toggle: () => void;
  archive?: () => void;
}) {
  return (
    <div className={`task-row ${task.status === "done" ? "done" : ""}`}>
      <button
        aria-label={
          task.status === "done"
            ? `重新打开：${task.title}`
            : `完成：${task.title}`
        }
        className="task-checkbox"
        onClick={toggle}
      >
        {task.status === "done" && <Check size={14} />}
      </button>
      <div>
        <strong>{task.title}</strong>
        <small>
          {task.due_at ? (
            <>
              <Clock3 size={11} />
              {timeText(task.due_at)}
            </>
          ) : task.source_run ? (
            "来自已确认的行动草案"
          ) : (
            "个人待办"
          )}
        </small>
      </div>
      {archive && task.status !== "dismissed" && (
        <button
          className="icon-button task-archive"
          aria-label={`归档：${task.title}`}
          onClick={archive}
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}

function Login({
  onLogin,
  notify,
}: {
  onLogin: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [invite, setInvite] = useState("");
  useEffect(() => {
    void api<{ demo_login: boolean }>("/health")
      .then((h) => setDemo(h.demo_login))
      .catch(() => notify("后端尚未连接，请按 README 启动服务"));
  }, [notify]);
  async function submit(path: string, body: unknown) {
    setBusy(true);
    try {
      await api(path, "POST", body);
      await onLogin();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <section className="login-story">
        <div className="brand">
          <Logo />
          <span>
            Family<span className="brand-light">Agent</span>
          </span>
        </div>
        <div className="login-story-copy">
          <span className="eyebrow">A LITTLE COMPANY. A CLEARER LIFE.</span>
          <h1>
            生活的答案，
            <br />
            可以一起慢慢找<span>。</span>
          </h1>
          <p>
            有时需要一个清晰的计划，
            <br />
            有时只是想被认真听见。
            <br />
            让不同的伙伴，陪你走过不同的日常。
          </p>
          <div className="login-avatars">
            {[
              { name: "知和", color: "sage" },
              { name: "远山", color: "lavender" },
              { name: "小暖", color: "peach" },
              { name: "明理", color: "blue" },
            ].map((c) => (
              <Avatar key={c.name} character={c} />
            ))}
            <span>各有所长，一起同行。</span>
          </div>
        </div>
        <span className="login-footer">
          <ShieldCheck size={15} />
          个人的心事留在个人空间，家庭的决定一起确认。
        </span>
        <div className="login-circle circle-1" />
        <div className="login-circle circle-2" />
      </section>
      <section className="login-form-area">
        <div className="login-form">
          <span className="eyebrow">WELCOME TO YOUR SPACE</span>
          <h2>{register ? "创建你的生活空间" : "欢迎，回到这里"}</h2>
          <p className="muted">
            {register
              ? "先为自己留一个空间，再邀请在乎的人。"
              : "你的想法、计划和伙伴，都可以在这里延续。"}
          </p>
          {demo && (
            <>
              <button
                className="demo-button"
                disabled={busy}
                onClick={() => submit("/auth/demo", {})}
              >
                <Sparkles size={18} />
                免配置，体验完整工作流
                <ArrowRight size={17} />
              </button>
              <p className="tiny muted demo-description">
                本机体验账号 · 示例回复明确标记 · 数据真实保存
              </p>
              <div className="form-divider">
                <span>或使用独立账号</span>
              </div>
            </>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit(register ? "/auth/register" : "/auth/login", {
                email,
                password,
                ...(register ? { name, invite: invite || null } : {}),
              });
            }}
          >
            {register && (
              <label>
                你的称呼
                <input
                  aria-label="你的称呼"
                  required
                  maxLength={30}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                />
              </label>
            )}
            <label>
              邮箱
              <input
                aria-label="邮箱"
                required
                type="email"
                maxLength={120}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                placeholder="you@example.com"
              />
            </label>
            <label>
              密码
              <input
                aria-label="密码"
                required
                type="password"
                minLength={8}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={register ? "new-password" : "current-password"}
                placeholder="至少 8 位"
              />
            </label>
            {register && (
              <label>
                家庭邀请码 <span className="muted">可选</span>
                <input
                  aria-label="家庭邀请码"
                  value={invite}
                  onChange={(e) => setInvite(e.target.value)}
                  placeholder="不填写则创建自己的家庭"
                />
              </label>
            )}
            <button
              className="primary login-submit"
              type="submit"
              disabled={busy}
            >
              {busy ? "正在准备…" : register ? "创建空间" : "进入我的空间"}
              <ArrowRight size={16} />
            </button>
          </form>
          <p className="login-switch">
            {register ? "已经有账号？" : "第一次来这里？"}
            <button
              className="text-button"
              disabled={busy}
              onClick={() => setRegister(!register)}
            >
              {register ? "去登录" : "创建一个账号"}
            </button>
          </p>
        </div>
      </section>
    </div>
  );
}
