import hashlib
import json
import os
import threading
import time
from .db import can_access, now, uid
from .models import ModelGateway, TemporaryModelError, InvalidModelOutput
from .tools import calculate_budget
from .intent import resolve_intent, filter_result


ACTIVE = {"queued", "running"}


def sequence(conversation, characters, intent=None):
    selected = [characters[c] for c in conversation["character_ids"]]
    if conversation["mode"] == "meeting":
        if intent and intent["name"] in {"question", "support"}:
            return [{"character": characters["host"], "stage": "response"}]
        if conversation.get("orchestration") == "single":
            return [{"character": characters["host"], "stage": "summary"}]
        return [{"character": characters["host"], "stage": "opening"}] + [{"character": c, "stage": "expert"} for c in selected if c["capability"] != "host"] + [{"character": characters["host"], "stage": "summary"}]
    return [{"character": selected[0], "stage": "response"}]


def create_run(store, conv, user, data, conn, gateway=None):
    if any(r["conversation_id"] == conv["id"] and r["status"] in ACTIVE | {"paused"} for r in store.all("run", conn)):
        raise ValueError("这段对话仍在运行，请先暂停或停止")
    # A new discussion invalidates an earlier, unaccepted proposal.
    for old in store.all("run", conn):
        if old["conversation_id"] == conv["id"] and old["status"] == "waiting_approval":
            old["status"] = "cancelled"
            old["superseded"] = True
            store.put("run", old, conn)
            store.event(old["id"], "run.cancelled", {"reason": "后续讨论替代了此草案"}, conn)
    msg = {"id": uid("msg_"), "conversation_id": conv["id"], "actor_type": "human", "actor_id": user["id"], "actor_name": user["name"], "content": data.content, "created_at": now()}
    store.put("message", msg, conn)
    chars = {c["id"]: c for c in store.all("character", conn) if c.get("builtin") or c["owner_id"] == user["id"]}
    chars.update({c["id"]: c for c in conv.get("character_snapshots", [])})
    intent = resolve_intent(data.content, conv["mode"], data.intent)
    steps = sequence(conv, chars, intent)
    gateway = gateway or ModelGateway()
    run = {"id": uid("run_"), "conversation_id": conv["id"], "owner_id": user["id"], "household": conv["household"], "scope": conv["scope"], "participants": conv["participants"], "mode": conv["mode"], "status": "queued", "created_at": now(), "updated_at": now(), "prompt": data.content, "steps": steps, "cursor": 0, "calls": 0, "max_calls": data.max_calls, "results": [], "usage": {"input_tokens": 0, "output_tokens": 0}, "finance": data.finance.model_dump() if data.finance else None, "finance_result": None, "tool_results": [], "tool_requests": 0, "retry_count": 0, "approvals": {}, "version": 1, "lease_until": 0, "worker": None, "error": None, "provider": os.getenv("FAMILYAGENT_PROVIDER", "demo")}
    run["provider"] = gateway.provider
    if hasattr(gateway, "config_snapshot"):
        run["model_config"] = gateway.config_snapshot()
    run["orchestration"] = conv.get("orchestration", "multi")
    run["intent"] = intent
    store.put("run", run, conn)
    store.event(run["id"], "run.queued", {"steps": len(steps), "max_calls": run["max_calls"], "provider": run["provider"]}, conn)
    store.event(run["id"], "intent.resolved", run["intent"], conn)
    conv.update(updated_at=now(), status="discussing" if conv["mode"] == "meeting" else "active")
    store.put("conversation", conv, conn)
    return run


