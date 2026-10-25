"""Small labelled lexical-retrieval regression, with no model or production DB."""
import argparse
import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from services.api.app.db import Store
from services.api.app.memory import retrieve

DOCUMENTS = [
    ('english', '英语口语练习每天早上二十分钟'),
    ('travel', '旅行住宿选择安静房间，老人每天最多一个景点'),
    ('python', 'Python testing preferences: pytest and fixtures'),
] + [(f'coffee{i}', f'咖啡口味记录{i}：不加糖') for i in range(7)]
CASES = [
    {'id': 'old-english', 'query': '英语口语', 'expected': ['english']},
    {'id': 'old-travel', 'query': '旅行住宿', 'expected': ['travel']},
    {'id': 'normalized-english', 'query': 'ＰＹＴＨＯＮ pytest', 'expected': ['python']},
    {'id': 'no-match', 'query': '量子物理', 'expected': []},
    {'id': 'stop-terms', 'query': '帮我 please', 'expected': []},
    {'id': 'other-owner', 'query': '摄影', 'expected': []},
    {'id': 'disabled', 'query': '英语', 'use_memory': False, 'expected': []},
    {'id': 'shared', 'query': '旅行', 'scope': 'family', 'mode': 'meeting', 'expected': []},
    {'id': 'support', 'query': '英语', 'mode': 'support', 'expected': []},
]


def evaluate():
    rows = []
    with tempfile.TemporaryDirectory(prefix='familyagent-memory-eval-') as temp:
        store = Store(Path(temp) / 'eval.sqlite3')
        with store.tx() as conn:
            for key, content in DOCUMENTS:
                store.put('memory', {'id': key, 'owner_id': 'synthetic-user', 'scope': 'private', 'content': content}, conn)
            store.put('memory', {'id': 'other', 'owner_id': 'synthetic-other', 'scope': 'private', 'content': '摄影私密偏好'}, conn)
            for case in CASES:
                conv = {'scope': 'private', 'mode': 'planning', 'use_memory': True, **case}
                hits, receipt = retrieve(store, {'id': 'synthetic-user'}, conv, case['query'], conn)
                authorized = conv['scope'] == 'private' and conv['mode'] != 'support' and conv['use_memory']
                baseline = [row['id'] for row in store.private_memories('synthetic-user', conn)[-6:]] if authorized else []
                actual = [row['id'] for row in hits]
                rows.append({'case_id': case['id'], 'expected': case['expected'], 'recent6': baseline, 'lexical': actual,
                             'recent6_exact': set(baseline) == set(case['expected']), 'lexical_exact': set(actual) == set(case['expected']),
                             'receipt': receipt})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线记忆检索回归，不调用模型')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    directory = args.output or Path(__file__).parent / 'results' / ('memory-' + datetime.now(ZoneInfo('Asia/Hong_Kong')).strftime('%Y%m%d-%H%M%S-%f'))
    directory.mkdir(parents=True, exist_ok=False)
    rows = evaluate()
    artifact = {'scope': 'synthetic lexical regression; not answer-quality evaluation',
                'dataset_sha256': hashlib.sha256(json.dumps([DOCUMENTS, CASES], ensure_ascii=False, sort_keys=True).encode()).hexdigest(), 'rows': rows}
    (directory / 'results.json').write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    lines = ['# 记忆检索离线对照', '', '9 个手工标注的合成用例；只验证来源集合，不验证真实语义泛化或回答质量。无模型调用。', '',
             '| 用例 | 最近6条完全匹配 | 词面检索完全匹配 |', '|---|---|---|']
    lines += [f"| {r['case_id']} | {r['recent6_exact']} | {r['lexical_exact']} |" for r in rows]
    lines += ['', f"来源集合完全匹配：最近6条 {sum(r['recent6_exact'] for r in rows)}/{len(rows)}；词面检索 {sum(r['lexical_exact'] for r in rows)}/{len(rows)}。",
              '', '小样本回归含人为设置的旧相关记忆和新无关记录，不据此宣称真实检索准确率、模型质量或费用提升。预算裁剪与删除行为由集成测试另行验证。']
    (directory / 'report.md').write_text('\n'.join(lines) + '\n')
    print(directory / 'report.md')
    return 0 if all(row['lexical_exact'] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
