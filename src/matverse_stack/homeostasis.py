from __future__ import annotations

from statistics import median
from typing import Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


HOMEOSTASIS_PROTOCOL = "matverse.ocg.controlled_homeostasis/0.1.0"
TARGET_ERROR = 0.05


class ViabilityVector(BaseModel):
    """Normalized deviation vector for the controlled homeostasis harness.

    Values are operational test coordinates, not biological variables. Zero is the
    preregistered target for resource, informational and constitutional deviation.
    """

    model_config = ConfigDict(extra="forbid")

    resource: float = Field(ge=0.0, le=1.0)
    informational: float = Field(ge=0.0, le=1.0)
    constitutional: float = Field(ge=0.0, le=1.0)

    def error(self) -> float:
        return (self.resource + self.informational + self.constitutional) / 3.0


class HomeostasisArmResult(BaseModel):
    arm: Literal["CONTROL_NO_REGULATION", "REGULATED_PROPORTIONAL"]
    perturbation_id: str
    initial_state: ViabilityVector
    perturbed_state: ViabilityVector
    final_state: ViabilityVector
    error_before: float
    error_after: float
    normalized_error_reduction: float
    recovery_latency_steps: int | None
    overshoot: float
    residual_error: float
    regulation_cost: float
    trajectory: List[ViabilityVector]


class HomeostasisPairResult(BaseModel):
    perturbation_id: str
    control: HomeostasisArmResult
    regulated: HomeostasisArmResult


class ControlledHomeostasisReport(BaseModel):
    protocol: str = HOMEOSTASIS_PROTOCOL
    n_pairs: int
    regulated_gain: float
    max_steps: int
    target_error: float = TARGET_ERROR
    pairs: List[HomeostasisPairResult]
    median_normalized_error_reduction: float
    max_recovery_latency_steps: int
    max_overshoot: float
    max_residual_error: float
    all_pairs_regulated_better: bool
    primary_result: Literal["PASS", "FAIL"]


def apply_perturbation(initial: ViabilityVector, delta: ViabilityVector) -> ViabilityVector:
    return ViabilityVector(
        resource=min(1.0, initial.resource + delta.resource),
        informational=min(1.0, initial.informational + delta.informational),
        constitutional=min(1.0, initial.constitutional + delta.constitutional),
    )


def _regulated_step(state: ViabilityVector, gain: float) -> ViabilityVector:
    if not 0.0 < gain <= 1.0:
        raise ValueError("gain must be in (0, 1]")
    return ViabilityVector(
        resource=max(0.0, state.resource * (1.0 - gain)),
        informational=max(0.0, state.informational * (1.0 - gain)),
        constitutional=max(0.0, state.constitutional * (1.0 - gain)),
    )


def run_arm(
    *,
    arm: Literal["CONTROL_NO_REGULATION", "REGULATED_PROPORTIONAL"],
    perturbation_id: str,
    initial: ViabilityVector,
    delta: ViabilityVector,
    gain: float,
    max_steps: int,
) -> HomeostasisArmResult:
    perturbed = apply_perturbation(initial, delta)
    error_before = perturbed.error()
    current = perturbed
    trajectory = [current]
    regulation_cost = 0.0
    recovery_latency_steps: int | None = None

    for step in range(1, max_steps + 1):
        if arm == "REGULATED_PROPORTIONAL":
            next_state = _regulated_step(current, gain)
            regulation_cost += (
                abs(current.resource - next_state.resource)
                + abs(current.informational - next_state.informational)
                + abs(current.constitutional - next_state.constitutional)
            )
            current = next_state
        trajectory.append(current)
        if recovery_latency_steps is None and current.error() <= TARGET_ERROR:
            recovery_latency_steps = step

    error_after = current.error()
    reduction = 0.0 if error_before == 0 else (error_before - error_after) / error_before
    overshoot = max(
        0.0,
        -current.resource,
        -current.informational,
        -current.constitutional,
    )

    return HomeostasisArmResult(
        arm=arm,
        perturbation_id=perturbation_id,
        initial_state=initial,
        perturbed_state=perturbed,
        final_state=current,
        error_before=error_before,
        error_after=error_after,
        normalized_error_reduction=reduction,
        recovery_latency_steps=recovery_latency_steps,
        overshoot=overshoot,
        residual_error=error_after,
        regulation_cost=regulation_cost,
        trajectory=trajectory,
    )


def preregistered_perturbations() -> List[tuple[str, ViabilityVector]]:
    raw = [
        ("P01", (0.80, 0.70, 0.60)),
        ("P02", (0.60, 0.90, 0.70)),
        ("P03", (0.90, 0.50, 0.80)),
        ("P04", (0.55, 0.65, 0.95)),
        ("P05", (0.75, 0.85, 0.45)),
        ("P06", (1.00, 0.40, 0.55)),
        ("P07", (0.45, 1.00, 0.65)),
        ("P08", (0.65, 0.55, 1.00)),
        ("P09", (0.70, 0.70, 0.70)),
        ("P10", (0.95, 0.80, 0.50)),
        ("P11", (0.50, 0.75, 0.90)),
        ("P12", (0.85, 0.60, 0.75)),
    ]
    return [
        (pid, ViabilityVector(resource=r, informational=i, constitutional=c))
        for pid, (r, i, c) in raw
    ]


def run_controlled_homeostasis(*, gain: float = 0.60, max_steps: int = 5) -> ControlledHomeostasisReport:
    initial = ViabilityVector(resource=0.0, informational=0.0, constitutional=0.0)
    pairs: List[HomeostasisPairResult] = []

    for perturbation_id, delta in preregistered_perturbations():
        control = run_arm(
            arm="CONTROL_NO_REGULATION",
            perturbation_id=perturbation_id,
            initial=initial,
            delta=delta,
            gain=gain,
            max_steps=max_steps,
        )
        regulated = run_arm(
            arm="REGULATED_PROPORTIONAL",
            perturbation_id=perturbation_id,
            initial=initial,
            delta=delta,
            gain=gain,
            max_steps=max_steps,
        )
        pairs.append(
            HomeostasisPairResult(
                perturbation_id=perturbation_id,
                control=control,
                regulated=regulated,
            )
        )

    reductions = [pair.regulated.normalized_error_reduction for pair in pairs]
    recovery_latencies = [
        pair.regulated.recovery_latency_steps
        for pair in pairs
        if pair.regulated.recovery_latency_steps is not None
    ]
    max_overshoot = max(pair.regulated.overshoot for pair in pairs)
    max_residual = max(pair.regulated.residual_error for pair in pairs)
    all_better = all(pair.regulated.error_after < pair.control.error_after for pair in pairs)
    median_reduction = median(reductions)
    max_latency = max(recovery_latencies) if recovery_latencies else max_steps + 1

    primary_pass = bool(
        len(pairs) == 12
        and all_better
        and median_reduction >= 0.90
        and max_latency <= 5
        and max_overshoot <= 0.05
        and max_residual <= 0.05
    )

    return ControlledHomeostasisReport(
        n_pairs=len(pairs),
        regulated_gain=gain,
        max_steps=max_steps,
        pairs=pairs,
        median_normalized_error_reduction=median_reduction,
        max_recovery_latency_steps=max_latency,
        max_overshoot=max_overshoot,
        max_residual_error=max_residual,
        all_pairs_regulated_better=all_better,
        primary_result="PASS" if primary_pass else "FAIL",
    )
