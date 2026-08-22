from __future__ import annotations

import httpx
from typing import Any, Dict

from .config import STACK_API_URL, STACK_API_KEY, STACK_API_TIMEOUT_SEC
from .utils import normalize_upstream


class UpstreamAPI:
    def __init__(self):
        self.client = httpx.Client(timeout=STACK_API_TIMEOUT_SEC)

    def process_query(self, query: str, top_k: int = 3) -> Dict[str, Any]:
        if not STACK_API_URL:
            return normalize_upstream(None, query, 503) # Service Unavailable for local fallback

        headers = {}
        if STACK_API_KEY:
            headers["X-API-Key"] = STACK_API_KEY

        try:
            response = self.client.post(
                STACK_API_URL,
                json={
                    "input": query,
                    "top_k": top_k,
                    "add_to_memory": False, # We manage memory locally
                    "metadata": {"source": "matverse-sgsi"}
                },
                headers=headers
            )
            response.raise_for_status()
            return normalize_upstream(response.json(), query, response.status_code)
        except httpx.RequestError as e:
            return normalize_upstream(None, query, 500) # Internal Server Error for request issues
        except httpx.HTTPStatusError as e:
            return normalize_upstream(e.response.json(), query, e.response.status_code)
