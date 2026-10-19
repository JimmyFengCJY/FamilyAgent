import { useState } from "react";
import { Check, Loader2, Plus } from "lucide-react";
import { Avatar, capabilityNames, modeNames } from "./components";
import type { Character, Mode, State } from "./types";

export function ConversationForm({
  state,
  initialMode,
  submit,
}: {
  state: State;
  initialMode: Mode;
  submit: (data: unknown, prompt: string) => Promise<void>;
}) {
  const defaults: Record<Mode, string[]> = {
    meeting: ["planner", "finance", "support"],
    planning: ["planner"],
    support: ["support"],
    finance: ["finance"],
    assistant: ["assistant"],
  };
  const [mode, setMode] = useState<Mode>(initialMode);
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [ids, setIds] = useState(defaults[initialMode]);
  const [participants, setParticipants] = useState(
    state.members.map((m) => m.id),
  );
  const [memory, setMemory] = useState(false);
  const [busy, setBusy] = useState(false);
  const chars = state.characters.filter((c) =>
    mode === "meeting"
      ? c.capability !== "host"
      : mode === "support"
        ? c.capability === "support"
        : c.capability !== "host",
  );
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await submit(
            {
              title:
                title.trim() ||
                `${modeNames[mode]} · ${new Date().toLocaleDateString("zh-CN", { timeZone: "Asia/Hong_Kong" })}`,
              mode,
              character_ids: ids,
              participants: mode === "meeting" ? participants : [],
              use_memory: memory,
            },
            prompt,
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      <label>
        想开启什么空间？
        <select
          aria-label="空间类型"
          value={mode}
          onChange={(e) => {
            const m = e.target.value as Mode;
            setMode(m);
            setIds(defaults[m]);
          }}
        >
          {Object.entries(modeNames).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        给这次讨论起个名字
        <input
          aria-label="空间名称"
          placeholder={
            mode === "meeting"
              ? "例如：我们的秋日旅行计划"
              : "例如：每天进步一点点"
          }
          maxLength={100}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label>
        邀请 AI 伙伴{" "}
        <span className="muted">
          {mode === "meeting" ? "最多 3 位，主持人自动加入" : "选择 1 位"}
        </span>
      </label>
      <div className="character-picker">
        {chars.map((c) => (
          <button
            type="button"
            className={`pick-character ${ids.includes(c.id) ? "selected" : ""}`}
            key={c.id}
            onClick={() =>
              setIds(
                mode !== "meeting"
                  ? [c.id]
                  : ids.includes(c.id)
                    ? ids.filter((id) => id !== c.id)
                    : ids.length < 3
                      ? [...ids, c.id]
                      : ids,
              )
            }
          >
            <Avatar small character={c} />
            <span>
              {c.name}
              <small>{capabilityNames[c.capability]}</small>
            </span>
            {ids.includes(c.id) && <Check size={14} />}
          </button>
        ))}
      </div>
      {mode === "meeting" ? (
        <>
          <label>参与的家庭成员</label>
          <div className="member-picker">
            {state.members.map((m) => (
              <label key={m.id}>
                <input
                  type="checkbox"
                  checked={participants.includes(m.id)}
                  disabled={m.id === state.user.id}
                  onChange={(e) =>
                    setParticipants(
                      e.target.checked
                        ? [...participants, m.id]
                        : participants.filter((id) => id !== m.id),
                    )
                  }
                />
                {m.name}
                {m.id === state.user.id && "（我）"}
              </label>
            ))}
          </div>
        </>
      ) : (
        mode !== "support" && (
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={memory}
              onChange={(e) => setMemory(e.target.checked)}
            />
            参考我主动保存的个人记忆
          </label>
        )
      )}
      <label>
        想先说的话 <span className="muted">可稍后填写</span>
        <textarea
          aria-label="议题内容"
          rows={3}
          maxLength={8000}
          placeholder="目标是什么？有哪些约束？"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
      </label>
      <div className="dialog-footer">
        <span className="tiny muted">
          {mode === "meeting"
            ? "仅受邀成员可见 · 不读取私密记忆"
            : "个人私密空间 · 不自动保存长期记忆"}
        </span>
        <button
          className="primary"
          type="submit"
          disabled={busy || ids.length === 0}
        >
          {busy ? <Loader2 size={16} className="spin" /> : <Plus size={16} />}{" "}
          {prompt.trim() ? "开始讨论" : "创建空间"}
        </button>
      </div>
    </form>
  );
}

export function CharacterForm({
  character,
  submit,
}: {
  character?: Character;
  submit: (data: unknown, key?: string) => Promise<void>;
}) {
  const [name, setName] = useState(
    character
      ? character.builtin
        ? `${character.name}的分身`
        : character.name
      : "",
  );
  const [capability, setCapability] = useState(
    character?.capability || "planner",
  );
  const [tone, setTone] = useState(character?.tone || "");
  const [description, setDescription] = useState(character?.description || "");
  const [color, setColor] = useState(character?.color || "lavender");
  const [busy, setBusy] = useState(false);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await submit(
            { name, capability, tone, description, color },
            character && !character.builtin ? character.id : undefined,
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      <label>
        角色名字
        <input
          aria-label="角色名字"
          required
          maxLength={30}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：阿岚"
        />
      </label>
      <div className="form-grid">
        <label>
          专业能力
          <select
            aria-label="专业能力"
            value={capability}
            onChange={(e) =>
              setCapability(e.target.value as Character["capability"])
            }
          >
            {Object.entries(capabilityNames).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          角色配色
          <select
            aria-label="角色配色"
            value={color}
            onChange={(e) => setColor(e.target.value)}
          >
            <option value="lavender">浅紫</option>
            <option value="sage">鼠尾草绿</option>
            <option value="peach">暖杏</option>
            <option value="blue">雾蓝</option>
            <option value="rose">浅玫瑰</option>
          </select>
        </label>
      </div>
      <label>
        交流风格
        <textarea
          aria-label="交流风格"
          required
          rows={3}
          maxLength={200}
          value={tone}
          onChange={(e) => setTone(e.target.value)}
          placeholder="例如：像耐心的朋友，少说教，用具体例子解释"
        />
      </label>
      <label>
        一句话介绍
        <input
          aria-label="角色介绍"
          maxLength={300}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="陪你把复杂的目标变简单"
        />
      </label>
      <div className="role-preview">
        <Avatar character={{ name, color }} />
        <div>
          <strong>{name || "你的新伙伴"}</strong>
          <p>{tone || "在这里，定义 TA 的交流方式。"}</p>
        </div>
      </div>
      <p className="tiny muted">
        人格改变表达方式，工具权限由系统控制。已有会话保留创建时的角色版本。
      </p>
      <div className="dialog-footer">
        <span className="tiny muted">
          {character && !character.builtin
            ? `保存为 v${character.version + 1}`
            : "创建后仅自己可选用"}
        </span>
        <button className="primary" disabled={busy} type="submit">
          {busy && <Loader2 size={16} className="spin" />}保存角色
        </button>
      </div>
    </form>
  );
}

export function TaskForm({
  submit,
}: {
  submit: (data: unknown) => Promise<void>;
}) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await submit({
            title,
            due_at: due ? new Date(due).toISOString() : null,
          });
        } finally {
          setBusy(false);
        }
      }}
    >
      <label>
        准备做什么？
        <input
          aria-label="任务标题"
          autoFocus
          required
          maxLength={150}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="一件具体、可以开始的事"
        />
      </label>
      <label>
        提醒时间 <span className="muted">可选，按设备时区输入</span>
        <input
          aria-label="提醒时间"
          type="datetime-local"
          value={due}
          onChange={(e) => setDue(e.target.value)}
        />
      </label>
      <p className="tiny muted">
        提醒保存在服务端。服务运行时触发；到期后在应用通知中显示，不发送手机推送。
      </p>
      <div className="dialog-footer">
        <span />
        <button className="primary" type="submit" disabled={busy}>
          添加任务
        </button>
      </div>
    </form>
  );
}

export function MemoryForm({
  submit,
}: {
  submit: (data: unknown) => Promise<void>;
}) {
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          await submit({ content });
        } finally {
          setBusy(false);
        }
      }}
    >
      <label>
        希望伙伴记住什么？
        <textarea
          aria-label="记忆内容"
          required
          rows={5}
          maxLength={1000}
          placeholder="例如：工作日晚上只安排 30 分钟学习，我更喜欢小步推进。"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </label>
      <p className="tiny muted">
        仅个人、已开启记忆的会话可以参考。家庭会议和情绪支持不读取这些内容。删除会阻止未来读取，但不会撤回历史发言中已引用的内容。
      </p>
      <div className="dialog-footer">
        <span />
        <button className="primary" type="submit" disabled={busy}>
          保存这条记忆
        </button>
      </div>
    </form>
  );
}