def propose_hash(run):
    return hashlib.sha256(json.dumps({"run": run["id"], "version": run["version"], "proposal": {k: run["proposal"].get(k) for k in ("kind", "summary", "actions", "required_approvers", "expires_at")}, "owner": run["owner_id"]}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def set_proposal(run, kind, summary, actions):
    run["proposal"] = {"kind": kind, "summary": summary, "actions": actions, "expires_at": time.time() + 86400 * 7,
                       "required_approvers": run["participants"] if kind == "decision" else [run["owner_id"]]}
    run["proposal"]["hash"] = propose_hash(run)
    run.update(status="waiting_approval", approvals={})


class Harness:
    def __init__(self, store, gateway=None, delay=None):
        self.store = store
        self.gateway = gateway or ModelGateway()
        self.delay = float(os.getenv("FAMILYAGENT_DEMO_DELAY", "0.65")) if delay is None else delay
        self.stop_event = threading.Event()
        self.worker = uid("worker_")
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="familyagent-worker")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def loop(self):
        while not self.stop_event.is_set():
            try:
                worked = self.tick()
                self.deliver_reminders()
            except Exception:
                # Avoid logging sensitive payloads. A claimed job is reclaimed after lease expiry.
                import logging
                logging.getLogger("familyagent").exception("Worker infrastructure failure")
                worked = False
            self.stop_event.wait(0.12 if worked else 0.5)

    def claim(self):
        with self.store.tx() as conn:
            for run in self.store.all("run", conn):
                if run.get("next_attempt_at", 0) > time.time():
                    continue
                if run["status"] == "queued" or (run["status"] == "running" and run.get("lease_until", 0) < time.time()):
                    if run["status"] == "running":
                        self.store.event(run["id"], "run.recovered", {"checkpoint": run["cursor"], "note": "恢复最近提交的步骤；未完成模型调用可能重新执行"}, conn)
                    run.update(status="running", worker=self.worker, lease_until=time.time() + 120)
                    self.store.put("run", run, conn)
                    return run
        return None

    def tick(self):
        run = self.claim()
        if not run:
            return False
        store = self.store
        with store.tx() as conn:
            run = store.get(run["id"], conn)
            user = store.user(run["owner_id"], conn)
            conv = store.get(run["conversation_id"], conn)
            if not can_access(user, conv):
                run.update(status="failed", error="当前账号不再有权访问会话")
                store.put("run", run, conn)
                store.event(run["id"], "run.failed", {"reason": run["error"]}, conn)
                return True
            if run["cursor"] >= len(run["steps"]):
                self.finish(run, conv, conn)
                return True
            if run["calls"] >= run["max_calls"]:
                run.update(status="exhausted", lease_until=0)
                store.event(run["id"], "run.exhausted", {"checkpoint": run["cursor"], "reason": "模型调用预算已用完，已保留阶段结果"}, conn)
                store.put("run", run, conn)
                return True
            if run["finance"] and run["finance_result"] is None:
                store.event(run["id"], "tool.started", {"name": "finance.calculate", "risk": "L0"}, conn)
                run["finance_result"] = calculate_budget(run["finance"])
                store.event(run["id"], "tool.completed", {"name": "finance.calculate", "result": run["finance_result"]}, conn)
            step = run["steps"][run["cursor"]]
            run["calls"] += 1
            store.event(run["id"], "agent.started", {"name": step["character"]["name"], "capability": step["character"]["capability"], "stage": step["stage"], "call": run["calls"]}, conn)
            store.put("run", run, conn)
            # Context built from this resource only. Support never loads long-term memory.
            history = [m for m in store.all("message", conn) if m["conversation_id"] == conv["id"]][-10:]
            memory = []
            if conv.get("use_memory") and conv["mode"] != "support":
                memory = [m for m in store.all("memory", conn) if m["owner_id"] == user["id"]][-6:]
                # Personal memory must never enter a shared meeting.
                if conv["scope"] == "family":
                    memory = []
            context = {"mode": conv["mode"], "stage": step["stage"], "character": step["character"], "prompt": run["prompt"], "history": [{"actor": m["actor_name"], "content": m["content"][:1600]} for m in history], "prior_results": run["results"], "memories": memory, "finance_result": run["finance_result"], "tool_results": run["tool_results"], "expert_roster": conv.get("character_snapshots", [])}
            if conv["mode"] == "meeting" and run.get("orchestration", "multi") == "single":
                context["task_instruction"] = "作为综合助手，在一次回答中独立分析所有列出的专家视角，给出完整建议、分歧、待确认事项和行动草案。"
            if run.get("format_retry"):
                context["output_correction"] = "上次输出格式无效，请严格返回系统要求的 JSON。"
            context["intent"] = run.get("intent") or resolve_intent(run["prompt"], conv["mode"])
        try:
            if run.get("model_config") and hasattr(self.gateway, "config_snapshot") and run["model_config"] != self.gateway.config_snapshot():
                raise ValueError("运行的模型配置与当前 Worker 不一致，请恢复原配置后继续")
            if self.gateway.provider == "demo":
                self.stop_event.wait(self.delay)
            result, usage = self.gateway.generate(context)
            error = None
        except Exception as exc:
            result, usage, error = None, getattr(exc, "usage", {}), exc
        with store.tx() as conn:
            fresh = store.get(run["id"], conn)
            if fresh["status"] != "running" or fresh["worker"] != self.worker:
                return True  # Cancellation/pause wins; no side effects have occurred.
            run = fresh
            for field in ("input_tokens", "output_tokens"):
                run["usage"][field] += usage.get(field, 0)
            if error:
                retries = run.get("retry_count", 0)
                recoverable = isinstance(error, (TemporaryModelError, InvalidModelOutput, httpx_error()))
                if recoverable and retries < (1 if isinstance(error, InvalidModelOutput) else 2):
                    run.update(status="queued", retry_count=retries + 1, format_retry=isinstance(error, InvalidModelOutput), lease_until=0, next_attempt_at=time.time() + (0 if isinstance(error, InvalidModelOutput) else min(2 ** retries, 8)))
                    store.event(run["id"], "step.retry", {"attempt": retries + 1, "reason": type(error).__name__, "usage": usage}, conn)
                else:
                    message = str(error) if isinstance(error, (ValueError, TemporaryModelError, InvalidModelOutput)) else "模型连接失败，请检查网络后恢复运行"
                    run.update(status="failed", error=message, lease_until=0)
                    store.event(run["id"], "run.failed", {"reason": message, "usage": usage}, conn)
                store.put("run", run, conn)
                return True
            if "tool_request" in result:
                name = result["tool_request"].get("name") if isinstance(result["tool_request"], dict) else None
                if name != "task.list" or step["character"]["capability"] not in {"planner", "assistant"} or conv["scope"] == "family" or run["tool_requests"] >= 2:
                    run.update(status="failed", error="工具请求不在该角色或本次运行的授权范围内")
                    store.event(run["id"], "tool.denied", {"name": name, "reason": run["error"]}, conn)
                else:
                    tasks = [{"title": t["title"], "status": t["status"]} for t in store.all("task", conn) if t["owner_id"] == user["id"]][:30]
                    run["tool_results"].append({"name": name, "result": tasks})
                    run["tool_requests"] += 1
                    run.update(status="queued", lease_until=0)
                    store.event(run["id"], "tool.completed", {"name": name, "count": len(tasks), "risk": "L0"}, conn)
                store.put("run", run, conn)
                return True
            result = filter_result(result, context["intent"])
            run["results"].append(result)
            msg = {"id": uid("msg_"), "conversation_id": conv["id"], "run_id": run["id"], "actor_type": "agent", "actor_id": step["character"]["id"], "actor_name": step["character"]["name"], "color": step["character"]["color"], "stage": step["stage"], "content": result["summary"], "result": result, "provider": usage["provider"], "created_at": now()}
            store.put("message", msg, conn)
            run.update(cursor=run["cursor"] + 1, status="queued", lease_until=0, updated_at=now(), retry_count=0, format_retry=False)
            store.event(run["id"], "agent.completed", {"name": step["character"]["name"], "stage": step["stage"], "usage": usage}, conn)
            store.event(run["id"], "checkpoint.saved", {"step": run["cursor"], "total": len(run["steps"])}, conn)
            if run["cursor"] >= len(run["steps"]):
                self.finish(run, conv, conn)
            else:
                store.put("run", run, conn)
        return True

    def finish(self, run, conv, conn):
        final = run["results"][-1] if run["results"] else {}
        actions = final.get("suggested_actions", [])
        intent = run.get("intent") or resolve_intent(run["prompt"], conv["mode"])
        decision = conv["mode"] == "meeting" and intent["name"] not in {"question", "support"}
        if decision or (actions and intent["auto_task_proposal"]):
            set_proposal(run, "decision" if decision else "tasks", final.get("summary", ""), actions)
            self.store.event(run["id"], "approval.required", {"version": run["version"], "kind": run["proposal"]["kind"], "actions": actions, "hash": run["proposal"]["hash"]}, conn)
            conv["status"] = "decision_pending" if decision else "proposal_pending"
        else:
            run["status"] = "succeeded"
            conv["status"] = "active"
        run.update(lease_until=0, updated_at=now())
        self.store.put("run", run, conn)
        self.store.put("conversation", conv, conn)
        self.store.event(run["id"], "run." + ("waiting_approval" if run["status"] == "waiting_approval" else "completed"), {"calls": run["calls"], "usage": run["usage"]}, conn)

    def deliver_reminders(self):
        with self.store.tx() as conn:
            for task in self.store.all("task", conn):
                if task["status"] == "todo" and task.get("due_at") and not task.get("notified") and task["due_at"] <= now():
                    notification = {"id": "reminder_" + task["id"], "owner_id": task["owner_id"], "title": "任务到时间了", "content": task["title"], "created_at": now(), "read": False}
                    self.store.put("notification", notification, conn)
                    task["notified"] = True
                    self.store.put("task", task, conn)


def httpx_error():
    import httpx
    return httpx.TransportError
