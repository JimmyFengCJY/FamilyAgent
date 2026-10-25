"""Local lexical retrieval with authorization before ranking; no model calls."""
import hashlib
import math
import re
import unicodedata
from collections import Counter

POLICY = {'version': 'lexical-v1', 'limit': 6}
STOP = {'帮我', '请问', '一下', '如何', '什么', '今天', '现在', '可以', '建议', '计划',
        '需要', '关于', '一个', '一些', '我的', '我们', '你的', '你们', '帮忙', '给我', '制定',
        'the', 'and', 'for', 'with', 'this', 'that', 'please', 'help', 'my', 'me', 'to', 'is', 'a'}


def terms(text):
    text = unicodedata.normalize('NFKC', text).lower()
    result = set(re.findall(r'[a-z0-9]{2,}', text))
    for segment in re.findall(r'[\u4e00-\u9fff]+', text):
        result.update(segment[i:i + 2] for i in range(len(segment) - 1))
    return result - STOP


def fingerprint(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def retrieve(store, user, conversation, query, conn, policy=None):
    policy = policy or POLICY
    reason = ('shared_space' if conversation.get('scope') != 'private' else
              'support_disabled' if conversation['mode'] == 'support' else
              'not_enabled' if not conversation.get('use_memory') else None)
    receipt = {'policy': policy['version'], 'reason': reason or 'no_match', 'selected': 0}
    if reason:
        return [], receipt
    if policy['version'] != POLICY['version']:
        raise ValueError('记忆检索策略版本不可用，请开启新运行')
    # SQL filters owner/scope before content is ranked, including legacy private rows.
    candidates = store.private_memories(user['id'], conn)
    query_terms = terms(query)
    documents = [terms(row['content']) for row in candidates]
    frequency = Counter(term for document in documents for term in document)
    ranked = []
    for index, (row, document) in enumerate(zip(candidates, documents)):
        matches = query_terms & document
        if matches:
            score = sum(1 + math.log((len(documents) + 1) / (frequency[t] + 1)) for t in matches)
            ranked.append((score, index, row))
    ranked.sort(key=lambda item: (-item[0], -item[1]))
    selected = [{**row, 'retrieval_score': round(score, 4)} for score, _, row in ranked[:policy['limit']]]
    receipt.update(reason='matched' if selected else 'no_match', selected=len(selected))
    return selected, receipt


def references(memories):
    # No content copies in message metadata. The read API resolves current sources.
    return [{'id': row['id'], 'content_sha256': fingerprint(row['content']),
             'score': row['retrieval_score']} for row in memories]


def resolve_sources(store, refs, user, conversation):
    if conversation.get('scope') != 'private' or conversation['mode'] == 'support' or not conversation.get('use_memory'):
        return []
    sources = []
    for ref in refs:
        row = store.get(ref['id'])
        valid = row and row.get('owner_id') == user['id'] and row.get('scope', 'private') == 'private'
        source = {'id': ref['id'], 'score': ref['score'], 'status': 'unavailable'}
        if valid and fingerprint(row['content']) == ref['content_sha256']:
            source.update(status='available', content=row['content'], source=row.get('source', '用户保存的记忆'), created_at=row.get('created_at'))
        elif valid:
            source['status'] = 'changed'
        sources.append(source)
    return sources
