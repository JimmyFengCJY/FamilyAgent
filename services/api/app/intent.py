"""Conservative per-message routing. A role/space never grants write intent."""
import re

INTENT_NAMES = {"question": "解释问答", "support": "倾听交流", "advice": "提供建议",
                "planning": "制定计划", "task": "准备任务", "discussion": "共同讨论"}


def resolve_intent(text, mode, requested="auto"):
    no_tasks = bool(re.search(r"(?:不要|不用|别|不想|不需要|无需)[^，。；,;\n]{0,8}(?:生成|创建|添加|保存|安排|制定|任务|待办|计划)|只(?:想|需|要)?(?:聊聊|倾诉|听我|讨论|解释|了解)", text))
    if requested != "auto":
        name, reason = requested, "用户明确选择"
    elif re.search(r"(?:只想|先|只要).{0,5}(?:聊聊|说说|倾诉|倾听)|不想.{0,8}(?:建议|计划)|听我说|想(?:找人)?聊(?:聊|一聊)", text):
        name, reason = "support", "明确希望倾听"
    elif re.search(r"(?:什么是|是什么|为什么|如何|怎么|解释|介绍|区别|含义|原理|说明一下)", text):
        name, reason = "question", "解释或知识问答"
    elif re.search(r"(?:创建|添加|保存|转成|转为|记为|列成|列为|设为|变成).{0,12}(?:任务|待办)|(?:提醒我|帮我记下)", text):
        name, reason = "task", "明确请求任务"
    elif re.search(r"(?:制定|做一?[个份下]?|拟定|安排|设计|重排).{0,12}(?:计划|日程|行程|学习|旅行)|帮我规划|重新安排|(?:我想|希望|目标).{0,25}(?:提升|养成|完成|达到)", text):
        name, reason = "planning", "明确请求规划"
    elif mode == "meeting":
        name, reason = "discussion", "会议讨论默认只确认结论"
    elif mode == "support":
        name, reason = "support", "私密交流默认倾听"
    else:
        name, reason = "advice", "需求未明确，先建议不写入"
    return {"name": name, "label": INTENT_NAMES[name], "source": "user" if requested != "auto" else "rules",
            "reason": reason, "allow_actions": not no_tasks and name not in {"question", "support"},
            "auto_task_proposal": not no_tasks and name in {"planning", "task"}, "policy_version": "intent-v1"}


def filter_result(result, intent):
    """Enforce server policy even if the model ignores the prompt."""
    result = dict(result)
    result["suggested_actions"] = list(dict.fromkeys(action.strip()[:150] for action in result.get("suggested_actions", []) if action.strip()))[:6] if intent["allow_actions"] else []
    return result
