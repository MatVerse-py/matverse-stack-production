from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from .config import STACK_API_URL
from .constraint_gate import (
    CausalConstraintRule,
    GovernanceEvaluationState,
    MutationContext,
    evaluate_constraint,
)
from .constraint_registry import ConstraintRegistry
from .effect_binding import (
    MemoryAppendEffectProposal,
    effect_payload_hash,
    execute_memory_append,
    observe_without_execution,
)
from .ledger import Ledger, LedgerEntry
from .memory import GeometricMemory, MNB, UnmediatedMemoryWriteError
from .sgsi import SGSI
from .upstream import UpstreamAPI
from .anchor import AnchorService


GLOBAL_MEDIATION_PROTOCOL = "matverse.ocg.global_mediation/0.1.0"


class MatVerseService:
    def __init__(self):
        self.memory = GeometricMemory()
        self.ledger = Ledger()
        self.upstream_api = UpstreamAPI()
        self.sgsi = SGSI()
        self.anchor_service = AnchorService()
        self.constraint_registry = ConstraintRegistry()

    @staticmethod
    def _canonical_hash(value: Dict[str, Any]) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def process_query(
        self,
        query: str,
        top_k: int = 3,
        add_to_memory: bool = False,
        metadata: Dict[str, Any] = {},
    ) -> Dict[str, Any]:
        """Process a query without allowing the legacy query path to mutate memory.

        `add_to_memory=True` is retained only as a compatibility signal. It is now
        fail-closed and produces evidence explaining that persistence must be proposed
        as a typed effect through execute_registered_effect.
        """
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

        memory_write_status = "NOT_REQUESTED"
        if add_to_memory:
            memory_write_status = "BLOCKED_GLOBAL_MEDIATION_REQUIRED"
            sgsi_result["skill"]["mutation_applied"] = False
            sgsi_result["skill"]["memory_write_status"] = memory_write_status

        ledger_payload = {
            "query": query,
            "upstream_result": upstream_result,
            "sgsi_result": sgsi_result,
            "mnb_added": None,
            "memory_write_requested": bool(add_to_memory),
            "memory_write_status": memory_write_status,
            "global_mediation_protocol": GLOBAL_MEDIATION_PROTOCOL,
        }
        self.ledger.add_entry("query_processed", ledger_payload)

        return {
            "query": query,
            "answer": upstream_result["answer"],
            "sgsi_decision": sgsi_result["governance"]["decision"],
            "sgsi_metrics": sgsi_result["metrics"],
            "mnb_id": None,
            "memory_write_status": memory_write_status,
            "ledger_entry": self.ledger.entries[-1].model_dump(),
        }

    def evaluate_registered_mutation(
        self,
        mutation: MutationContext,
        constraint_id: str,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        constraint, registry_record_hash, registry_snapshot_hash = self.constraint_registry.resolve(
            constraint_id
        )
        return self.evaluate_mutation(
            mutation,
            constraint,
            initial_state=initial_state,
            constraint_authority="PINNED_CANONICAL_CONSTRAINT_REGISTRY",
            registry_record_hash=registry_record_hash,
            registry_snapshot_hash=registry_snapshot_hash,
        )

    def evaluate_mutation(
        self,
        mutation: MutationContext,
        constraint: CausalConstraintRule,
        initial_state: Optional[Dict[str, Any]] = None,
        constraint_authority: str = "DIRECT_TYPED_RULE_INTERNAL_ONLY",
        registry_record_hash: Optional[str] = None,
        registry_snapshot_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        governing_state = GovernanceEvaluationState.model_validate(initial_state or {}).model_dump()
        mutation_dict = mutation.model_dump()
        constraint_dict = constraint.model_dump()
        input_hash = self._canonical_hash(mutation_dict)
        state_hash_before = self._canonical_hash(governing_state)

        constraint_result = evaluate_constraint(mutation, constraint)

        runtime_gate = SGSI()
        sgsi_input = {
            **governing_state,
            "type": "mutation_evaluation",
            "stimulus": mutation_dict,
            "context": {
                "constraint_id": constraint.constraint_id,
                "constraint_status": constraint.status,
                "constraint_authority": constraint_authority,
                "registry_record_hash": registry_record_hash,
                "registry_snapshot_hash": registry_snapshot_hash,
                "binding": "typed_causal_constraint_v1",
            },
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
            "constraint_authority": constraint_authority,
            "registry_record_hash": registry_record_hash,
            "registry_snapshot_hash": registry_snapshot_hash,
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

    def execute_registered_effect(
        self,
        mutation: MutationContext,
        constraint_id: str,
        effect: MemoryAppendEffectProposal,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Bind a governed decision to a persistent MNB append effect."""
        mutation_dict = mutation.model_dump()
        effect_dict = effect.model_dump()
        proposed_effect_hash = effect_payload_hash(effect)
        operation_input_hash = self._canonical_hash(
            {"mutation": mutation_dict, "effect": effect_dict}
        )

        if not mutation.payload_hash or mutation.payload_hash != proposed_effect_hash:
            observation = observe_without_execution(
                self.memory,
                effect,
                final_decision="FAIL_CLOSED_BINDING",
                binding_rejected=True,
            )
            payload = {
                "operation_input_hash": operation_input_hash,
                "mutation": mutation_dict,
                "constraint_id": constraint_id,
                "effect": effect_dict,
                "binding_valid": False,
                "final_decision": "FAIL_CLOSED",
                "effect_observation": observation.model_dump(),
                "effect_binding_result": "FAIL",
                "reason": "mutation.payload_hash does not match the exact effect proposal",
            }
            entry = self.ledger.add_entry("mutation_effect_rejected", payload)
            return {
                **payload,
                "ledger_entry": entry.model_dump(),
                "receipt": self.ledger.receipt(entry.index),
            }

        evaluation = self.evaluate_registered_mutation(
            mutation,
            constraint_id,
            initial_state=initial_state,
        )

        execution_authorized = evaluation["final_decision"] == "PASS"
        authorization_payload = {
            "operation_input_hash": operation_input_hash,
            "mutation_input_hash": evaluation["input_hash"],
            "evaluation_entry_hash": evaluation["ledger_entry"]["entry_hash"],
            "constraint_id": constraint_id,
            "constraint_authority": evaluation["constraint_authority"],
            "registry_record_hash": evaluation["registry_record_hash"],
            "registry_snapshot_hash": evaluation["registry_snapshot_hash"],
            "final_decision": evaluation["final_decision"],
            "execution_authorized": execution_authorized,
            "effect_payload_hash": proposed_effect_hash,
            "effect": effect_dict,
            "global_mediation_protocol": GLOBAL_MEDIATION_PROTOCOL,
        }
        authorization_event = (
            "mutation_effect_authorized"
            if execution_authorized
            else "mutation_effect_not_authorized"
        )
        authorization_entry = self.ledger.add_entry(authorization_event, authorization_payload)
        authorization_receipt = self.ledger.receipt(authorization_entry.index)

        if execution_authorized:
            observation = execute_memory_append(
                self.memory,
                effect,
                mutation_id=mutation.mutation_id,
            )
        else:
            observation = observe_without_execution(
                self.memory,
                effect,
                final_decision=evaluation["final_decision"],
            )

        if execution_authorized:
            effect_binding_result = "PASS" if observation.effect_observed else "FAIL"
        else:
            effect_binding_result = (
                "PASS"
                if observation.readback_ok and not observation.effect_observed
                else "FAIL"
            )

        payload = {
            "operation_input_hash": operation_input_hash,
            "mutation_input_hash": evaluation["input_hash"],
            "state_hash_before": evaluation["state_hash_before"],
            "constraint_id": constraint_id,
            "constraint_authority": evaluation["constraint_authority"],
            "registry_record_hash": evaluation["registry_record_hash"],
            "registry_snapshot_hash": evaluation["registry_snapshot_hash"],
            "evaluation_entry_hash": evaluation["ledger_entry"]["entry_hash"],
            "authorization_entry_hash": authorization_entry.entry_hash,
            "authorization_receipt_leaf": authorization_receipt["leaf"],
            "final_decision": evaluation["final_decision"],
            "binding_valid": True,
            "effect": effect_dict,
            "effect_observation": observation.model_dump(),
            "effect_binding_result": effect_binding_result,
            "global_mediation_protocol": GLOBAL_MEDIATION_PROTOCOL,
        }
        entry = self.ledger.add_entry("mutation_effect_bound", payload)
        return {
            **payload,
            "authorization_receipt": authorization_receipt,
            "ledger_entry": entry.model_dump(),
            "receipt": self.ledger.receipt(entry.index),
        }

    def close_autogenesis(self, metadata: Dict[str, Any] = {}) -> Dict[str, Any]:
        """Evidence-plane closure; this does not mutate governed MNB state."""
        status = self.ledger.status()
        anchor_ref = self.anchor_service.anchor_hash(status["merkle_root"])
        payload = {
            "merkle_root": status["merkle_root"],
            "total_entries": status["entries"],
            "integrity_ok": status["integrity_ok"],
            "metadata": metadata,
        }
        entry = self.ledger.close_cycle(payload, anchor_ref=anchor_ref)
        return {
            "status": "CLOSED",
            "entry": entry.model_dump(),
            "receipt": self.ledger.receipt(entry.index),
            "anchor": self.anchor_service.get_status(),
        }

    def add_mnb(self, content: str, source: str, metadata: Dict[str, Any]) -> MNB:
        """Fail closed for the legacy direct-memory service surface."""
        attempt = {
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "source": source,
            "metadata_hash": self._canonical_hash(metadata),
            "status": "BLOCKED_GLOBAL_MEDIATION_REQUIRED",
            "global_mediation_protocol": GLOBAL_MEDIATION_PROTOCOL,
        }
        self.ledger.add_entry("unmediated_memory_write_blocked", attempt)
        raise UnmediatedMemoryWriteError(
            "direct MatVerseService.add_mnb is disabled; submit a typed MNB_APPEND through the governed mutation/effect path"
        )

    def search_memory(self, query: str, top_k: int = 5) -> List[MNB]:
        return self.memory.search(query, top_k)

    def get_mnb(self, mnb_id: str) -> Optional[MNB]:
        return self.memory.get(mnb_id)

    def get_ledger(self) -> List[LedgerEntry]:
        return self.ledger.get_all_entries()

    def get_mediation_status(self) -> Dict[str, Any]:
        return {
            "protocol": GLOBAL_MEDIATION_PROTOCOL,
            "governed_state_plane": "MNB_MEMORY",
            "allowed_persistent_effect": "MNB_APPEND_VIA_MUTATION_EXECUTE",
            "legacy_memory_add": "BLOCKED",
            "process_query_memory_write": "BLOCKED",
            "direct_geometric_memory_add": "BLOCKED",
            "evidence_plane_ledger": "EXEMPT_APPEND_ONLY_EVIDENCE",
            "anchor_plane": "EVIDENCE_ONLY_NO_CONFIRMED_ONCHAIN_WRITE_IN_CURRENT_IMPLEMENTATION",
        }

    def get_health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "memory_path": str(self.memory.path),
            "ledger_path": str(self.ledger.path),
            "stack_api_configured": bool(STACK_API_URL),
            "memory_items": len(self.memory.ltm),
            "ledger_entries": len(self.ledger.entries),
            "ledger_status": self.ledger.status(),
            "anchor_status": self.anchor_service.get_status(),
            "global_mediation": self.get_mediation_status(),
        }
