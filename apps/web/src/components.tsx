import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Check,
  ChevronRight,
  Clock3,
  Leaf,
  Loader2,
  Plus,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { api } from "./api";
import type { Budget, BudgetResult, Character, Run, RunEvent } from "./types";

export const modeNames: Record<string, string> = {
  meeting: "家庭会议",
  planning: "个人规划",
  support: "心事闲聊",
  finance: "预算分析",
  assistant: "日常助手",
};
export const capabilityNames: Record<string, string> = {
  host: "会议主持",
  planner: "成长规划",
  support: "情绪支持",
  finance: "财务分析",
  assistant: "日常事务",
};
export const statusNames: Record<string, string> = {
  queued: "准备中",
  running: "进行中",
  paused: "已暂停",
  waiting_approval: "待确认",
  waiting_user: "待重新讨论",
  succeeded: "已完成",
  failed: "运行失败",
  cancelled: "已停止",
  exhausted: "预算用完",
  draft: "未开始",
  active: "进行中",
  discussing: "讨论中",
  decision_pending: "待成员确认",
  proposal_pending: "待确认",
  concluded: "已达成决议",
};
export const isActive = (run?: Run) =>
  !!run && ["queued", "running"].includes(run.status);
export const timeText = (date: string) =>
  new Date(date).toLocaleString("zh-CN", {
    timeZone: "Asia/Hong_Kong",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

export function Avatar({
  character,
  small = false,
}: {
  character?: Partial<Character>;
  small?: boolean;
}) {
  return (
    <span
      className={`avatar ${character?.color || "sage"} ${small ? "small" : ""}`}
      aria-hidden="true"
    >
      <span>{character?.initial || character?.name?.[0] || "和"}</span>
      <i />
    </span>
  );
}

export function Logo() {
  return (
    <span className="brand-mark">
      <Leaf size={23} strokeWidth={1.8} />
    </span>
  );
}
export function Badge({
  children,
  color = "",
}: {
  children: ReactNode;
  color?: string;
}) {
  return <span className={`badge ${color}`}>{children}</span>;
}
export function Empty({
  icon = <Leaf size={30} />,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function Dialog({
  title,
  subtitle,
  close,
  children,
}: {
  title: string;
  subtitle?: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = () =>
      Array.from(
        ref.current?.querySelectorAll<HTMLElement>(
          'button, input, textarea, select, [tabindex="0"]',
        ) || [],
      ).filter((el) => !el.hasAttribute("disabled"));
    focusable()[1]?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab") {
        const items = focusable();
        const first = items[0];
        const last = items.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        }
        if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handler);
      document.body.style.overflow = "";
      previous?.focus();
    };
  }, [close]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={ref}
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
      >
        <button
          className="icon-button modal-close"
          aria-label="关闭"
          onClick={close}
        >
          <X size={20} />
        </button>
        <span className="eyebrow">FAMILYAGENT SPACE</span>
        <h2 id="dialog-title">{title}</h2>
        {subtitle && <p className="muted">{subtitle}</p>}
        {children}
      </div>
    </div>
  );
}

const eventNames: Record<string, string> = {
  "run.queued": "任务进入持久队列",
  "agent.started": "角色开始工作",
  "agent.completed": "角色完成发言",
  "checkpoint.saved": "检查点已保存",
  "approval.required": "等待用户确认草案",
  "run.waiting_approval": "讨论完成，等待确认",
  "run.completed": "运行完成",
  "tool.started": "工具开始执行",
  "tool.completed": "工具执行完成",
  "tool.denied": "工具权限检查拒绝",
  "run.exhausted": "调用预算已耗尽",
  "run.failed": "运行失败",
  "run.pause": "用户暂停",
  "run.resume": "从检查点继续",
  "run.cancel": "用户停止",
  "run.cancelled": "草案已被替代",
  "run.recovered": "恢复中断的运行",
  "decision.accepted": "成员确认草案",
  "decision.declined": "成员提出修改",
  "step.retry": "步骤有限重试",
};
export function Trace({
  run,
  compact = false,
}: {
  run: Run;
  compact?: boolean;
}) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    const load = () =>
      api<RunEvent[]>(`/runs/${run.id}/events`)
        .then((v) => {
          if (!disposed) {
            setEvents(v);
            setError("");
          }
        })
        .catch((e) => {
          if (!disposed) setError(e.message);
        });
    void load();
    const timer = window.setInterval(load, isActive(run) ? 900 : 4000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [run.id, run.status]);
  const visible = compact ? events.slice(-6) : events;
  return (
    <div className={`trace ${compact ? "compact" : ""}`}>
      <div className="trace-heading">
        <Activity size={16} />
        <span>运行轨迹</span>
        <Badge>{events.length} 个事件</Badge>
      </div>
      {error && <p className="form-error">{error}</p>}
      {visible.map((e) => (
        <div className="trace-event" key={e.id}>
          <span
            className={`trace-dot ${e.type.includes("failed") || e.type.includes("denied") ? "danger" : ""}`}
          />
          <div>
            <strong>{eventNames[e.type] || e.type}</strong>
            <p>
              {[
                e.data.name,
                e.data.by,
                e.data.stage === "summary" ? "汇总阶段" : "",
                e.data.step ? `步骤 ${e.data.step}/${e.data.total}` : "",
                e.data.reason,
              ]
                .filter(Boolean)
                .map(String)
                .join(" · ")}
            </p>
            <time>
              {new Date(e.created_at).toLocaleTimeString("zh-CN", {
                timeZone: "Asia/Hong_Kong",
              })}
            </time>
            {!compact && (
              <details>
                <summary>查看事件数据</summary>
                <pre>{JSON.stringify(e.data, null, 2)}</pre>
              </details>
            )}
          </div>
        </div>
      ))}
      {!events.length && !error && <p className="muted">等待首个事件…</p>}
    </div>
  );
}

