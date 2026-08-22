from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field

from .config import MEMORY_PATH
from .utils import safe_write_json


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
        self.ltm: List[MNB] = [] # Long-Term Memory
        self.stm: List[MNB] = [] # Short-Term Memory (recent/high-psi)
        self.buffer: List[MNB] = [] # Context Buffer (active window)
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            content = self.path.read_text(encoding="utf-8")
            data = json.loads(content)
            self.ltm = [MNB(**item) for item in data]
            # Initialize STM with high-psi items from LTM
            self.stm = [item for item in self.ltm if item.psi > 0.8][-10:]
            # Buffer starts empty or with most recent STM
            self.buffer = self.stm[-5:]

    def _save(self) -> None:
        safe_write_json(self.path, [item.model_dump() for item in self.ltm])

    def add(self, content: str, source: str, metadata: Dict[str, Any]) -> MNB:
        # Placeholder for actual embedding and geometric anchor generation
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
        
        # Update STM (Short-Term Memory)
        self.stm.append(mnb)
        if len(self.stm) > 20:
            self.stm.pop(0)
            
        # Update Context Buffer
        self.buffer.append(mnb)
        if len(self.buffer) > 5:
            self.buffer.pop(0)

        self._save()
        return mnb

    def search(self, query: str, top_k: int = 5) -> List[MNB]:
        # Search primarily in LTM, but prioritize STM/Buffer context
        query_embedding = [float(ord(c)) / 128.0 for c in query[:16].ljust(16)]
        
        scored_items = []
        for item in self.ltm:
            distance = sum((q_val - i_val)**2 for q_val, i_val in zip(query_embedding, item.embedding))**0.5
            # Boost score if item is in STM or Buffer
            boost = 0.8 if any(b.mnb_id == item.mnb_id for b in self.buffer) else (0.9 if any(s.mnb_id == item.mnb_id for s in self.stm) else 1.0)
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
