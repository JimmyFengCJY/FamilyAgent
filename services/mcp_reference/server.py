"""Independent read-only MCP server. No app DB, credentials or network clients."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.mcp_reference.contracts import ReferenceOutput
from typing import Annotated
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP('familyagent-reference', log_level='ERROR')
DOCUMENTS = [
    {'id': 'goal', 'title': '目标拆解', 'excerpt': '先写出可观察的目标、截止时间与资源限制，再拆成本周可完成的小步骤。'},
    {'id': 'review', 'title': '学习复盘', 'excerpt': '记录本周实际投入的时间、完成内容与障碍，根据记录调整下一周安排。'},
    {'id': 'meeting', 'title': '家庭会议议程', 'excerpt': '先分别收集成员约束，再比较方案，保留分歧与待澄清问题，结论和个人任务分别确认。'},
]


def search(query, limit):
    # Deliberately transparent lexical lookup in a tiny authored corpus.
    text = query.lower()
    bigrams = {text[i:i+2] for i in range(len(text)-1) if text[i:i+2].strip()}
    rows = []
    for doc in DOCUMENTS:
        content = doc['title'] + doc['excerpt']
        score = sum(term in content for term in bigrams)
        if score:
            rows.append((score, doc))
    rows.sort(key=lambda item: (-item[0], item[1]['id']))
    return {'hits': [{**doc, 'source': 'familyagent://reference/' + doc['id']} for _, doc in rows[:limit]]}


@mcp.tool(structured_output=True, annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def search_reference(query: Annotated[str, Field(min_length=2, max_length=120)],
                     limit: Annotated[int, Field(ge=1, le=5)] = 3) -> ReferenceOutput:
    """检索项目内置的目标、学习复盘和家庭会议参考资料，不包含个人记录。"""
    return ReferenceOutput.model_validate(search(query, limit))


if __name__ == '__main__':
    mcp.run(transport='stdio')