export function BudgetCalculator({
  onAnalyze,
  notify,
}: {
  onAnalyze: (budget: Budget) => Promise<void>;
  notify: (message: string) => void;
}) {
  const [income, setIncome] = useState("20000");
  const [currency, setCurrency] = useState("CNY");
  const [items, setItems] = useState([
    { name: "居住与水电", amount: "6500" },
    { name: "餐饮与日用", amount: "3500" },
    { name: "交通与其他", amount: "2000" },
  ]);
  const [result, setResult] = useState<BudgetResult | null>(null);
  const [busy, setBusy] = useState(false);
  function change(index: number, field: "name" | "amount", value: string) {
    setItems(
      items.map((item, i) =>
        i === index ? { ...item, [field]: value } : item,
      ),
    );
    setResult(null);
  }
  const payload = { income, currency, items };
  async function calculate() {
    setBusy(true);
    try {
      setResult(await api<BudgetResult>("/finance/calculate", "POST", payload));
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="budget-card">
      <div className="section-heading">
        <div>
          <span className="eyebrow">A LITTLE MORE CLARITY</span>
          <h3>把生活的账，算明白</h3>
        </div>
        <span className="budget-symbol">¥</span>
      </div>
      <p className="muted small-text">
        下方为可编辑的示例。仅按你提交的数据计算，不连接银行或市场行情。
      </p>
      <div className="budget-income">
        <label>
          本期收入
          <input
            aria-label="本期收入"
            type="number"
            min="0"
            step="0.01"
            value={income}
            onChange={(e) => {
              setIncome(e.target.value);
              setResult(null);
            }}
          />
        </label>
        <label>
          币种
          <select
            aria-label="币种"
            value={currency}
            onChange={(e) => {
              setCurrency(e.target.value);
              setResult(null);
            }}
          >
            <option value="CNY">人民币 CNY</option>
            <option value="HKD">港币 HKD</option>
            <option value="USD">美元 USD</option>
          </select>
        </label>
      </div>
      <div className="budget-items">
        {items.map((item, i) => (
          <div className="budget-row" key={i}>
            <input
              aria-label={`支出名称 ${i + 1}`}
              value={item.name}
              maxLength={40}
              onChange={(e) => change(i, "name", e.target.value)}
            />
            <input
              aria-label={`支出金额 ${i + 1}`}
              type="number"
              min="0"
              step="0.01"
              value={item.amount}
              onChange={(e) => change(i, "amount", e.target.value)}
            />
            <button
              className="icon-button"
              aria-label={`移除支出 ${i + 1}`}
              onClick={() => {
                setItems(items.filter((_, n) => n !== i));
                setResult(null);
              }}
              disabled={items.length <= 1}
            >
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
      <button
        className="text-button"
        disabled={items.length >= 30}
        onClick={() => {
          setItems([...items, { name: "", amount: "0" }]);
          setResult(null);
        }}
      >
        <Plus size={14} /> 添加一项支出
      </button>
      <div className="budget-actions">
        <button className="secondary" disabled={busy} onClick={calculate}>
          {busy ? (
            <Loader2 size={16} className="spin" />
          ) : (
            <ArrowDownRight size={16} />
          )}
          精确计算
        </button>
        <button
          className="primary"
          disabled={busy || !result}
          onClick={async () => {
            setBusy(true);
            try {
              await onAnalyze(payload);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Sparkles size={16} />
          请明理分析
        </button>
      </div>
      {result && (
        <div className="budget-result">
          <div>
            <span>本期支出</span>
            <strong>
              {Number(result.expenses).toLocaleString("zh-CN", {
                minimumFractionDigits: 2,
              })}
            </strong>
          </div>
          <div>
            <span>本期结余</span>
            <strong
              className={Number(result.remaining) < 0 ? "negative" : "positive"}
            >
              {Number(result.remaining).toLocaleString("zh-CN", {
                minimumFractionDigits: 2,
              })}
            </strong>
          </div>
          <div>
            <span>结余率</span>
            <strong>
              {result.savings_rate === null
                ? "不适用"
                : `${result.savings_rate}%`}
            </strong>
          </div>
        </div>
      )}
      <p className="tiny muted">
        <ShieldCheck size={12} /> 金额由确定性计算器处理 · 分析需另行提交
      </p>
    </div>
  );
}
