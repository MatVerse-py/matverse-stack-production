from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field

from .config import MEMORY_PATH
from .utils import safe_write_json


class UnmediatedMemoryWriteError(RuntimeError):
    """Raised when code attempts to persist organism memory outside the governed effect path."""


# Process-local capability. This is an application-architecture boundary, not a
# hostile-code sandbox. Only the governed effect executor in effect_binding.py
# imports the module-private append function that holds this capability.
_MEMORY_WRITE_CAPABILITY = object()


class MNB(BaseModel):
    mnb_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    content_hash: str
    embedding: List[float]
    geometric_anchor: List[float]
    psi: float = 1.0
    epsilon: float = 0.0
    kappa: float = 0.0
    persistence: float = 1.0
    source: str = "unknown"
    metadata: Dict[str, Any] = {}


class GeometricMemory:
    def __init__(self, path: Path = MEMORY_PATH):
        self.path = path
        self.ltm: List[MNB] = []
        self.stm: List[MNB] = []
        self.buffer: List[MNB] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            content = self.path.read_text(encoding="utf-8")
            data = json.loads(content)
            self.ltm = [MNB(**item) for item in data]
            self.stm = [item for item in self.ltm if item.psi > 0.8][-10:]
            self.buffer = self.stm[-5:]

    def _save(self) -> None:
        safe_write_json(self.path, [item.model_dump() for item in self.ltm])

    def add(self, content: str, source: str, metadata: Dict[str, Any]) -> MNB:
        """Reject legacy direct persistent writes.

        All organism-memory writes must traverse the governed mutation/effect path.
        Read/search operations remain available directly.
        """
        raise UnmediatedMemoryWriteError(
            "direct GeometricMemory.add is disabled; use the governed mutation/effect path"
        )

    def _append_with_capability(
        self,
        content: str,
        source: str,
        metadata: Dict[str, Any],
        *,
        capability: object,
    ) -> MNB:
        if capability is not _MEMORY_WRITE_CAPABILITY:
            raise UnmediatedMemoryWriteError("invalid governed memory-write capability")

        embedding = [float(ord(c)) / 128.0 for c in content[:16].ljust(16)]
        geometric_anchor = [sum(embedding) / len(embedding)] * 16
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        mnb = MNB(
            content=content,
            content_hash=content_hash,
            embedding=embedding,
            geometric_anchor=geometric_anchor,
            source=source,
            metadata=metadata,
        )
        self.ltm.append(mnb)

        self.stm.append(mnb)
        if len(self.stm) > 20:
            self.stm.pop(0)

        self.buffer.append(mnb)
        if len(self.buffer) > 5:
            self.buffer.pop(0)

        self._save()
        return mnb

    def search(self, query: str, top_k: int = 5) -> List[MNB]:
        query_embedding = [float(ord(c)) / 128.0 for c in query[:16].ljust(16)]

        scored_items = []
        for item in self.ltm:
            distance = sum(
                (q_val - i_val) ** 2
                for q_val, i_val in zip(query_embedding, item.embedding)
            ) ** 0.5
            boost = (
                0.8
                if any(b.mnb_id == item.mnb_id for b in self.buffer)
                else (0.9 if any(s.mnb_id == item.mnb_id for s in self.stm) else 1.0)
            )
            scored_items.append((distance * boost, item))

        scored_items.sort(key=lambda x: x[0])
        return [item for _, item in scored_items[:top_k]]

    def get_context_window(self) -> List[MNB]:
        return self.buffer

    def get(self, mnb_id: str) -> Optional[MNB]:
        for item in self.ltm:
            if item.mnb_id == mnb_id:
                return item
        return None


def _governed_memory_append(
    memory: GeometricMemory,
    *,
    content: str,
    source: str,
    metadata: Dict[str, Any],
) -> MNB:
    """Module-private persistence capability used only by the governed effect executor."""
    return memory._append_with_capability(
        content,
        source,
        metadata,
        capability=_MEMORY_WRITE_CAPABILITY,
    )
