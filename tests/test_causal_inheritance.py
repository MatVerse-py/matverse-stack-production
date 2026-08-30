import pytest

from matverse_stack.causal_inheritance import (
    AuthoritySeparationError,
    CausalRejection,
    ConstraintGenerator,
    IndependentAdjudicator,
)
from matverse_stack.constraint_gate import MutationContext
from matverse_stack.ledger import Ledger
from matverse_stack.service import MatVerseService


def _rejection():
    return CausalRejection(
        rejection_id="reject-001",
        mutation_class="UNSCOPED_WRITE",
        observed_confidence=0.20,
        reason="unsafe unscoped write",
        causal_attribution="unsafe_unscoped_persistent_write",
        context_id="context-A",
        substrate_id="substrate-A",
    )


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


def _service(tmp_path):
    service = MatVerseService.__new__(MatVerseService)
    service.ledger = Ledger(tmp_path / "causal_inheritance_test_ledger.jsonl")
    return service


def test_generator_emits_candidate_not_active_constraint():
    candidate = ConstraintGenerator("generator-A").generate(_rejection())
    assert candidate.origin_rejection_id == "reject-001"
    assert candidate.generator_id == "generator-A"
    assert candidate.attribution_ablated is False
    assert not hasattr(candidate, "status")


def test_generator_cannot_adjudicate_its_own_candidate():
    candidate = ConstraintGenerator("generator-A").generate(_rejection())
    with pytest.raises(AuthoritySeparationError):
        IndependentAdjudicator("generator-A").adjudicate(
            candidate, promote=True, evidence_ok=True
        )


def test_independent_promotion_activates_matching_rule():
    candidate = ConstraintGenerator("generator-A").generate(_rejection())
    rule, receipt = IndependentAdjudicator("adjudicator-B").adjudicate(
        candidate, promote=True, evidence_ok=True
    )
    assert rule.status == "ACTIVE"
    assert rule.mutation_class == "UNSCOPED_WRITE"
    assert receipt.decision == "PROMOTE"
    assert receipt.receipt_sha256


def test_not_promoted_candidate_is_dormant():
    candidate = ConstraintGenerator("generator-A").generate(_rejection())
    rule, receipt = IndependentAdjudicator("adjudicator-B").adjudicate(
        candidate, promote=False, evidence_ok=True
    )
    assert rule.status == "DORMANT"
    assert receipt.decision == "HOLD"


def test_attribution_ablation_breaks_future_match():
    generator = ConstraintGenerator("generator-A")
    candidate = generator.generate(_rejection(), ablate_attribution=True)
    rule, _ = IndependentAdjudicator("adjudicator-B").adjudicate(
        candidate, promote=True, evidence_ok=True
    )
    assert candidate.origin_rejection_id == "reject-001"
    assert candidate.attribution_ablated is True
    assert rule.status == "ACTIVE"
    assert rule.mutation_class == "ATTRIBUTION_ABLATED"


def test_promotion_changes_later_decision_under_same_input_and_state(tmp_path):
    generator = ConstraintGenerator("generator-A")
    candidate = generator.generate(
        _rejection(), confidence_lt=0.60, allow_if_compensating_guard=True
    )
    adjudicator = IndependentAdjudicator("adjudicator-B")
    promoted, _ = adjudicator.adjudicate(candidate, promote=True, evidence_ok=True)
    held, _ = adjudicator.adjudicate(candidate, promote=False, evidence_ok=True)
    service = _service(tmp_path)
    mutation = MutationContext(
        mutation_id="future-001",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.35,
        compensating_guard=False,
    )

    with_promoted = service.evaluate_mutation(
        mutation,
        promoted,
        initial_state=_initial_state(),
        constraint_authority="INDEPENDENT_ADJUDICATION_LAB_RECEIPT",
    )
    without_promotion = service.evaluate_mutation(
        mutation,
        held,
        initial_state=_initial_state(),
        constraint_authority="INDEPENDENT_ADJUDICATION_LAB_RECEIPT",
    )

    assert with_promoted["input_hash"] == without_promotion["input_hash"]
    assert with_promoted["state_hash_before"] == without_promotion["state_hash_before"]
    assert with_promoted["sgsi_decision"] == "PASS"
    assert without_promotion["sgsi_decision"] == "PASS"
    assert with_promoted["final_decision"] == "BLOCK"
    assert without_promotion["final_decision"] == "PASS"
    assert service.ledger.verify_integrity() is True


def test_promoted_rule_remains_selective_for_safe_controls(tmp_path):
    candidate = ConstraintGenerator("generator-A").generate(
        _rejection(), confidence_lt=0.60, allow_if_compensating_guard=True
    )
    rule, _ = IndependentAdjudicator("adjudicator-B").adjudicate(
        candidate, promote=True, evidence_ok=True
    )
    service = _service(tmp_path)

    high_conf = MutationContext(
        mutation_id="safe-high",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.95,
        compensating_guard=False,
    )
    compensated = MutationContext(
        mutation_id="safe-guarded",
        mutation_class="UNSCOPED_WRITE",
        confidence=0.20,
        compensating_guard=True,
    )

    assert service.evaluate_mutation(
        high_conf, rule, initial_state=_initial_state()
    )["final_decision"] == "PASS"
    assert service.evaluate_mutation(
        compensated, rule, initial_state=_initial_state()
    )["final_decision"] == "PASS"
