from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .config import STACK_API_URL
from .constraint_gate import CausalConstraintRule, MutationContext, evaluate_constraint
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

    @staticmethod
    def _canonical_hash(value: Dict[str, Any]) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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

    def evaluate_mutation(
        self,
        mutation: MutationContext,
        constraint: CausalConstraintRule,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluate a mutation through a typed causal constraint and the SGSI gate.

        This is a decision-level production-binding candidate. It records the exact
        input, governing-state hash, activated constraint, final decision, ledger
        entry, and receipt. It deliberately does not claim a downstream mutation
        effect because this stack does not expose a mutation executor yet.
        """

        initial_state = initial_state or {
            "psi": 0.95,
            "theta": 0.95,
            "pole": 0.95,
            "losses": [],
            "latency_ms": 0,
            "replay_ok": True,
            "receipt_ok": True,
            "publication_ok": True,
        }

        mutation_dict = mutation.model_dump()
        constraint_dict = constraint.model_dump()
        input_hash = self._canonical_hash(mutation_dict)
        state_hash_before = self._canonical_hash(initial_state)

        constraint_result = evaluate_constraint(mutation, constraint)

        # Fresh SGSI instance makes the paired initial-state comparison explicit and
        # prevents state leakage (e.g. fail_closed_active) between experimental arms.
        runtime_gate = SGSI()
        sgsi_input = {
            "type": "mutation_evaluation",
            "stimulus": mutation_dict,
            "context": {
                "constraint_id": constraint.constraint_id,
                "constraint_status": constraint.status,
                "binding": "typed_causal_constraint_v1",
            },
            **initial_state,
        }
        sgsi_result = runtime_gate.process(sgsi_input)
        sgsi_decision = sgsi_result["governance"]["decision"]

        final_decision = "BLOCK" if constraint_result.decision == "BLOCK" else sgsi_decision
        activated_constraint_ids = constraint_result.activated_constraint_ids

        ledger_payload = {
            "input_hash": input_hash,
            "state_hash_before": state_hash_before,
            "mutation": mutation_dict,
            "constraint": constraint_dict,
            "constraint_decision": constraint_result.model_dump(),
            "sgsi_decision": sgsi_decision,
            "final_decision": final_decision,
            "activated_constraint_ids": activated_constraint_ids,
            "effect_observed": False,
            "effect_status": "NOT_EXECUTED_NO_MUTATION_EXECUTOR",
        }
        entry = self.ledger.add_entry("mutation_evaluated", ledger_payload)
        receipt = self.ledger.receipt(entry.index)

        return {
            **ledger_payload,
            "ledger_entry": entry.model_dump(),
            "receipt": receipt,
            "state_hash_after": state_hash_before,
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
