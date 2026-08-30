from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from .constraint_gate import CausalConstraintRule


CAUSAL_INHERITANCE_PROTOCOL = "matverse.governed_causal_inheritance/0.1.0"


class AuthoritySeparationError(RuntimeError):
    """Raised when the generator attempts to authorize its own candidate."""


def _canonical_hash(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CausalRejection(BaseModel):
    rejection_id: str
    mutation_class: str
    observed_confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    causal_attribution: str
    context_id: str
    substrate_id: str


class ConstraintCandidate(BaseModel):
    candidate_id: str
    origin_rejection_id: str
    generator_id: str
    mutation_class: str
    confidence_lt: float = Field(ge=0.0, le=1.0)
    allow_if_compensating_guard: bool = True
    attribution_hash: str
    attribution_ablated: bool = False
    protocol: str = CAUSAL_INHERITANCE_PROTOCOL


class AdjudicationReceipt(BaseModel):
    candidate_id: str
    origin_rejection_id: str
    generator_id: str
    adjudicator_id: str
    decision: Literal["PROMOTE", "HOLD", "REJECT"]
    evidence_ok: bool
    promoted_constraint_id: str
    receipt_sha256: str
    protocol: str = CAUSAL_INHERITANCE_PROTOCOL


class ConstraintGenerator:
    """Generate constraint candidates from attributed rejections.

    Generation is deliberately non-authoritative: this class has no operation that
    can promote a candidate into an ACTIVE runtime constraint.
    """

    def __init__(self, generator_id: str):
        if not generator_id:
            raise ValueError("generator_id is required")
        self.generator_id = generator_id

    def generate(
        self,
        rejection: CausalRejection,
        *,
        confidence_lt: float = 0.60,
        allow_if_compensating_guard: bool = True,
        ablate_attribution: bool = False,
    ) -> ConstraintCandidate:
        if not rejection.causal_attribution and not ablate_attribution:
            raise ValueError("causal attribution is required before candidate generation")

        mutation_class = (
            "ATTRIBUTION_ABLATED"
            if ablate_attribution
            else rejection.mutation_class
        )
        attribution_value = "ABLATION" if ablate_attribution else rejection.causal_attribution
        attribution_hash = _canonical_hash(
            {
                "origin_rejection_id": rejection.rejection_id,
                "causal_attribution": attribution_value,
                "mutation_class": mutation_class,
            }
        )
        candidate_payload = {
            "origin_rejection_id": rejection.rejection_id,
            "generator_id": self.generator_id,
            "mutation_class": mutation_class,
            "confidence_lt": confidence_lt,
            "allow_if_compensating_guard": allow_if_compensating_guard,
            "attribution_hash": attribution_hash,
            "attribution_ablated": ablate_attribution,
            "protocol": CAUSAL_INHERITANCE_PROTOCOL,
        }
        candidate_id = "cand-" + _canonical_hash(candidate_payload)[:24]
        return ConstraintCandidate(candidate_id=candidate_id, **candidate_payload)


class IndependentAdjudicator:
    """Adjudicate a candidate under explicit generator/authorizer separation."""

    def __init__(self, adjudicator_id: str):
        if not adjudicator_id:
            raise ValueError("adjudicator_id is required")
        self.adjudicator_id = adjudicator_id

    def adjudicate(
        self,
        candidate: ConstraintCandidate,
        *,
        promote: bool,
        evidence_ok: bool,
    ) -> tuple[CausalConstraintRule, AdjudicationReceipt]:
        if self.adjudicator_id == candidate.generator_id:
            raise AuthoritySeparationError(
                "generator cannot adjudicate or promote its own constraint candidate"
            )

        if promote and evidence_ok:
            decision: Literal["PROMOTE", "HOLD", "REJECT"] = "PROMOTE"
            status = "ACTIVE"
        elif promote and not evidence_ok:
            decision = "REJECT"
            status = "DORMANT"
        else:
            decision = "HOLD"
            status = "DORMANT"

        constraint_id = "gci-" + _canonical_hash(
            {
                "candidate_id": candidate.candidate_id,
                "adjudicator_id": self.adjudicator_id,
                "decision": decision,
            }
        )[:24]

        rule = CausalConstraintRule(
            constraint_id=constraint_id,
            status=status,
            mutation_class=candidate.mutation_class,
            confidence_lt=candidate.confidence_lt,
            allow_if_compensating_guard=candidate.allow_if_compensating_guard,
        )

        receipt_payload = {
            "candidate_id": candidate.candidate_id,
            "origin_rejection_id": candidate.origin_rejection_id,
            "generator_id": candidate.generator_id,
            "adjudicator_id": self.adjudicator_id,
            "decision": decision,
            "evidence_ok": evidence_ok,
            "promoted_constraint_id": constraint_id,
            "protocol": CAUSAL_INHERITANCE_PROTOCOL,
        }
        receipt = AdjudicationReceipt(
            **receipt_payload,
            receipt_sha256=_canonical_hash(receipt_payload),
        )
        return rule, receipt
