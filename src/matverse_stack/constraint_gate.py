from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class MutationContext(BaseModel):
    """Machine-readable mutation attributes evaluated by a causal constraint."""

    mutation_id: str
    mutation_class: str
    confidence: float = Field(ge=0.0, le=1.0)
    compensating_guard: bool = False
    payload_hash: Optional[str] = None


class CausalConstraintRule(BaseModel):
    """Typed runtime binding for a persisted causal constraint.

    Free-text predicates are deliberately not interpreted here. A constraint must be
    translated into this explicit rule shape before it can affect runtime behavior.
    """

    constraint_id: str
    status: Literal["ACTIVE", "SUPERSEDED", "RETRACTED", "DORMANT"] = "ACTIVE"
    mutation_class: str
    confidence_lt: float = Field(ge=0.0, le=1.0)
    allow_if_compensating_guard: bool = False
    supersedes: Optional[str] = None


class ConstraintDecision(BaseModel):
    decision: Literal["BLOCK", "PASS", "INACTIVE"]
    activated_constraint_ids: List[str] = []
    reason: str


def evaluate_constraint(
    mutation: MutationContext,
    constraint: CausalConstraintRule,
) -> ConstraintDecision:
    """Evaluate one typed constraint deterministically and fail closed on a match."""

    if constraint.status != "ACTIVE":
        return ConstraintDecision(
            decision="INACTIVE",
            activated_constraint_ids=[],
            reason=f"constraint_status={constraint.status}",
        )

    if mutation.mutation_class != constraint.mutation_class:
        return ConstraintDecision(
            decision="PASS",
            activated_constraint_ids=[],
            reason="mutation_class_not_matched",
        )

    if mutation.confidence >= constraint.confidence_lt:
        return ConstraintDecision(
            decision="PASS",
            activated_constraint_ids=[],
            reason="confidence_above_or_equal_threshold",
        )

    if constraint.allow_if_compensating_guard and mutation.compensating_guard:
        return ConstraintDecision(
            decision="PASS",
            activated_constraint_ids=[],
            reason="compensating_guard_bypass",
        )

    return ConstraintDecision(
        decision="BLOCK",
        activated_constraint_ids=[constraint.constraint_id],
        reason="active_constraint_matched",
    )
