import hashlib
import json
from pathlib import Path

from matverse_stack.constraint_gate import CausalConstraintRule, MutationContext
from matverse_stack.constraint_registry import REGISTRY_SCHEMA, ConstraintRegistry
from matverse_stack.effect_binding import MemoryAppendEffectProposal, effect_payload_hash
from matverse_stack.homeostasis import run_controlled_homeostasis
from matverse_stack.ledger import Ledger
from matverse_stack.memory import GeometricMemory
from matverse_stack.service import MatVerseService


PREREG_SHA256 = "9ae5344b7cb980d18d41da55e16d2d6b898882e25f6c0504b935a23df59b6a7e"
V2_ID = "2a3e5b66-683e-49eb-87bc-756814ec4ca2"
SOURCE_APP_ID = "692b9db11ff853fffafa174e"


def _canonical_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _service(tmp_path: Path) -> MatVerseService:
    constraint = CausalConstraintRule(
        constraint_id=V2_ID,
        status="ACTIVE",
        mutation_class="UNSCOPED_WRITE",
        confidence_lt=0.60,
        allow_if_compensating_guard=True,
    )
    records = [constraint.model_dump()]
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
    service.ledger = Ledger(tmp_path / "ledger.jsonl")
    service.constraint_registry = ConstraintRegistry(
        registry_path,
        expected_snapshot_sha256=snapshot,
        expected_source_app_id=SOURCE_APP_ID,
    )
    return service


def test_preregistration_digest_is_frozen_before_execution():
    prereg_path = Path("experiments/controlled_homeostasis_v1.7_prereg.json")
    raw = prereg_path.read_text(encoding="utf-8")
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == PREREG_SHA256


def test_primary_paired_homeostasis_endpoints_meet_preregistered_thresholds():
    report = run_controlled_homeostasis(gain=0.60, max_steps=5)

    assert report.n_pairs == 12
    assert report.primary_result == "PASS"
    assert report.all_pairs_regulated_better is True
    assert report.median_normalized_error_reduction >= 0.90
    assert report.max_recovery_latency_steps <= 5
    assert report.max_overshoot <= 0.05
    assert report.max_residual_error <= 0.05

    for pair in report.pairs:
        assert pair.control.initial_state == pair.regulated.initial_state
        assert pair.control.perturbed_state == pair.regulated.perturbed_state
        assert pair.regulated.error_after < pair.control.error_after
        assert pair.regulated.normalized_error_reduction >= 0.90
        assert pair.regulated.recovery_latency_steps is not None
        assert pair.regulated.recovery_latency_steps <= 5


def test_control_arm_does_not_self_recover_without_regulation():
    report = run_controlled_homeostasis(gain=0.60, max_steps=5)

    for pair in report.pairs:
        assert pair.control.error_after == pair.control.error_before
        assert pair.control.normalized_error_reduction == 0.0
        assert pair.control.recovery_latency_steps is None


def test_inherited_constraint_blocks_hazardous_persistent_write(tmp_path):
    service = _service(tmp_path)
    effect = MemoryAppendEffectProposal(
        content="hazardous-homeostasis-challenge",
        source="controlled_homeostasis_v1.7",
        metadata={"class": "hazardous"},
    )
    mutation = MutationContext(
        mutation_id="homeostasis-hazard-001",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.40,
        compensating_guard=False,
        payload_hash=effect_payload_hash(effect),
    )

    result = service.execute_registered_effect(mutation, V2_ID, effect)

    assert result["final_decision"] == "BLOCK"
    assert result["effect_observation"]["effect_observed"] is False
    assert result["effect_observation"]["effect_count_before"] == 0
    assert result["effect_observation"]["effect_count_after"] == 0
    assert GeometricMemory(service.memory.path).ltm == []
    assert service.ledger.verify_integrity() is True


def test_inherited_constraint_admits_safe_guarded_writes_without_false_blocks(tmp_path):
    service = _service(tmp_path)
    safe_cases = [
        ("safe-01", 0.40),
        ("safe-02", 0.45),
        ("safe-03", 0.50),
        ("safe-04", 0.55),
    ]
    blocked = 0

    for mutation_id, confidence in safe_cases:
        effect = MemoryAppendEffectProposal(
            content=f"safe-homeostasis-challenge-{mutation_id}",
            source="controlled_homeostasis_v1.7",
            metadata={"class": "safe_guarded"},
        )
        mutation = MutationContext(
            mutation_id=mutation_id,
            mutation_class="UNSCOPED_WRITE",
            confidence=confidence,
            compensating_guard=True,
            payload_hash=effect_payload_hash(effect),
        )
        result = service.execute_registered_effect(mutation, V2_ID, effect)
        if result["final_decision"] != "PASS":
            blocked += 1
        assert result["effect_observation"]["effect_observed"] is True
        assert result["effect_observation"]["readback_ok"] is True

    assert blocked / len(safe_cases) == 0.0
    assert len(GeometricMemory(service.memory.path).ltm) == len(safe_cases)
    assert service.ledger.verify_integrity() is True
