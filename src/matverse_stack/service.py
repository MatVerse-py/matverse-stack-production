from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import STACK_API_URL
from .ledger import Ledger, LedgerEntry
from .memory import GeometricMemory, MNB
from .sgsi import SGSI
from .upstream import UpstreamAPI
from .anchor import AnchorService


class MatVerseService:
    def __init__(self):
        self.memory = GeometricMemory()
        self.ledger = Ledger()
        self.upstream_api = UpstreamAPI()
        self.sgsi = SGSI()
        self.anchor_service = AnchorService()

    def process_query(self, query: str, top_k: int = 3, add_to_memory: bool = False, metadata: Dict[str, Any] = {}) -> Dict[str, Any]:
        upstream_result = self.upstream_api.process_query(query, top_k)

        sgsi_input = {
            "type": "process_query",
            "stimulus": {"query": query, "top_k": top_k, "metadata": metadata},
            "context": {"upstream_mode": upstream_result["mode"]},
            "losses": [1.0] if upstream_result["mode"] != "remote_ok" else [],
            "latency_ms": 0,
            "replay_ok": True,
            "receipt_ok": True,
            "publication_ok": True,
        }
        sgsi_result = self.sgsi.process(sgsi_input)

        mnb: Optional[MNB] = None
        if add_to_memory and sgsi_result["governance"]["decision"] != "BLOCK":
            mnb = self.memory.add(
                content=upstream_result["answer"],
                source=metadata.get("source", "process_query"),
                metadata=metadata
            )
            sgsi_result["skill"]["mutation_applied"] = True
            sgsi_result["skill"]["new_mnb_id"] = mnb.mnb_id

        ledger_payload = {
            "query": query,
            "upstream_result": upstream_result,
            "sgsi_result": sgsi_result,
            "mnb_added": mnb.model_dump() if mnb else None,
        }
        self.ledger.add_entry("query_processed", ledger_payload)

        return {
            "query": query,
            "answer": upstream_result["answer"],
            "sgsi_decision": sgsi_result["governance"]["decision"],
            "sgsi_metrics": sgsi_result["metrics"],
            "mnb_id": mnb.mnb_id if mnb else None,
            "ledger_entry": self.ledger.entries[-1].model_dump(),
        }

    def close_autogenesis(self, metadata: Dict[str, Any] = {}) -> Dict[str, Any]:
        status = self.ledger.status()
        anchor_ref = self.anchor_service.anchor_hash(status["merkle_root"])
        
        payload = {
            "merkle_root": status["merkle_root"],
            "total_entries": status["entries"],
            "integrity_ok": status["integrity_ok"],
            "metadata": metadata
        }
        
        entry = self.ledger.close_cycle(payload, anchor_ref=anchor_ref)
        
        return {
            "status": "CLOSED",
            "entry": entry.model_dump(),
            "receipt": self.ledger.receipt(entry.index),
            "anchor": self.anchor_service.get_status()
        }

    def add_mnb(self, content: str, source: str, metadata: Dict[str, Any]) -> MNB:
        mnb = self.memory.add(content, source, metadata)
        self.ledger.add_entry("mnb_added", mnb.model_dump())
        return mnb

    def search_memory(self, query: str, top_k: int = 5) -> List[MNB]:
        return self.memory.search(query, top_k)

    def get_mnb(self, mnb_id: str) -> Optional[MNB]:
        return self.memory.get(mnb_id)

    def get_ledger(self) -> List[LedgerEntry]:
        return self.ledger.get_all_entries()

    def get_health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "memory_path": str(self.memory.path),
            "ledger_path": str(self.ledger.path),
            "stack_api_configured": bool(STACK_API_URL),
            "memory_items": len(self.memory.ltm),
            "ledger_entries": len(self.ledger.entries),
            "ledger_status": self.ledger.status(),
            "anchor_status": self.anchor_service.get_status()
        }
