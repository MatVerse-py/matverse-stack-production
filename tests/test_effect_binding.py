import hashlib
import json
from pathlib import Path

from matverse_stack.constraint_gate import CausalConstraintRule, MutationContext
from matverse_stack.constraint_registry import REGISTRY_SCHEMA, ConstraintRegistry
from matverse_stack.effect_binding import MemoryAppendEffectProposal, effect_payload_hash
from matverse_stack.ledger import Ledger
from matverse_stack.memory import GeometricMemory
from matverse_stack.service import MatVerseService


V1_ID = "86714849-d405-4c5f-beee-6b7c9376983d"
V2_ID = "2a3e5b66-683e-49eb-87bc-756814ec4ca2"
SOURCE_APP_ID = "692b9db11ff853fffafa174e"


def _canonical_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _v1() -> CausalConstraintRule:
    return CausalConstraintRule(
        constraint_id=V1_ID,
        status="ACTIVE",
        mutation_class="UNSCOPED_WRITE",
        confidence_lt=0.80,
        allow_if_compensating_guard=False,
    )


def _v2() -> CausalConstraintRule:
    return CausalConstraintRule(
        constraint_id=V2_ID,
        status="ACTIVE",
        mutation_class="UNSCOPED_WRITE",
        confidence_lt=0.60,
        allow_if_compensating_guard=True,
        supersedes=V1_ID,
    )


def _service(tmp_path: Path) -> MatVerseService:
    records = [_v1().model_dump(), _v2().model_dump()]
    snapshot = _canonical_hash(records)
    registry_path = tmp_path / "causal_constraints.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema": REGISTRY_SCHEMA,
                "source_app_id": SOURCE_APP_ID,
                "snapshot_sha256": snapshot,
                "constraints": records,
            }
        ),
        encoding="utf-8",
    )

    service = MatVerseService.__new__(MatVerseService)
    service.memory = GeometricMemory(tmp_path / "memory.json")
    service.ledger = Ledger(tmp_path / "effect_binding_ledger.jsonl")
    service.constraint_registry = ConstraintRegistry(
        registry_path,
        expected_snapshot_sha256=snapshot,
        expected_source_app_id=SOURCE_APP_ID,
    )
    return service


def _effect() -> MemoryAppendEffectProposal:
    return MemoryAppendEffectProposal(
        content="effect-binding-v1.5 deterministic payload",
        source="ocg_effect_binding_test",
        metadata={"fixture": "paired-v1-v2"},
    )


def _mutation(effect: MemoryAppendEffectProposal, *, confidence: float = 0.65) -> MutationContext:
    return MutationContext(
        mutation_id="effect-paired-001",
        mutation_class="UNSCOPED_WRITE",
        confidence=confidence,
        compensating_guard=False,
        payload_hash=effect_payload_hash(effect),
    )


def test_same_operation_v1_blocks_and_v2_persists_observable_effect(tmp_path):
    service = _service(tmp_path)
    effect = _effect()
    mutation = _mutation(effect)

    with_v1 = service.execute_registered_effect(mutation, V1_ID, effect)
    with_v2 = service.execute_registered_effect(mutation, V2_ID, effect)

    assert with_v1["operation_input_hash"] == with_v2["operation_input_hash"]
    assert with_v1["mutation_input_hash"] == with_v2["mutation_input_hash"]
    assert with_v1["state_hash_before"] == with_v2["state_hash_before"]
    assert with_v1["registry_snapshot_hash"] == with_v2["registry_snapshot_hash"]

    v1_effect = with_v1["effect_observation"]
    v2_effect = with_v2["effect_observation"]
    assert v1_effect["effect_state_hash_before"] == v2_effect["effect_state_hash_before"]

    assert with_v1["final_decision"] == "BLOCK"
    assert with_v1["effect_binding_result"] == "PASS"
    assert v1_effect["effect_status"] == "BLOCKED_NOT_EXECUTED"
    assert v1_effect["effect_observed"] is False
    assert v1_effect["effect_count_before"] == 0
    assert v1_effect["effect_count_after"] == 0
    assert v1_effect["effect_state_hash_before"] == v1_effect["effect_state_hash_after"]

    assert with_v2["final_decision"] == "PASS"
    assert with_v2["effect_binding_result"] == "PASS"
    assert v2_effect["effect_status"] == "EXECUTED_OBSERVED"
    assert v2_effect["effect_observed"] is True
    assert v2_effect["readback_ok"] is True
    assert v2_effect["effect_count_before"] == 0
    assert v2_effect["effect_count_after"] == 1
    assert v2_effect["effect_state_hash_before"] != v2_effect["effect_state_hash_after"]
    assert v2_effect["content_hash"] == v2_effect["readback_content_hash"]

    fresh = GeometricMemory(service.memory.path)
    persisted = fresh.get(v2_effect["mnb_id"])
    assert persisted is not None
    assert persisted.content == effect.content
    assert persisted.metadata["mutation_id"] == mutation.mutation_id
    assert persisted.metadata["effect_payload_hash"] == effect_payload_hash(effect)
    assert persisted.metadata["write_authority"] == "GOVERNED_EFFECT_CAPABILITY"

    assert with_v1["authorization_entry_hash"]
    assert with_v2["authorization_entry_hash"]
    assert with_v1["authorization_receipt"]["entry_hash"] == with_v1["authorization_entry_hash"]
    assert with_v2["authorization_receipt"]["entry_hash"] == with_v2["authorization_entry_hash"]
    assert service.ledger.verify_integrity() is True


