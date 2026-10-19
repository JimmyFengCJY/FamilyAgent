from .auth import hash_password


CHARACTERS = [
    {"id": "host", "name": "知和", "capability": "host", "tone": "平和、中立，给每一种声音留空间", "description": "会议主持人 · 让讨论有方向，让分歧被看见", "color": "sage", "initial": "和"},
    {"id": "planner", "name": "远山", "capability": "planner", "tone": "理性、具体，善于把大目标拆成小步骤", "description": "成长规划师 · 陪你走好下一小步", "color": "lavender", "initial": "山"},
    {"id": "support", "name": "小暖", "capability": "support", "tone": "温暖、耐心，先倾听再建议，不评判", "description": "情绪支持伙伴 · 有些心事，可以慢慢说", "color": "peach", "initial": "暖"},
    {"id": "finance", "name": "明理", "capability": "finance", "tone": "审慎、清晰，区分事实、假设与预测", "description": "财务分析师 · 把生活的账算明白", "color": "blue", "initial": "理"},
    {"id": "assistant", "name": "拾光", "capability": "assistant", "tone": "轻快、周到，优先给出能马上做的事情", "description": "生活助手 · 接住日常的小事", "color": "rose", "initial": "光"},
]


def seed(store, demo=True):
    with store.tx() as conn:
        for char in CHARACTERS:
            if not store.get(char["id"], conn):
                store.put("character", {**char, "version": 1, "owner_id": "system", "builtin": True}, conn)
        if demo:
            for user_id, email, name, family in [("demo-jimmy", "jimmy@demo.local", "Jimmy", "demo-family"), ("demo-lin", "lin@demo.local", "林悦", "demo-family")]:
                if not store.user(user_id, conn):
                    conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user_id, email, name, hash_password("family-demo-local"), family))
