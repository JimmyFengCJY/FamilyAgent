import pytest
from services.api.app.intent import resolve_intent
from services.api.app.schemas import AgentResult
from tests.test_system import setup, conversation, send, drain


@pytest.mark.parametrize("text,mode,expected,automatic", [
    ("什么是上下文管理？", "assistant", "question", False),
    ("解释一下任务清单如何生成", "planning", "question", False),
    ("今天我只想倾诉，不想听建议", "assistant", "support", False),
    ("我很累，想找人聊一聊", "assistant", "support", False),
    ("请给一些阅读建议", "assistant", "advice", False),
    ("帮我制定英语学习计划", "assistant", "planning", True),
    ("创建三个整理房间的待办", "assistant", "task", True),
    ("帮我制定计划，但不要生成任务", "planning", "planning", False),
    ("讨论本周家庭分工", "meeting", "discussion", False),
    ("家庭会议是什么？", "meeting", "question", False),
    ("你好", "planning", "advice", False),
    ("给我一些读书建议", "planning", "advice", False),
])
def test_conservative_intent_rules(text, mode, expected, automatic):
    intent = resolve_intent(text, mode)
    assert intent["name"] == expected
    assert intent["auto_task_proposal"] is automatic


@pytest.mark.parametrize("mode,content,choice", [
    ("assistant", "解释上下文管理", "auto"),
    ("planning", "计划应该怎么制定？", "auto"),
    ("support", "我只想说说，不想要建议", "auto"),
    ("meeting", "解释什么是家庭会议", "auto"),
    ("assistant", "帮我制定计划，但不要创建任务", "auto"),
    ("assistant", "帮我安排旅行", "question"),
])
def test_model_cannot_force_actions_against_user_intent(setup, mode, content, choice):
    _, harness, client, _, _ = setup
    def overeager(context):
        return AgentResult(summary="这是回答", proposals=["一条文字建议"], suggested_actions=["强行创建任务"]).model_dump(), {"provider": "demo"}
    harness.gateway.generate = overeager
    run = drain(harness, client, send(client, conversation(client, mode), content=content, intent=choice))
    assert run["status"] == "succeeded"
    assert not run.get("proposal")
    if mode == "meeting":
        assert run["calls"] == 1
    assert all(not r["suggested_actions"] for r in run["results"])
    assert not client.get("/api/state").json()["tasks"]
    assert client.post(f"/api/runs/{run['id']}/tasks/proposal").status_code == 409


def test_advice_is_optional_then_explicit_preview_then_confirmation(setup):
    _, harness, client, _, _ = setup
    conv = conversation(client)
    run = drain(harness, client, send(client, conv, content="请给我一些整理房间的建议"))
    assert run["status"] == "succeeded" and not run.get("proposal")
    assert run["results"][-1]["suggested_actions"]
    endpoint = f"/api/runs/{run['id']}/tasks/proposal"
    draft = client.post(endpoint).json()
    assert draft["proposal"]["kind"] == "tasks"
    assert not client.get("/api/state").json()["tasks"]
    assert client.post(endpoint).json()["version"] == draft["version"]
    url = f"/api/decisions/{run['id']}/acceptances"
    assert client.post(url, json={"version": draft["version"]}).status_code == 200
    count = len(client.get("/api/state").json()["tasks"])
    assert count > 0
    assert client.post(url, json={"version": draft["version"]}).status_code == 409
    assert client.post(endpoint).status_code == 409
    assert len(client.get("/api/state").json()["tasks"]) == count


def test_planning_requires_user_approval_and_explicit_override_is_recorded(setup):
    _, harness, client, _, _ = setup
    run = drain(harness, client, send(client, conversation(client), content="整理今天的事情", intent="planning"))
    assert run["intent"]["source"] == "user"
    assert run["status"] == "waiting_approval"
    assert run["proposal"]["kind"] == "tasks"
    assert not client.get("/api/state").json()["tasks"]
    events = client.get(f"/api/runs/{run['id']}/events").json()
    assert any(e["type"] == "intent.resolved" for e in events)


def test_concluded_meeting_without_action_items_still_records_decision(setup):
    _, harness, client, _, _ = setup
    harness.gateway.generate = lambda context: (AgentResult(summary="保留两个方案，仍需补充资料").model_dump(), {"provider": "demo"})
    run = drain(harness, client, send(client, conversation(client, "meeting")))
    assert run["proposal"]["kind"] == "decision" and not run["proposal"]["actions"]
    assert client.post(f"/api/decisions/{run['id']}/acceptances", json={"version": 1}).json()["status"] == "succeeded"
    assert not client.get("/api/state").json()["tasks"]
    assert client.post(f"/api/runs/{run['id']}/tasks/proposal").status_code == 409


def test_new_discussion_invalidates_old_conversion(setup):
    _, harness, client, _, _ = setup
    conv = conversation(client)
    old = drain(harness, client, send(client, conv, content="给一些收纳建议"))
    drain(harness, client, send(client, conv, content="先解释收纳的含义"))
    assert client.post(f"/api/runs/{old['id']}/tasks/proposal").status_code == 409


def test_non_owner_cannot_convert_shared_decision(setup):
    _, harness, client, lin, outsider = setup
    run = drain(harness, client, send(client, conversation(client, "meeting", participants=["demo-lin"])))
    url = f"/api/runs/{run['id']}/tasks/proposal"
    assert client.post(url).status_code == 409
    assert lin.post(url).status_code == 403
    assert outsider.post(url).status_code == 404


def test_old_policy_proposal_must_not_execute_after_upgrade(setup):
    app, harness, client, _, _ = setup
    run = drain(harness, client, send(client, conversation(client)))
    with app.state.store.tx() as conn:
        value = app.state.store.get(run["id"], conn)
        value["proposal"].pop("kind")
        app.state.store.put("run", value, conn)
    assert client.post(f"/api/decisions/{run['id']}/acceptances", json={"version": 1}).status_code == 409
    assert not client.get("/api/state").json()["tasks"]