def test_hazardous_successor_decision_blocks_without_effect(tmp_path):
    service = _service(tmp_path)
    effect = _effect()
    mutation = _mutation(effect, confidence=0.40)

    result = service.execute_registered_effect(mutation, V2_ID, effect)
    observation = result["effect_observation"]

    assert result["final_decision"] == "BLOCK"
    assert result["effect_binding_result"] == "PASS"
    assert observation["effect_status"] == "BLOCKED_NOT_EXECUTED"
    assert observation["effect_count_before"] == observation["effect_count_after"] == 0
    assert GeometricMemory(service.memory.path).ltm == []


def test_payload_hash_mismatch_fails_closed_before_execution(tmp_path):
    service = _service(tmp_path)
    effect = _effect()
    mutation = _mutation(effect).model_copy(update={"payload_hash": "0" * 64})

    result = service.execute_registered_effect(mutation, V2_ID, effect)
    observation = result["effect_observation"]

    assert result["binding_valid"] is False
    assert result["final_decision"] == "FAIL_CLOSED"
    assert result["effect_binding_result"] == "FAIL"
    assert observation["effect_status"] == "BINDING_REJECTED"
    assert observation["effect_observed"] is False
    assert observation["effect_count_before"] == observation["effect_count_after"] == 0
    assert service.ledger.verify_integrity() is True


def test_non_pass_sgsi_decision_cannot_execute_even_when_constraint_passes(tmp_path):
    service = _service(tmp_path)
    effect = _effect()
    mutation = _mutation(effect)

    result = service.execute_registered_effect(
        mutation,
        V2_ID,
        effect,
        initial_state={
            "psi": 0.40,
            "theta": 0.95,
            "pole": 0.95,
            "losses": [],
            "latency_ms": 0,
            "replay_ok": True,
            "receipt_ok": True,
            "publication_ok": True,
        },
    )
    observation = result["effect_observation"]

    assert result["final_decision"] == "ESCALATE"
    assert result["effect_binding_result"] == "PASS"
    assert observation["effect_status"] == "NOT_EXECUTED_DECISION"
    assert observation["execution_authorized"] is False
    assert observation["effect_count_before"] == observation["effect_count_after"] == 0


def test_effect_receipts_bind_evaluation_authorization_and_observation(tmp_path):
    service = _service(tmp_path)
    effect = _effect()
    mutation = _mutation(effect)

    result = service.execute_registered_effect(mutation, V2_ID, effect)

    assert result["evaluation_entry_hash"]
    assert result["authorization_entry_hash"]
    assert result["authorization_receipt"]["entry_hash"] == result["authorization_entry_hash"]
    assert result["ledger_entry"]["event_type"] == "mutation_effect_bound"
    assert result["receipt"]["entry_hash"] == result["ledger_entry"]["entry_hash"]
    assert result["effect_observation"]["readback_ok"] is True
    assert service.ledger.verify_integrity() is True


def test_authorized_retry_is_idempotent_and_does_not_duplicate_mnb(tmp_path):
    service = _service(tmp_path)
    effect = _effect()
    mutation = _mutation(effect)

    first = service.execute_registered_effect(mutation, V2_ID, effect)
    retry = service.execute_registered_effect(mutation, V2_ID, effect)

    assert first["effect_observation"]["effect_status"] == "EXECUTED_OBSERVED"
    assert retry["effect_observation"]["effect_status"] == "ALREADY_EXECUTED_OBSERVED"
    assert retry["effect_observation"]["effect_observed"] is True
    assert retry["effect_binding_result"] == "PASS"
    assert retry["effect_observation"]["mnb_id"] == first["effect_observation"]["mnb_id"]
    assert len(GeometricMemory(service.memory.path).ltm) == 1
    assert service.ledger.verify_integrity() is True


def test_duplicate_idempotency_key_fails_closed_without_third_write(tmp_path):
    service = _service(tmp_path)
    effect = _effect()
    mutation = _mutation(effect)

    first = service.execute_registered_effect(mutation, V2_ID, effect)
    assert first["effect_observation"]["effect_status"] == "EXECUTED_OBSERVED"

    # Corruption fixture: duplicate the persisted record at the storage layer rather
    # than using a second application write path. The runtime must detect this and
    # refuse to create a third effect.
    raw = json.loads(service.memory.path.read_text(encoding="utf-8"))
    raw.append(dict(raw[0]))
    service.memory.path.write_text(json.dumps(raw), encoding="utf-8")
    assert len(GeometricMemory(service.memory.path).ltm) == 2

    result = service.execute_registered_effect(mutation, V2_ID, effect)

    assert result["final_decision"] == "PASS"
    assert result["effect_binding_result"] == "FAIL"
    assert result["effect_observation"]["effect_status"] == "DUPLICATE_EFFECT_DETECTED"
    assert result["effect_observation"]["effect_observed"] is False
    assert len(GeometricMemory(service.memory.path).ltm) == 2
    assert service.ledger.verify_integrity() is True
