from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from fastapi import Header, HTTPException

DATA_LOCK = Lock()
APP_API_KEY = os.getenv("APP_API_KEY", "").strip()


def require_api_key(x_api_key: str | None = Header(None)) -> None:
    if APP_API_KEY and x_api_key != APP_API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


def safe_append_jsonl(path: Path, event: Dict[str, Any]) -> None:
    with DATA_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def safe_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with DATA_LOCK:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)


def normalize_upstream(raw: Any, text_fallback: str, status_code: int) -> Dict[str, Any]:
    if status_code >= 400:
        return {
            "mode": "remote_error",
            "status_code": status_code,
            "answer": f"Erro remoto HTTP {status_code}",
            "raw": raw,
        }

    answer = None
    if isinstance(raw, dict):
        answer = raw.get("answer") or raw.get("output") or raw.get("result")

    if not isinstance(answer, str) or not answer.strip():
        answer = text_fallback

    return {
        "mode": "remote_ok",
        "status_code": status_code,
        "answer": answer,
        "raw": raw,
    }
