import time
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from services.api.app.main import create_app
from services.api.app.harness import Harness
from services.api.app.models import ModelGateway, InvalidModelOutput
from services.api.app.schemas import AgentResult


@pytest.fixture
def setup(tmp_path):
    app = create_app(tmp_path / "test.sqlite", start_worker=False, demo=True)
    harness = app.state.harness
    harness.delay = 0
    jimmy, lin, outsider = TestClient(app), TestClient(app), TestClient(app)
    assert jimmy.post("/api/auth/demo").status_code == 200
    assert lin.post("/api/auth/demo?account=demo-lin").status_code == 200
    response = outsider.post("/api/auth/register", json={"name": "外部成员", "email": "other@example.com", "password": "example-password"})
    assert response.status_code == 201
    return app, harness, jimmy, lin, outsider


def conversation(client, mode="assistant", **extra):
    response = client.post("/api/conversations", json={"title": "验证空间", "mode": mode, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def send(client, conv, **extra):
    response = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "安排一次家庭旅行，预算8000元", **extra})
    assert response.status_code == 202, response.text
    return response.json()


def drain(harness, client, run):
    for _ in range(30):
        harness.tick()
        result = client.get(f"/api/runs/{run['id']}").json()
        if result["status"] not in {"running", "queued"}:
            return result
    pytest.fail("运行未按预算停止")


def test_meeting_requires_every_real_member_and_is_idempotent(setup):
    app, harness, jimmy, lin, _ = setup
    conv = conversation(jimmy, "meeting", participants=["demo-lin"])
    run = drain(harness, jimmy, send(jimmy, conv))
    assert run["status"] == "waiting_approval"
    assert run["cursor"] == 5
    assert not jimmy.get("/api/state").json()["tasks"]
    url = f"/api/decisions/{run['id']}/acceptances"
    assert jimmy.post(url, json={"version": 1}).json()["status"] == "waiting_approval"
    assert not jimmy.get("/api/state").json()["tasks"]
    assert lin.post(url, json={"version": 1}).json()["status"] == "succeeded"
    count = len(jimmy.get("/api/state").json()["tasks"])
    assert count == len(run["proposal"]["actions"])
    assert lin.post(url, json={"version": 1}).status_code == 409
    assert len(jimmy.get("/api/state").json()["tasks"]) == count
    assert not lin.get("/api/state").json()["tasks"]


def test_private_resources_are_inaccessible_to_family_and_outsider(setup):
    _, harness, jimmy, lin, outsider = setup
    conv = conversation(jimmy, "support")
    run = drain(harness, jimmy, send(jimmy, conv))
    memory = jimmy.post("/api/memories", json={"content": "不公开的个人偏好"}).json()
    task = jimmy.post("/api/tasks", json={"title": "私密任务"}).json()
    for other in [lin, outsider]:
        assert other.get(f"/api/conversations/{conv['id']}").status_code == 404
        assert other.get(f"/api/runs/{run['id']}").status_code == 404
        assert other.get(f"/api/runs/{run['id']}/events").status_code == 404
        assert other.get(f"/api/runs/{run['id']}/stream").status_code == 404
        assert other.post(f"/api/conversations/{conv['id']}/messages", json={"content": "越权"}).status_code == 404
        assert other.delete(f"/api/memories/{memory['id']}").status_code == 404
        assert other.patch(f"/api/tasks/{task['id']}", json={"status": "done"}).status_code == 404
        data = other.get("/api/state").json()
        assert not any(c["id"] == conv["id"] for c in data["conversations"])
        assert not data["memories"]


def test_shared_meeting_is_restricted_to_invited_members(setup):
    _, _, jimmy, lin, outsider = setup
    conv = conversation(jimmy, "meeting", participants=[])
    assert lin.get(f"/api/conversations/{conv['id']}").status_code == 404
    other_id = outsider.get("/api/state").json()["user"]["id"]
    assert jimmy.post("/api/conversations", json={"title": "错误邀请", "mode": "meeting", "participants": [other_id]}).status_code == 422


def test_budget_exhaustion_resume_keeps_checkpoint(setup):
    _, harness, jimmy, _, _ = setup
    conv = conversation(jimmy, "meeting")
    run = drain(harness, jimmy, send(jimmy, conv, max_calls=1))
    assert run["status"] == "exhausted" and run["cursor"] == 1
    assert jimmy.post(f"/api/runs/{run['id']}/resume", json={"max_calls": 1}).status_code == 422
    assert jimmy.post(f"/api/runs/{run['id']}/resume", json={"max_calls": 8}).status_code == 200
    resumed = drain(harness, jimmy, run)
    assert resumed["status"] == "waiting_approval" and resumed["calls"] == 5
    messages = jimmy.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert len([m for m in messages if m.get("stage") == "opening"]) == 1


