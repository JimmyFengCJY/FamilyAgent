import os
import math
import threading
import time
from .db import can_access, now, uid
from .leases import LeaseHeartbeat
from .models import ModelGateway, TemporaryModelError, InvalidModelOutput, SYSTEM
from .context import POLICY, ContextOverflow, pack_context
from .memory import POLICY as MEMORY_POLICY, retrieve, references
from .approvals import propose_hash
from .tool_registry import registry, ToolContext, ToolError, ToolDenied, ToolArgumentsError, VERSION as TOOL_VERSION
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


def expert_findings(run):
    return [{"source_id": f"expert_{index}", "actor_id": step["character"]["id"],
             "actor_name": step["character"]["name"], "result": result}
            for index, (step, result) in enumerate(zip(run["steps"], run["results"])) if step["stage"] == "expert"]


def validate_discussion(result, context):
    """Check attribution, not whether model-inferred disagreements are true."""
    if "tool_request" in result:
        return
    if context["stage"] != "summary":
        result["consensus"], result["disagreements"] = [], []
        return
    sources = {f["source_id"]: f["result"] for f in context["expert_findings"]}
    for disagreement in result.get("disagreements", []):
        seen = set()
        for position in disagreement["positions"]:
            source = sources.get(position["source_id"])
            texts = [] if source is None else [source["summary"], *source.get("proposals", []), *source.get("risks", []), *source.get("assumptions", []), *source.get("missing_information", [])]
            if position["source_id"] in seen or not any(position["quote"] in text for text in texts):
                raise InvalidModelOutput("分歧引用必须来自不同专家的本轮原文")
            seen.add(position["source_id"])


def create_run(store, conv, user, data, conn, gateway=None):
    if any(r["conversation_id"] == conv["id"] and r["status"] in ACTIVE | {"paused"} for r in store.conversation_records("run", conv["id"], conn)):
        raise ValueError("这段对话仍在运行，请先暂停或停止")
    # A new discussion invalidates an earlier, unaccepted proposal.
    for old in store.conversation_records("run", conv["id"], conn):
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
    run["constraints"] = list(conv.get("constraints", []))
    run["context_policy"] = dict(POLICY)
    run["memory_policy"] = dict(MEMORY_POLICY)
    run["tool_policy"] = TOOL_VERSION
    store.put("run", run, conn)
    store.event(run["id"], "run.queued", {"steps": len(steps), "max_calls": run["max_calls"], "provider": run["provider"]}, conn)
    store.event(run["id"], "intent.resolved", run["intent"], conn)
    conv.update(updated_at=now(), status="discussing" if conv["mode"] == "meeting" else "active")
    store.put("conversation", conv, conn)
    return run


def set_proposal(run, kind, summary, actions):
    run["proposal"] = {"kind": kind, "summary": summary, "actions": actions, "expires_at": time.time() + 86400 * 7,
                       "required_approvers": run["participants"] if kind == "decision" else [run["owner_id"]]}
    run["proposal"]["hash"] = propose_hash(run)
    run.update(status="waiting_approval", approvals={})


