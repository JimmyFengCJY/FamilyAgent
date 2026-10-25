"""Deterministic context packing; estimates are not provider token counts."""
import copy
import json
import math

POLICY = {"version": "context-v1", "max_input_chars": 32000}


class ContextOverflow(ValueError):
    pass


def measure(context, system):
    text = system + json.dumps(context, ensure_ascii=False)
    return {"input_chars": len(text), "estimated_tokens": math.ceil(len(text.encode('utf-8')) / 3)}


def pack_context(context, system, policy=None):
    policy = policy or POLICY
    if policy['version'] != POLICY['version']:
        raise ContextOverflow('上下文策略版本不可用，请开启新运行')
    limit = policy['max_input_chars']
    packed = copy.deepcopy(context)
    before = measure(packed, system)
    dropped = {key: 0 for key in ('history', 'memories', 'prior_results')}
    # Keep current prompt, explicit constraints, role, intent, tool/finance data,
    # and attributed expert findings intact. Remove optional old content first.
    for key in dropped:
        while packed.get(key) and measure(packed, system)['input_chars'] > limit:
            packed[key].pop(-1 if key == 'memories' else 0)
            dropped[key] += 1
    after = measure(packed, system)
    if after['input_chars'] > limit:
        raise ContextOverflow('关键上下文超过输入字符预算，未调用模型；请缩短问题或拆分讨论')
    return packed, {"policy": policy['version'], "max_input_chars": limit,
                    "before_chars": before['input_chars'], **after, "dropped": dropped,
                    "constraint_count": len(packed.get('constraints', [])),
                    "token_estimator": "ceil(utf8_bytes/3); estimate only; excludes provider framing"}
