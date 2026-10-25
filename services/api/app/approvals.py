"""Shared integrity check for proposals and approved tool execution."""
import hashlib
import json


def propose_hash(run):
    return hashlib.sha256(json.dumps({'run': run['id'], 'version': run['version'],
        'proposal': {k: run['proposal'].get(k) for k in ('kind', 'summary', 'actions', 'required_approvers', 'expires_at')},
        'owner': run['owner_id']}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