def test_pause_stop_and_no_extra_side_effects(setup):
    _, harness, jimmy, _, _ = setup
    conv = conversation(jimmy, "meeting")
    run = send(jimmy, conv)
    assert jimmy.post(f"/api/runs/{run['id']}/pause", json={}).json()["status"] == "paused"
    assert not harness.tick()
    assert jimmy.post(f"/api/conversations/{conv['id']}/messages", json={"content": "新消息"}).status_code == 409
    assert jimmy.post(f"/api/runs/{run['id']}/resume", json={}).status_code == 200
    harness.tick()
    assert jimmy.post(f"/api/runs/{run['id']}/cancel", json={}).json()["status"] == "cancelled"
    assert not harness.tick()
    assert not jimmy.get("/api/state").json()["tasks"]


def test_reclaim_after_worker_crash(setup):
    app, harness, jimmy, _, _ = setup
    conv = conversation(jimmy, "meeting")
    run = send(jimmy, conv)
    harness.tick()
    with app.state.store.tx() as conn:
        value = app.state.store.get(run["id"], conn)
        value.update(status="running", worker="dead-worker", lease_until=0)
        app.state.store.put("run", value, conn)
    replacement = Harness(app.state.store, delay=0)
    result = drain(replacement, jimmy, run)
    assert result["status"] == "waiting_approval" and result["calls"] == 5
    events = jimmy.get(f"/api/runs/{run['id']}/events").json()
    assert any(e["type"] == "run.recovered" for e in events)


@pytest.mark.parametrize("income,items,expenses,remaining", [
    ("0.30", [{"name": "a", "amount": "0.10"}, {"name": "b", "amount": "0.20"}], "0.30", "0.00"),
    ("100", [{"name": "a", "amount": "100.005"}], "100.01", "-0.01"),
    ("0", [{"name": "a", "amount": "20"}], "20.00", "-20.00"),
])
def test_finance_uses_decimal(setup, income, items, expenses, remaining):
    _, _, client, _, _ = setup
    response = client.post("/api/finance/calculate", json={"income": income, "items": items})
    assert response.status_code == 200
    assert response.json()["expenses"] == expenses
    assert response.json()["remaining"] == remaining


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "1000000001", "bad"])
def test_invalid_amounts_fail_before_run_created(setup, amount):
    _, _, client, _, _ = setup
    conv = conversation(client, "finance")
    response = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "计算", "finance": {"income": amount, "items": [{"name": "a", "amount": "1"}]}})
    assert response.status_code == 422
    assert client.get(f"/api/conversations/{conv['id']}").json()["messages"] == []


def test_finance_tool_is_recorded_and_result_persisted(setup):
    _, harness, client, _, _ = setup
    conv = conversation(client, "finance")
    run = drain(harness, client, send(client, conv, finance={"income": "20000", "items": [{"name": "房租", "amount": "6500"}]}))
    assert run["finance_result"]["remaining"] == "13500.00"
    assert any(e["data"].get("name") == "finance.calculate" for e in client.get(f"/api/runs/{run['id']}/events").json())


def test_character_versions_are_snapshotted(setup):
    _, harness, client, lin, _ = setup
    data = {"name": "测试规划师", "capability": "planner", "tone": "温和", "color": "sage"}
    char = client.post("/api/characters", json=data).json()
    conv = conversation(client, "meeting", character_ids=[char["id"]], participants=["demo-lin"])
    assert client.put(f"/api/characters/{char['id']}", json={**data, "tone": "严格"}).json()["version"] == 2
    run = send(lin, conv)
    assert run["steps"][1]["character"]["version"] == 1
    assert run["steps"][1]["character"]["tone"] == "温和"
    assert lin.put(f"/api/characters/{char['id']}", json=data).status_code == 404


def test_invitation_is_single_use_and_joins_family(setup):
    app, _, jimmy, _, outsider = setup
    invite = jimmy.post("/api/invitations").json()["token"]
    fresh = TestClient(app)
    data = {"email": "new@example.com", "password": "example-password", "name": "新成员", "invite": invite}
    assert fresh.post("/api/auth/register", json=data).status_code == 201
    assert fresh.get("/api/state").json()["user"]["household"] == "demo-family"
    assert outsider.post("/api/auth/register", json={**data, "email": "twice@example.com"}).status_code == 422


def test_memory_never_crosses_to_meeting_or_support(setup):
    app, harness, client, _, _ = setup
    client.post("/api/memories", json={"content": "只属于个人的偏好"})
    captured = []
    original = harness.gateway.generate
    harness.gateway.generate = lambda context: (captured.append(context) or original(context))
    for mode in ["planning", "meeting", "support"]:
        conv = conversation(client, mode, use_memory=True)
        drain(harness, client, send(client, conv))
    assert captured[0]["memories"][0]["content"] == "只属于个人的偏好"
    assert all(not c["memories"] for c in captured if c["mode"] in {"meeting", "support"})


def test_decline_and_supersede_are_not_fake_consensus(setup):
    _, harness, client, lin, _ = setup
    conv = conversation(client, "meeting", participants=["demo-lin"])
    run = drain(harness, client, send(client, conv))
    assert lin.post(f"/api/decisions/{run['id']}/acceptances", json={"version": 1, "accepted": False}).json()["status"] == "waiting_user"
    assert not client.get("/api/state").json()["tasks"]
    newer = drain(harness, client, send(client, conv))
    send(client, conv)
    assert client.get(f"/api/runs/{newer['id']}").json()["status"] == "cancelled"
    assert client.post(f"/api/decisions/{newer['id']}/acceptances", json={"version": 1}).status_code == 409


