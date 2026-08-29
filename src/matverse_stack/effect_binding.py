from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .memory import GeometricMemory


EFFECT_BINDING_PROTOCOL = "matverse.ocg.effect_binding/0.1.0"


class EffectBindingError(RuntimeError):
    """Raised when a proposed effect cannot be bound safely to its mutation."""


class MemoryAppendEffectProposal(BaseModel):
    """A narrowly scoped, machine-readable proposal to append one MNB.

    This is intentionally not a generic code executor. The only supported effect in
    v1.5 is an append through the stack's existing GeometricMemory persistence path.
    """

    model_config = ConfigDict(extra="forbid")

    effect_type: Literal["MNB_APPEND"] = "MNB_APPEND"
    content: str = Field(min_length=1, max_length=100_000)
    source: str = Field(default="governed_mutation", min_length=1, max_length=256)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EffectObservation(BaseModel):
    protocol: str = EFFECT_BINDING_PROTOCOL
    effect_type: Literal["MNB_APPEND"] = "MNB_APPEND"
    effect_status: Literal[
        "EXECUTED_OBSERVED",
        "READBACK_MISMATCH",
        "BLOCKED_NOT_EXECUTED",
        "NOT_EXECUTED_DECISION",
        "BINDING_REJECTED",
    ]
    effect_observed: bool
    execution_authorized: bool
    effect_payload_hash: str
    effect_state_hash_before: str
    effect_state_hash_after: str
    effect_count_before: int
    effect_count_after: int
    mnb_id: Optional[str] = None
    content_hash: Optional[str] = None
    readback_content_hash: Optional[str] = None
    readback_ok: bool = False
    reason: str


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def effect_payload_hash(effect: MemoryAppendEffectProposal) -> str:
    return canonical_hash(effect.model_dump())


def _persistent_snapshot(path: Path) -> tuple[str, int]:
    """Read persistence through a fresh GeometricMemory instance."""

    fresh = GeometricMemory(path)
    serialized = [item.model_dump() for item in fresh.ltm]
    return canonical_hash(serialized), len(serialized)


def observe_without_execution(
    memory: GeometricMemory,
    effect: MemoryAppendEffectProposal,
    *,
    final_decision: str,
    binding_rejected: bool = False,
) -> EffectObservation:
    before_hash, before_count = _persistent_snapshot(memory.path)
    after_hash, after_count = _persistent_snapshot(memory.path)
    if binding_rejected:
        status = "BINDING_REJECTED"
    elif final_decision == "BLOCK":
        status = "BLOCKED_NOT_EXECUTED"
    else:
        status = "NOT_EXECUTED_DECISION"
    return EffectObservation(
        effect_status=status,
        effect_observed=False,
        execution_authorized=False,
        effect_payload_hash=effect_payload_hash(effect),
        effect_state_hash_before=before_hash,
        effect_state_hash_after=after_hash,
        effect_count_before=before_count,
        effect_count_after=after_count,
        readback_ok=before_hash == after_hash and before_count == after_count,
        reason=(
            "mutation payload_hash does not bind the proposed effect"
            if binding_rejected
            else f"final_decision={final_decision}; effect execution skipped"
        ),
    )


def execute_memory_append(
    memory: GeometricMemory,
    effect: MemoryAppendEffectProposal,
    *,
    mutation_id: str,
) -> EffectObservation:
    """Execute one authorized MNB append and verify it by independent readback."""

    before_hash, before_count = _persistent_snapshot(memory.path)
    proposed_hash = effect_payload_hash(effect)
    content_hash = hashlib.sha256(effect.content.encode("utf-8")).hexdigest()

    mnb = memory.add(
        content=effect.content,
        source=effect.source,
        metadata={
            "effect_binding_protocol": EFFECT_BINDING_PROTOCOL,
            "mutation_id": mutation_id,
            "effect_payload_hash": proposed_hash,
            "proposal_metadata": effect.metadata,
        },
    )

    # Independent readback: construct a fresh memory reader from the persisted file.
    fresh = GeometricMemory(memory.path)
    readback = fresh.get(mnb.mnb_id)
    after_hash, after_count = _persistent_snapshot(memory.path)

    readback_content_hash = readback.content_hash if readback else None
    readback_ok = bool(
        readback
        and readback.content == effect.content
        and readback.content_hash == content_hash
        and readback.metadata.get("mutation_id") == mutation_id
        and readback.metadata.get("effect_payload_hash") == proposed_hash
        and after_count == before_count + 1
        and after_hash != before_hash
    )

    return EffectObservation(
        effect_status="EXECUTED_OBSERVED" if readback_ok else "READBACK_MISMATCH",
        effect_observed=readback_ok,
        execution_authorized=True,
        effect_payload_hash=proposed_hash,
        effect_state_hash_before=before_hash,
        effect_state_hash_after=after_hash,
        effect_count_before=before_count,
        effect_count_after=after_count,
        mnb_id=mnb.mnb_id,
        content_hash=content_hash,
        readback_content_hash=readback_content_hash,
        readback_ok=readback_ok,
        reason="persisted MNB independently observed" if readback_ok else "persisted effect failed independent readback",
    )
