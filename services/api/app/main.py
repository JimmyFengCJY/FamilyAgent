import asyncio
import hashlib
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .auth import current_user, hash_password, logout, new_session, verify_password
from .db import Store, can_access, dump, now, uid
from .harness import ACTIVE, Harness, create_run, propose_hash, set_proposal
from .schemas import (Acceptance, CharacterInput, ConversationInput, FinanceInput,
                      Login, MemoryInput, MessageInput, Register, ResumeInput,
                      TaskInput, TaskUpdate)
from .seed import seed
from .tools import calculate_budget
from .config import runtime_revision


ROOT = Path(__file__).resolve().parents[3]


def create_app(db_path=None, start_worker=True, demo=None, gateway=None):
    demo = os.getenv("FAMILYAGENT_DEMO_LOGIN", "1") == "1" if demo is None else demo
    store = Store(db_path)
    seed(store, demo)
    harness = Harness(store, gateway)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            harness.start()
        yield
        harness.stop()

    app = FastAPI(title="FamilyAgent", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.harness, app.state.demo_login = store, harness, demo
    app.state.runtime_revision = runtime_revision(ROOT)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    attempts = defaultdict(list)

    @app.middleware("http")
    async def protections(request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            allowed = {"http://127.0.0.1:5173", "http://localhost:5173", str(request.base_url).rstrip("/")}
            if origin and origin not in allowed:
                return JSONResponse({"detail": "请求来源不受信任"}, status_code=403)
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 100000):
                return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def check_login_rate(request):
        ip = request.client.host if request.client else "local"
        attempts[ip] = [t for t in attempts[ip] if t > time.time() - 60]
        if len(attempts[ip]) >= 20:
            raise HTTPException(429, "尝试次数过多，请稍后再试")
        attempts[ip].append(time.time())

    def accessible(key, user, kind=None, conn=None):
        obj = store.get(key, conn)
        if not obj or (kind and not key.startswith(kind)) or not can_access(user, obj):
            raise HTTPException(404, "资源不存在或无权访问")
        return obj

    def run_for(key, user, conn=None):
        run = store.get(key, conn)
        if not run or not key.startswith("run_"):
            raise HTTPException(404, "运行不存在")
        accessible(run["conversation_id"], user, "conv_", conn)
        return run

    @app.get("/api/health")
    def health():
        return {"ok": True, "service": "familyagent", "provider": harness.gateway.provider, "demo_login": demo,
                "model_config": harness.gateway.config_snapshot() if hasattr(harness.gateway, "config_snapshot") else None,
                "runtime_revision": app.state.runtime_revision, "interaction_policy": "intent-v1"}

    @app.post("/api/auth/demo")
    def demo_login(request: Request, response: Response, account: str = "demo-jimmy"):
        if not demo or account not in {"demo-jimmy", "demo-lin"} or (request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}):
            raise HTTPException(403, "体验账号仅在本地开发模式可用")
        new_session(store, account, response)
        return store.user(account)

    @app.post("/api/auth/register", status_code=201)
    def register(data: Register, request: Request, response: Response):
        check_login_rate(request)
        email = data.email.strip().lower()
        if "@" not in email:
            raise HTTPException(422, "请输入有效邮箱")
        user_id, household = uid("user_"), uid("family_")
        try:
            with store.tx() as conn:
                if data.invite:
                    invite = conn.execute("SELECT * FROM invitations WHERE token=? AND expires>? AND used=0", (data.invite, time.time())).fetchone()
                    if not invite:
                        raise HTTPException(422, "邀请码无效或已使用")
                    household = invite["household"]
                    conn.execute("UPDATE invitations SET used=1 WHERE token=?", (data.invite,))
                conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user_id, email, data.name.strip(), hash_password(data.password), household))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "邮箱已注册")
        new_session(store, user_id, response, request.url.scheme == "https")
        return store.user(user_id)

    @app.post("/api/auth/login")
    def login(data: Login, request: Request, response: Response):
        check_login_rate(request)
        with store.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (data.email.strip().lower(),)).fetchone()
        if not row or (row["id"].startswith("demo-") and not demo) or not verify_password(data.password, row["password"]):
            raise HTTPException(401, "邮箱或密码不正确")
        new_session(store, row["id"], response, request.url.scheme == "https")
        return store.user(row["id"])

    @app.post("/api/auth/logout")
    def signout(request: Request, response: Response):
        logout(store, request, response)
        return {"ok": True}

    @app.post("/api/invitations")
    def invite(user=Depends(current_user)):
        token = secrets.token_urlsafe(18)
        with store.tx() as conn:
            conn.execute("INSERT INTO invitations VALUES(?,?,?,0)", (token, user["household"], time.time() + 86400))
        return {"token": token, "expires_in": "24 小时，仅可使用一次"}

    @app.get("/api/state")
    def state(user=Depends(current_user)):
        convs = [c for c in store.all("conversation") if can_access(user, c)]
        ids = {c["id"] for c in convs}
        return {"user": user, "members": store.members(user["household"]), "provider": harness.gateway.provider,
                "demo_login": demo, "characters": [c for c in store.all("character") if c.get("builtin") or c["owner_id"] == user["id"]],
                "conversations": sorted(convs, key=lambda c: c["updated_at"], reverse=True),
                "runs": [r for r in store.all("run") if r["conversation_id"] in ids],
                "tasks": [t for t in store.all("task") if t["owner_id"] == user["id"]],
                "plans": [p for p in store.all("plan") if p["owner_id"] == user["id"]],
                "memories": [m for m in store.all("memory") if m["owner_id"] == user["id"]],
                "notifications": [n for n in store.all("notification") if n["owner_id"] == user["id"]]}

    @app.post("/api/characters", status_code=201)
    def character(data: CharacterInput, user=Depends(current_user)):
        obj = {**data.model_dump(), "id": uid("char_"), "owner_id": user["id"], "version": 1, "initial": data.name[0], "builtin": False}
        with store.tx() as conn:
            store.put("character", obj, conn)
        return obj

    @app.put("/api/characters/{key}")
    def update_character(key: str, data: CharacterInput, user=Depends(current_user)):
        with store.tx() as conn:
            old = accessible(key, user, "char_", conn)
            store.put("character_version", {**old, "id": f"version_{key}_{old['version']}"}, conn)
            value = {**old, **data.model_dump(), "initial": data.name[0], "version": old["version"] + 1}
            store.put("character", value, conn)
        return value

    @app.post("/api/conversations", status_code=201)
    def conversation(data: ConversationInput, user=Depends(current_user)):
        defaults = {"meeting": ["planner", "finance", "support"], "planning": ["planner"], "support": ["support"], "finance": ["finance"], "assistant": ["assistant"]}
        ids = data.character_ids or defaults[data.mode]
        if data.mode != "meeting" and len(ids) != 1:
            raise HTTPException(422, "个人场景请选择一位角色")
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "角色不能重复")
        chars = []
        for key in ids:
            char = store.get(key)
            if not char or (not char.get("builtin") and char.get("owner_id") != user["id"]):
                raise HTTPException(404, "角色不可用")
            if data.mode == "meeting" and char["capability"] == "host":
                raise HTTPException(422, "会议主持人已自动加入，请选择专家角色")
            chars.append(char)
        allowed = {m["id"] for m in store.members(user["household"])}
        participants = list(dict.fromkeys([user["id"]] + data.participants)) if data.mode == "meeting" else [user["id"]]
        if not set(participants).issubset(allowed):
            raise HTTPException(422, "只能邀请本家庭成员")
        obj = {"id": uid("conv_"), "owner_id": user["id"], "household": user["household"], "scope": "family" if data.mode == "meeting" else "private", "participants": participants,
               "mode": data.mode, "title": data.title.strip(), "character_ids": ids, "character_snapshots": chars,
               "use_memory": data.use_memory and data.mode not in {"support", "meeting"}, "orchestration": data.orchestration, "status": "draft", "created_at": now(), "updated_at": now()}
        with store.tx() as conn:
            store.put("conversation", obj, conn)
        return obj

    @app.get("/api/conversations/{key}")
    def get_conversation(key: str, user=Depends(current_user)):
        obj = accessible(key, user, "conv_")
        return {"conversation": obj, "messages": [m for m in store.all("message") if m["conversation_id"] == key], "runs": [r for r in store.all("run") if r["conversation_id"] == key]}

    @app.post("/api/conversations/{key}/messages", status_code=202)
    def send_message(key: str, data: MessageInput, user=Depends(current_user)):
        with store.tx() as conn:
            conv = accessible(key, user, "conv_", conn)
            if data.finance:
                if conv["mode"] != "finance":
                    raise HTTPException(422, "预算数据仅可在理财会话中提交")
                try:
                    calculate_budget(data.finance.model_dump())
                except ValueError as exc:
                    raise HTTPException(422, str(exc))
            if conv["mode"] == "support" and any(c["capability"] != "support" for c in conv["character_snapshots"]):
                raise HTTPException(422, "情绪支持场景请选择支持伙伴，避免角色偏离")
            try:
                run = create_run(store, conv, user, data, conn, harness.gateway)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
        return run

    @app.get("/api/runs/{key}")
    def get_run(key: str, user=Depends(current_user)):
        return run_for(key, user)

    @app.get("/api/runs/{key}/events")
    def get_events(key: str, after: int = 0, user=Depends(current_user)):
        run_for(key, user)
        return store.events(key, after)

    @app.get("/api/runs/{key}/stream")
    async def stream(key: str, request: Request, after: int = 0, user=Depends(current_user)):
        run_for(key, user)
        async def generate():
            cursor = after
            for _ in range(120):
                if await request.is_disconnected():
                    break
                try:
                    live_user = current_user(request)
                except HTTPException:
                    break
                run = store.get(key)
                conv = store.get(run["conversation_id"]) if run else None
                if not can_access(live_user, conv):
                    break
                events = store.events(key, cursor)
                for event in events:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: update\ndata: {dump(event)}\n\n"
                yield ": heartbeat\n\n"
                if run["status"] not in ACTIVE:
                    break
                await asyncio.sleep(0.5)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/runs/{key}/{operation}")
    def control(key: str, operation: str, data: ResumeInput, user=Depends(current_user)):
        if operation not in {"pause", "resume", "cancel"}:
            raise HTTPException(404, "操作不存在")
        with store.tx() as conn:
            run = run_for(key, user, conn)
            if run["owner_id"] != user["id"]:
                raise HTTPException(403, "仅发起人可控制运行")
            if operation == "resume":
                if run["status"] not in {"paused", "exhausted", "failed"}:
                    raise HTTPException(409, "当前状态不能恢复")
                if data.max_calls is not None:
                    if data.max_calls <= run["calls"]:
                        raise HTTPException(422, "总调用上限必须大于已用次数")
                    run["max_calls"] = data.max_calls
                if run["calls"] >= run["max_calls"]:
                    raise HTTPException(422, "请先提高总调用上限")
                # Lease invalidation prevents an old in-flight model response from committing.
                run.update(status="queued", worker=None, lease_until=0, retry_count=0, error=None)
            elif operation == "pause":
                if run["status"] not in ACTIVE:
                    raise HTTPException(409, "当前状态不能暂停")
                run.update(status="paused", worker=None, lease_until=0)
            else:
                if run["status"] in {"succeeded", "cancelled"}:
                    raise HTTPException(409, "运行已经结束")
                run.update(status="cancelled", worker=None, lease_until=0)
            run["updated_at"] = now()
            store.put("run", run, conn)
            store.event(key, "run." + operation, {"checkpoint": run["cursor"], "by": user["name"]}, conn)
        return run

    @app.post("/api/decisions/{key}/acceptances")
    def accept(key: str, data: Acceptance, user=Depends(current_user)):
        with store.tx() as conn:
            run = run_for(key, user, conn)
            if run["status"] != "waiting_approval":
                raise HTTPException(409, "草案已处理或不再有效")
            if run["proposal"].get("kind") not in {"decision", "tasks"}:
                raise HTTPException(409, "这是旧版草案，请发送新消息重新生成后确认")
            if user["id"] not in run["proposal"]["required_approvers"]:
                raise HTTPException(403, "此任务草案仅由任务接收人确认")
            if data.version != run["version"] or propose_hash(run) != run["proposal"]["hash"]:
                raise HTTPException(409, "草案版本已变更，请刷新后确认")
            if run["proposal"]["expires_at"] < time.time():
                raise HTTPException(409, "草案已过期，请重新讨论")
            run["approvals"][user["id"]] = data.accepted
            store.event(key, "decision.accepted" if data.accepted else "decision.declined", {"by": user["name"], "version": data.version}, conn)
            if not data.accepted:
                run["status"] = "waiting_user"
            elif all(run["approvals"].get(p) is True for p in run["proposal"]["required_approvers"]):
                if run["proposal"]["kind"] == "decision":
                    run["decision"] = {**run["proposal"], "approvals": dict(run["approvals"]), "confirmed_at": now()}
                    store.event(key, "decision.confirmed", {"note": "只确认讨论结论，尚未创建任务"}, conn)
                else:
                    task_ids = []
                    for index, title in enumerate(run["proposal"]["actions"]):
                        task = {"id": f"task_{key}_{index}", "owner_id": run["owner_id"], "title": title, "status": "todo", "due_at": None, "source_run": key, "created_at": now(), "notified": False}
                        store.put("task", task, conn)
                        task_ids.append(task["id"])
                    run["created_task_ids"] = task_ids
                    if run["mode"] == "planning" or run.get("intent", {}).get("name") == "planning":
                        plan = {"id": "plan_" + key, "owner_id": run["owner_id"], "title": run["prompt"][:100], "source_run": key,
                                "conversation_id": run["conversation_id"], "milestones": run["results"][-1].get("proposals", []), "version": 1, "created_at": now()}
                        store.put("plan", plan, conn)
                    store.event(key, "tool.completed", {"name": "task.create_batch", "count": len(task_ids), "idempotency_key": key}, conn)
                run["status"] = "succeeded"
                conv = store.get(run["conversation_id"], conn)
                conv["status"] = "concluded" if run.get("decision") else "active"
                store.put("conversation", conv, conn)
                store.event(key, "run.completed", {"kind": run["proposal"]["kind"], "reason": "所需成员已确认"}, conn)
            store.put("run", run, conn)
        return run

    @app.post("/api/runs/{key}/tasks/proposal")
    def convert_to_tasks(key: str, user=Depends(current_user)):
        with store.tx() as conn:
            run = run_for(key, user, conn)
            if run["owner_id"] != user["id"]:
                raise HTTPException(403, "只有发起人可以为自己整理任务草案")
            siblings = [r for r in store.all("run", conn) if r["conversation_id"] == run["conversation_id"]]
            if not siblings or siblings[-1]["id"] != key:
                raise HTTPException(409, "已有后续讨论，请使用最新回复")
            if run["status"] == "waiting_approval" and run.get("proposal", {}).get("kind") == "tasks":
                return run
            if run["status"] != "succeeded" or run.get("created_task_ids"):
                raise HTTPException(409, "本次运行尚未结束或已经创建任务")
            if run["mode"] == "meeting" and not run.get("decision"):
                raise HTTPException(409, "请先由参与成员确认讨论结论")
            if not run.get("intent", {}).get("allow_actions"):
                raise HTTPException(409, "本次是问答、倾听或明确不创建任务；如需任务请发送新的明确请求")
            actions = run["results"][-1].get("suggested_actions", []) if run["results"] else []
            if not actions:
                raise HTTPException(409, "这条回复没有可转换的行动项")
            run["version"] += 1
            set_proposal(run, "tasks", run["results"][-1]["summary"], actions)
            store.put("run", run, conn)
            store.event(key, "tasks.proposed", {"version": run["version"], "owner": user["name"], "count": len(actions)}, conn)
        return run

    @app.post("/api/tasks", status_code=201)
    def add_task(data: TaskInput, user=Depends(current_user)):
        due_at = None
        if data.due_at:
            try:
                date = datetime.fromisoformat(data.due_at.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    raise ValueError()
                due_at = date.astimezone(timezone.utc).isoformat()
            except ValueError:
                raise HTTPException(422, "提醒时间需包含时区")
        task = {"id": uid("task_"), "owner_id": user["id"], "title": data.title.strip(), "status": "todo", "due_at": due_at, "created_at": now(), "notified": False}
        with store.tx() as conn:
            store.put("task", task, conn)
        return task

    @app.patch("/api/tasks/{key}")
    def update_task(key: str, data: TaskUpdate, user=Depends(current_user)):
        with store.tx() as conn:
            task = accessible(key, user, "task_", conn)
            task["status"] = data.status
            store.put("task", task, conn)
        return task

    @app.post("/api/memories", status_code=201)
    def add_memory(data: MemoryInput, user=Depends(current_user)):
        obj = {"id": uid("mem_"), "owner_id": user["id"], "content": data.content.strip(), "scope": "private", "source": "用户主动保存", "created_at": now()}
        with store.tx() as conn:
            store.put("memory", obj, conn)
        return obj

    @app.delete("/api/memories/{key}")
    def delete_memory(key: str, user=Depends(current_user)):
        with store.tx() as conn:
            accessible(key, user, "mem_", conn)
            # No vector/cache copies in v0.1. Historical generated messages are conversation records.
            conn.execute("DELETE FROM records WHERE id=?", (key,))
        return {"ok": True}

    @app.post("/api/finance/calculate")
    def calculate(data: FinanceInput, user=Depends(current_user)):
        try:
            return calculate_budget(data.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/notifications/{key}/read")
    def read_notification(key: str, user=Depends(current_user)):
        with store.tx() as conn:
            obj = accessible(key, user, "reminder_", conn)
            obj["read"] = True
            store.put("notification", obj, conn)
        return {"ok": True}

    dist = ROOT / "dist"
    if (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets", check_dir=False), name="assets")
        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")
        @app.get("/favicon.svg")
        def favicon():
            return FileResponse(dist / "favicon.svg", media_type="image/svg+xml")

    return app


app = create_app(start_worker=os.getenv("FAMILYAGENT_EMBEDDED_WORKER", "1") == "1")