def test_expired_or_tampered_proposal_cannot_execute(setup):
    app, harness, client, _, _ = setup
    conv = conversation(client)
    run = drain(harness, client, send(client, conv))
    url = f"/api/decisions/{run['id']}/acceptances"
    assert client.post(url, json={"version": 2}).status_code == 409
    with app.state.store.tx() as conn:
        row = app.state.store.get(run["id"], conn)
        row["proposal"]["actions"].append("篡改任务")
        app.state.store.put("run", row, conn)
    assert client.post(url, json={"version": 1}).status_code == 409
    assert not client.get("/api/state").json()["tasks"]


def test_reminder_delivery_is_durable_and_deduplicated(setup):
    _, harness, client, lin, _ = setup
    due = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    task = client.post("/api/tasks", json={"title": "该喝水了", "due_at": due}).json()
    harness.deliver_reminders(); harness.deliver_reminders()
    reminders = client.get("/api/state").json()["notifications"]
    assert len(reminders) == 1 and reminders[0]["content"] == "该喝水了"
    assert not lin.get("/api/state").json()["notifications"]
    assert client.post(f"/api/notifications/{reminders[0]['id']}/read").status_code == 200


def test_auth_csrf_and_whitespace_validation(setup):
    app, _, client, _, _ = setup
    assert TestClient(app).get("/api/state").status_code == 401
    assert client.post("/api/tasks", headers={"Origin": "https://evil.example"}, json={"title": "跨站"}).status_code == 403
    assert client.post("/api/tasks", json={"title": "   "}).status_code == 422
    assert client.post("/api/tasks", json={"title": "提醒", "due_at": "2026-10-13T14:00"}).status_code == 422
    client.post("/api/auth/logout")
    assert client.get("/api/state").status_code == 401


def test_model_tool_requests_are_bounded_and_authorized(setup):
    _, harness, client, _, _ = setup
    class ToolGateway:
        provider = "demo"
        def generate(self, context):
            if not context["tool_results"]:
                return {"tool_request": {"name": "task.list"}}, {"provider": "demo"}
            return AgentResult(summary="已查看授权任务").model_dump(), {"provider": "demo"}
    harness.gateway = ToolGateway()
    client.post("/api/tasks", json={"title": "个人任务"})
    run = drain(harness, client, send(client, conversation(client)))
    assert run["status"] == "succeeded" and run["calls"] == 2
    assert run["tool_results"][0]["result"][0]["title"] == "个人任务"
    denied = drain(harness, client, send(client, conversation(client, "support")))
    assert denied["status"] == "failed" and not denied["tool_results"]


def test_format_retry_counts_against_budget(setup):
    _, harness, client, _, _ = setup
    original = harness.gateway.generate
    attempts = []
    def flaky(context):
        attempts.append(1)
        if len(attempts) == 1:
            raise InvalidModelOutput("invalid")
        return original(context)
    harness.gateway.generate = flaky
    run = drain(harness, client, send(client, conversation(client)))
    assert run["calls"] == 2 and run["cursor"] == 1


def test_cancel_during_model_call_discards_late_result(setup):
    _, harness, client, _, _ = setup
    conv = conversation(client)
    run = send(client, conv)
    original = harness.gateway.generate
    def late(context):
        client.post(f"/api/runs/{run['id']}/cancel", json={})
        return original(context)
    harness.gateway.generate = late
    harness.tick()
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "cancelled"
    assert len(client.get(f"/api/conversations/{conv['id']}").json()["messages"]) == 1


def test_private_data_survives_restart_and_demo_disable(setup):
    app, _, client, _, _ = setup
    client.post("/api/memories", json={"content": "持久记录"})
    other_app = create_app(app.state.store.path, start_worker=False, demo=True)
    other = TestClient(other_app)
    other.post("/api/auth/demo")
    assert other.get("/api/state").json()["memories"][0]["content"] == "持久记录"
    disabled = TestClient(create_app(app.state.store.path, start_worker=False, demo=False))
    assert disabled.post("/api/auth/demo").status_code == 403
    assert disabled.post("/api/auth/login", json={"email": "jimmy@demo.local", "password": "family-demo-local"}).status_code == 401


def test_confirmed_plan_is_persistent_and_personal(setup):
    _, harness, client, lin, _ = setup
    conv = conversation(client, "planning")
    run = drain(harness, client, send(client, conv))
    assert not client.get("/api/state").json()["plans"]
    response = client.post(f"/api/decisions/{run['id']}/acceptances", json={"version": 1})
    assert response.json()["status"] == "succeeded"
    plans = client.get("/api/state").json()["plans"]
    assert len(plans) == 1 and plans[0]["milestones"]
    assert plans[0]["source_run"] == run["id"]
    assert not lin.get("/api/state").json()["plans"]
