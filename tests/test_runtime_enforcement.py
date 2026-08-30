import hashlib
import json
from pathlib import Path

import pytest

from matverse_stack.constraint_gate import CausalConstraintRule, MutationContext, evaluate_constraint
from matverse_stack.constraint_registry import (
    REGISTRY_SCHEMA,
    ConstraintRegistry,
    ConstraintRegistryError,
)
from matverse_stack.ledger import Ledger
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


def _write_registry(tmp_path: Path, records=None, source_app_id: str = SOURCE_APP_ID):
    records = records or [_v1().model_dump(), _v2().model_dump()]
    snapshot = _canonical_hash(records)
    registry_path = tmp_path / "causal_constraints.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema": REGISTRY_SCHEMA,
                "source_app_id": source_app_id,
                "snapshot_sha256": snapshot,
                "constraints": records,
            }
        ),
        encoding="utf-8",
    )
    return registry_path, snapshot


def _service(tmp_path: Path) -> MatVerseService:
    registry_path, snapshot = _write_registry(tmp_path)

    service = MatVerseService.__new__(MatVerseService)
    service.ledger = Ledger(tmp_path / "runtime_enforcement_ledger.jsonl")
    service.constraint_registry = ConstraintRegistry(
        registry_path,
        expected_snapshot_sha256=snapshot,
        expected_source_app_id=SOURCE_APP_ID,
    )
    return service


def _initial_state():
    return {
        "psi": 0.95,
        "theta": 0.95,
        "pole": 0.95,
        "losses": [],
        "latency_ms": 0,
        "replay_ok": True,
        "receipt_ok": True,
        "publication_ok": True,
    }


def test_constraint_pair_changes_decision_without_changing_input_or_initial_state(tmp_path):
    mutation = MutationContext(
        mutation_id="paired-001",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.65,
        compensating_guard=False,
        payload_hash="a" * 64,
    )
    service = _service(tmp_path)

    with_v1 = service.evaluate_registered_mutation(mutation, V1_ID, _initial_state())
    with_v2 = service.evaluate_registered_mutation(mutation, V2_ID, _initial_state())

    assert with_v1["input_hash"] == with_v2["input_hash"]
    assert with_v1["state_hash_before"] == with_v2["state_hash_before"]
    assert with_v1["constraint_authority"] == "PINNED_CANONICAL_CONSTRAINT_REGISTRY"
    assert with_v2["constraint_authority"] == "PINNED_CANONICAL_CONSTRAINT_REGISTRY"
    assert with_v1["registry_record_hash"]
    assert with_v2["registry_record_hash"]
    assert with_v1["registry_snapshot_hash"] == with_v2["registry_snapshot_hash"]
    assert with_v1["sgsi_decision"] == "PASS"
    assert with_v2["sgsi_decision"] == "PASS"
    assert with_v1["final_decision"] == "BLOCK"
    assert with_v2["final_decision"] == "PASS"
    assert with_v1["activated_constraint_ids"] == [V1_ID]
    assert with_v2["activated_constraint_ids"] == []
    assert with_v1["effect_observed"] is False
    assert with_v2["effect_observed"] is False
    assert with_v1["receipt"]["entry_hash"]
    assert with_v2["receipt"]["entry_hash"]
    assert service.ledger.verify_integrity() is True


def test_successor_preserves_hazardous_block(tmp_path):
    mutation = MutationContext(
        mutation_id="paired-002",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.40,
        compensating_guard=False,
    )
    service = _service(tmp_path)

    with_v1 = service.evaluate_registered_mutation(mutation, V1_ID, _initial_state())
    with_v2 = service.evaluate_registered_mutation(mutation, V2_ID, _initial_state())

    assert with_v1["final_decision"] == "BLOCK"
    assert with_v2["final_decision"] == "BLOCK"
    assert with_v2["activated_constraint_ids"] == [V2_ID]


def test_successor_allows_compensating_guard():
    mutation = MutationContext(
        mutation_id="paired-003",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.30,
        compensating_guard=True,
    )

    assert evaluate_constraint(mutation, _v1()).decision == "BLOCK"
    assert evaluate_constraint(mutation, _v2()).decision == "PASS"


def test_superseded_constraint_is_not_enforced():
    superseded = _v1().model_copy(update={"status": "SUPERSEDED"})
    mutation = MutationContext(
        mutation_id="paired-004",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.10,
    )

    result = evaluate_constraint(mutation, superseded)
    assert result.decision == "INACTIVE"
    assert result.activated_constraint_ids == []


def test_registry_fails_closed_for_unknown_constraint(tmp_path):
    service = _service(tmp_path)
    mutation = MutationContext(
        mutation_id="paired-005",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.65,
    )

    with pytest.raises(ConstraintRegistryError):
        service.evaluate_registered_mutation(mutation, "missing-constraint", _initial_state())


def test_registry_fails_closed_when_snapshot_is_tampered(tmp_path):
    registry_path, snapshot = _write_registry(tmp_path)
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    raw["constraints"][0]["confidence_lt"] = 0.99
    registry_path.write_text(json.dumps(raw), encoding="utf-8")

    registry = ConstraintRegistry(
        registry_path,
        expected_snapshot_sha256=snapshot,
        expected_source_app_id=SOURCE_APP_ID,
    )
    with pytest.raises(ConstraintRegistryError):
        registry.resolve(V1_ID)


def test_registry_fails_closed_for_wrong_source_app(tmp_path):
    registry_path, snapshot = _write_registry(tmp_path, source_app_id="wrong-app")
    registry = ConstraintRegistry(
        registry_path,
        expected_snapshot_sha256=snapshot,
        expected_source_app_id=SOURCE_APP_ID,
    )
    with pytest.raises(ConstraintRegistryError):
        registry.resolve(V1_ID)
