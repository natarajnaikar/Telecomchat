"""Append-only JSONL logs for interactions, feedback and handoffs (FR-05a, FR-36, NFR-23).

All free text is PII-redacted before it is written (NFR-13).
"""
import json
from datetime import datetime, timezone

import config
from guardrails import redact_pii

_REDACT_KEYS = {"question", "rewritten_query", "answer", "comment", "transcript"}


def _redact(value):
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    return value


def write(event_type: str, payload: dict, path=config.INTERACTION_LOG, redact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event_type}
    for k, v in payload.items():
        record[k] = _redact(v) if redact and k in _REDACT_KEYS else v
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_all(path=config.INTERACTION_LOG) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