class Harness:
    def __init__(self, store, gateway=None, delay=None, *, lease_seconds=120, heartbeat_interval=30, max_attempt_seconds=600):
        if (not all(math.isfinite(v) and v > 0 for v in (lease_seconds, heartbeat_interval, max_attempt_seconds))
                or heartbeat_interval >= lease_seconds / 2 or max_attempt_seconds < lease_seconds):
            raise ValueError("租约参数需要满足 0 < 心跳间隔 < 租约/2，最大续租窗口 >= 租约")
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = heartbeat_interval
        self.max_attempt_seconds = max_attempt_seconds
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
            run = self.store.claim_candidate(time.time(), conn)
            if run is not None:
                if run["status"] == "running":
                    self.store.event(run["id"], "run.recovered", {"checkpoint": run["cursor"], "note": "恢复最近提交的步骤；未完成模型调用可能重新执行"}, conn)
                timestamp = time.time()
                run.update(status="running", worker=self.worker, attempt_id=uid("attempt_"),
                    lease_until=math.floor((timestamp + self.lease_seconds) * 1000) / 1000,
                    lease_deadline=math.floor((timestamp + self.max_attempt_seconds) * 1000) / 1000,
                    lease_renewals=0, lease_renewed_at=None)
                self.store.put("run", run, conn)
                return run
        return None

    def owns(self, run, attempt):
        return bool(run and run["status"] == "running" and run.get("worker") == self.worker
                    and run.get("attempt_id") == attempt and run.get("lease_until", 0) > time.time())

    def discard(self, run_id, attempt, phase, conn, usage=None, call_id=None):
        fresh = self.store.get(run_id, conn)
        if fresh and usage:
            self.account_usage(fresh, attempt, usage)
            self.store.put("run", fresh, conn)
        self.store.event(run_id, "attempt.discarded", {"attempt_id": attempt, "phase": phase,
            "call_id": call_id, "reason": "运行状态、执行代次或租约已变化，结果未写入",
            **({"reported_usage": usage} if usage else {})}, conn)

    def account_usage(self, run, attempt, usage):
        accounted = run.setdefault("accounted_attempts", [])
        if attempt not in accounted:
            for field in ("input_tokens", "output_tokens"):
                run["usage"][field] += usage.get(field, 0)
            accounted.append(attempt)

    def tool_failure(self, run, name, exc, conn):
        message = str(exc) if isinstance(exc, ToolError) else "工具请求格式无效"
        run.update(status="failed", error=message, lease_until=0)
        self.store.event(run["id"], "tool.denied" if isinstance(exc, (ToolDenied, ToolArgumentsError)) else "tool.failed",
                         {"name": name, "reason": message}, conn)
        self.store.put("run", run, conn)

    def execute_pending_tool(self, claimed):
        """Prepare/commit in short transactions; readonly handler waits outside them."""
        store, attempt = self.store, claimed["attempt_id"]
        with store.tx() as conn:
            run = store.get(claimed["id"], conn)
            if not self.owns(run, attempt) or not run.get("pending_tool"):
                return True
            pending = run["pending_tool"]
            if pending.get("state") == "executing" and pending.get("attempt_id") == attempt:
                return True
            if pending.get("step") != run["cursor"]:
                self.tool_failure(run, pending["name"], ToolDenied("工具检查点与当前步骤不一致"), conn)
                return True
            user = store.user(run["owner_id"], conn)
            conv = store.get(run["conversation_id"], conn)
            capability = run["steps"][run["cursor"]]["character"]["capability"]
            ctx = ToolContext(store, None, user, "model", conv, run, capability)
            try:
                if run.get("tool_policy", TOOL_VERSION) != TOOL_VERSION:
                    raise ToolDenied("工具策略版本不可用，请开启新运行")
                prepared = registry.prepare(pending["name"], pending["arguments"], ctx)
            except ValueError as exc:
                self.tool_failure(run, pending["name"], exc, conn)
                return True
            pending.update(state="executing", attempt_id=attempt)
            store.put("run", run, conn)
            store.event(run["id"], "tool.started", {"name": pending["name"], "risk": prepared[0].risk,
                "origin": "model", "transport": prepared[0].transport, "call_id": pending["id"],
                "attempt_id": attempt}, conn)
        started = time.perf_counter()
        try:
            output = registry.invoke_read(prepared, ctx)
            error = None
        except ValueError as exc:
            output, error = None, exc
        with store.tx() as conn:
            fresh = store.get(run["id"], conn)
            if (not self.owns(fresh, attempt) or not fresh.get("pending_tool")
                or fresh["pending_tool"]["id"] != pending["id"]
                or fresh["cursor"] != pending["step"]
                or fresh["pending_tool"]["arguments"] != pending["arguments"]
                or fresh["pending_tool"]["name"] != pending["name"]):
                self.discard(run["id"], attempt, "tool", conn, call_id=pending["id"])
                return True
            run = fresh
            # Recheck current identity, ACL and policy after the wait, not snapshots.
            user = store.user(run["owner_id"], conn)
            conv = store.get(run["conversation_id"], conn)
            try:
                if run.get("tool_policy", TOOL_VERSION) != TOOL_VERSION:
                    raise ToolDenied("工具策略版本已变化")
                registry.prepare(pending["name"], pending["arguments"],
                    ToolContext(store, conn, user, "model", conv, run,
                                run["steps"][run["cursor"]]["character"]["capability"]))
            except ValueError as exc:
                error = exc
            if error:
                run["pending_tool"]["state"] = "failed"
                self.tool_failure(run, pending["name"], error, conn)
                return True
            run["tool_results"].append({"name": pending["name"], "result": output, "call_id": pending["id"]})
            run.pop("pending_tool")
            run.update(status="queued", worker=None, lease_until=0, updated_at=now())
            store.event(run["id"], "tool.completed", {"name": pending["name"], "risk": prepared[0].risk,
                "origin": "model", "transport": prepared[0].transport, "call_id": pending["id"],
                "attempt_id": attempt, "elapsed_seconds": round(time.perf_counter()-started, 6),
                "count": len(output) if isinstance(output, list) else 1}, conn)
            store.put("run", run, conn)
        return True

    def tick(self):
        run = self.claim()
        if not run:
            return False
        with LeaseHeartbeat(self.store, run, self.stop_event, self.heartbeat_interval, self.lease_seconds):
            return self.execute_claimed(run)

    def execute_claimed(self, run):
        if run.get("pending_tool"):
            return self.execute_pending_tool(run)
        store = self.store
        attempt = run["attempt_id"]
        with store.tx() as conn:
            run = store.get(run["id"], conn)
            if not self.owns(run, attempt):
                return True
            user = store.user(run["owner_id"], conn)
            conv = store.get(run["conversation_id"], conn)
            if not can_access(user, conv):
                run.update(status="failed", error="当前账号不再有权访问会话")
                store.put("run", run, conn)
                store.event(run["id"], "run.failed", {"reason": run["error"]}, conn)
                return True
            if run.get("tool_policy", TOOL_VERSION) != TOOL_VERSION:
                run.update(status="failed", error="工具策略版本不可用，请开启新运行", lease_until=0)
                store.put("run", run, conn)
                store.event(run["id"], "run.failed", {"reason": run["error"], "model_called": False}, conn)
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
                try:
                    run["finance_result"] = registry.execute("finance.calculate", run["finance"], ToolContext(store, conn, user, "budget", conv, run, "finance"))
                except ToolError as exc:
                    run.update(status="failed", error=str(exc), lease_until=0)
                    store.event(run["id"], "tool.denied", {"name": "finance.calculate", "reason": str(exc)}, conn)
                    store.put("run", run, conn)
                    return True
            step = run["steps"][run["cursor"]]
            # Context built from this resource only. Support never loads long-term memory.
            independent = run.get("orchestration") == "independent" and step["stage"] == "expert"
            history = store.conversation_records("message", conv["id"], conn, limit=10, exclude_run=run["id"] if independent else None)
            try:
                memory, memory_receipt = retrieve(store, user, conv, run["prompt"] + "\n" + "\n".join(run.get("constraints", [])), conn, run.get("memory_policy"))
            except ValueError as exc:
                run.update(status="failed", error=str(exc), lease_until=0)
                store.event(run["id"], "run.failed", {"reason": str(exc), "model_called": False}, conn)
                store.put("run", run, conn)
                return True
            context = {"mode": conv["mode"], "stage": step["stage"], "character": step["character"], "prompt": run["prompt"], "history": [{"actor": m["actor_name"], "content": m["content"][:1600]} for m in history], "prior_results": run["results"], "memories": memory, "finance_result": run["finance_result"], "tool_results": run["tool_results"], "expert_roster": conv.get("character_snapshots", [])}
            context["expert_findings"] = expert_findings(run) if step["stage"] == "summary" else []
            context["analysis_mode"] = "independent" if independent else "shared"
            if independent:
                context["prior_results"] = []
                context["task_instruction"] = "独立首轮分析：只根据共同问题和既有讨论约束，从自己的职责给出建议、假设与风险，不推测其他专家本轮观点。"
            if conv["mode"] == "meeting" and run.get("orchestration", "multi") == "single":
                context["task_instruction"] = "作为综合助手，在一次回答中独立分析所有列出的专家视角，给出完整建议、分歧、待确认事项和行动草案。"
            if run.get("format_retry"):
                context["output_correction"] = "上次输出格式无效，请严格返回系统要求的 JSON。"
            context["intent"] = run.get("intent") or resolve_intent(run["prompt"], conv["mode"])
            context["constraints"] = run.get("constraints", [])
            context["tools"] = registry.catalog(ToolContext(store, conn, user, "model", conv, run, step["character"]["capability"]))
            try:
                context, context_usage = pack_context(context, SYSTEM, run.get("context_policy"))
            except ContextOverflow as exc:
                run.update(status="failed", error=str(exc), lease_until=0)
                store.event(run["id"], "context.rejected", {"reason": str(exc), "model_called": False}, conn)
                store.put("run", run, conn)
                return True
            store.event(run["id"], "context.prepared", {**context_usage, "stage": step["stage"], "analysis_mode": context["analysis_mode"], "prior_results": len(context["prior_results"]), "expert_sources": len(context["expert_findings"])}, conn)
            store.event(run["id"], "memory.retrieved", {**memory_receipt, "sent": len(context["memories"]), "dropped_by_budget": memory_receipt["selected"] - len(context["memories"])}, conn)
            run["available_tools"] = [tool["name"] for tool in context["tools"]]
            run["calls"] += 1
            store.event(run["id"], "agent.started", {"name": step["character"]["name"], "capability": step["character"]["capability"], "stage": step["stage"], "call": run["calls"]}, conn)
            store.put("run", run, conn)
        usage = {}
        try:
            if run.get("model_config") and hasattr(self.gateway, "config_snapshot") and run["model_config"] != self.gateway.config_snapshot():
                raise ValueError("运行的模型配置与当前 Worker 不一致，请恢复原配置后继续")
            if self.gateway.provider == "demo":
                self.stop_event.wait(self.delay)
            result, usage = self.gateway.generate(context)
            validate_discussion(result, context)
            error = None
        except Exception as exc:
            result, usage, error = None, (getattr(exc, "usage", None) or usage), exc
        with store.tx() as conn:
            fresh = store.get(run["id"], conn)
            if not self.owns(fresh, attempt):
                self.discard(run["id"], attempt, "model", conn, usage=usage)
                return True
            run = fresh
            self.account_usage(run, attempt, usage)
            user = store.user(run["owner_id"], conn)
            conv = store.get(run["conversation_id"], conn)
            if not can_access(user, conv):
                run.update(status="failed", error="当前账号不再有权访问会话", lease_until=0)
                store.put("run", run, conn)
                store.event(run["id"], "run.failed", {"reason": run["error"]}, conn)
                return True
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
                safe_name = name if isinstance(name, str) and name in {t["name"] for t in registry.catalog()} else "unknown"
                try:
                    if run["tool_requests"] >= 2:
                        raise ToolError("本次运行的工具请求次数已用完")
                    from .schemas import ToolRequest
                    request = ToolRequest.model_validate(result["tool_request"])
                    prepared = registry.prepare(request.name, request.arguments, ToolContext(store, conn, user, "model", conv, run, step["character"]["capability"]))
                    run["pending_tool"] = {"id": uid("toolcall_"), "name": request.name,
                        "arguments": prepared[1].model_dump(), "state": "pending", "step": run["cursor"]}
                    run["tool_requests"] += 1
                    store.event(run["id"], "tool.queued", {"name": request.name,
                        "call_id": run["pending_tool"]["id"], "note": "已保存只读工具检查点，等待事务外执行"}, conn)
                except ValueError as exc:
                    message = str(exc) if isinstance(exc, ToolError) else "工具请求格式无效"
                    run.update(status="failed", error=message, lease_until=0)
                    store.event(run["id"], "tool.denied" if isinstance(exc, (ToolDenied, ToolArgumentsError)) else "tool.failed", {"name": safe_name, "reason": message}, conn)
                store.put("run", run, conn)
            else:
                result = filter_result(result, context["intent"])
                if step["stage"] == "summary":
                    result["expert_sources"] = [{"source_id": f["source_id"], "actor_id": f["actor_id"], "actor_name": f["actor_name"]} for f in context["expert_findings"]]
                run["results"].append(result)
                msg = {"id": uid("msg_"), "conversation_id": conv["id"], "run_id": run["id"], "actor_type": "agent", "actor_id": step["character"]["id"], "actor_name": step["character"]["name"], "color": step["character"]["color"], "stage": step["stage"], "content": result["summary"], "result": result, "provider": usage["provider"], "created_at": now()}
                msg["memory_sources"] = references(context["memories"])
                store.put("message", msg, conn)
                run.update(cursor=run["cursor"] + 1, status="queued", lease_until=0, updated_at=now(), retry_count=0, format_retry=False)
                store.event(run["id"], "agent.completed", {"name": step["character"]["name"], "stage": step["stage"], "usage": usage}, conn)
                store.event(run["id"], "checkpoint.saved", {"step": run["cursor"], "total": len(run["steps"])}, conn)
                if run["cursor"] >= len(run["steps"]):
                    self.finish(run, conv, conn)
                else:
                    store.put("run", run, conn)
        if run.get("pending_tool") and run["status"] == "running":
            return self.execute_pending_tool(run)
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
