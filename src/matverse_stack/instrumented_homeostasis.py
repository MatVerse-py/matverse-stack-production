from __future__ import annotations

import hashlib
import json
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .constraint_gate import CausalConstraintRule, MutationContext
from .constraint_registry import REGISTRY_SCHEMA, ConstraintRegistry
from .effect_binding import MemoryAppendEffectProposal, effect_payload_hash
from .ledger import Ledger
from .memory import GeometricMemory
from .service import MatVerseService

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX fallback
    resource = None


PROTOCOL = "matverse.ocg.instrumented_homeostasis/0.1.0"
PREREG_SHA256 = "85e73ac48847d711833974be05e5b8cc27809090c9b2771e369ed9acfc6533a1"
V1_ID = "86714849-d405-4c5f-beee-6b7c9376983d"
V2_ID = "2a3e5b66-683e-49eb-87bc-756814ec4ca2"
SOURCE_APP_ID = "692b9db11ff853fffafa174e"
BASELINE_UNITS = 20_000
PERTURBED_UNITS = 160_000
REGULATION_FACTOR = 0.5
MAX_STEPS = 5
REQUIRED_CPU_DEVIATION_REDUCTION = 0.60


class ProcessTelemetry(BaseModel):
    work_units: int
    wall_ms: float = Field(ge=0.0)
    process_cpu_ms: float = Field(ge=0.0)
    max_rss_kb: Optional[float] = Field(default=None, ge=0.0)
    digest: str


class InstrumentedPair(BaseModel):
    pair_id: str
    baseline: ProcessTelemetry
    control: ProcessTelemetry
    target_cpu_ms: float = Field(ge=0.0)
    regulated_history: List[ProcessTelemetry]
    regulated_final: ProcessTelemetry
    recovery_steps: int
    cpu_reduction: float
    recovered: bool


def _rss_kb() -> Optional[float]:
    if resource is None:
        return None
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _deterministic_work(units: int, seed: bytes) -> str:
    digest = hashlib.sha256(seed).digest()
    for index in range(units):
        digest = hashlib.sha256(digest + index.to_bytes(8, "big")).digest()
    return digest.hex()


def measure_process_work(units: int, seed: bytes) -> ProcessTelemetry:
    wall_before = time.perf_counter_ns()
    cpu_before = time.process_time_ns()
    digest = _deterministic_work(units, seed)
    cpu_after = time.process_time_ns()
    wall_after = time.perf_counter_ns()
    return ProcessTelemetry(
        work_units=units,
        wall_ms=(wall_after - wall_before) / 1_000_000.0,
        process_cpu_ms=(cpu_after - cpu_before) / 1_000_000.0,
        max_rss_kb=_rss_kb(),
        digest=digest,
    )


def run_resource_pair(pair_index: int) -> InstrumentedPair:
    seed = f"matverse-instrumented-homeostasis-{pair_index}".encode("utf-8")
    baseline = measure_process_work(BASELINE_UNITS, seed)
    control = measure_process_work(PERTURBED_UNITS, seed)

    # Derive the feedback target from the preregistered 60% minimum reduction,
    # rather than introducing a post-hoc CPU ratio. The target retains at most
    # 40% of the observed perturbation excess above the measured baseline.
    excess_cpu = max(0.0, control.process_cpu_ms - baseline.process_cpu_ms)
    target_cpu_ms = baseline.process_cpu_ms + (
        excess_cpu * (1.0 - REQUIRED_CPU_DEVIATION_REDUCTION)
    )
    units = PERTURBED_UNITS
    history: List[ProcessTelemetry] = []
    recovered = False

    for step in range(MAX_STEPS + 1):
        sample = measure_process_work(units, seed)
        history.append(sample)
        if sample.process_cpu_ms <= target_cpu_ms:
            recovered = True
            break
        if step >= MAX_STEPS:
            break
        units = max(BASELINE_UNITS, int(units * REGULATION_FACTOR))

    regulated_final = history[-1]
    cpu_reduction = 1.0 - (
        regulated_final.process_cpu_ms / max(control.process_cpu_ms, 1e-12)
    )
    return InstrumentedPair(
        pair_id=f"IH-{pair_index:02d}",
        baseline=baseline,
        control=control,
        target_cpu_ms=target_cpu_ms,
        regulated_history=history,
        regulated_final=regulated_final,
        recovery_steps=max(0, len(history) - 1),
        cpu_reduction=cpu_reduction,
        recovered=recovered,
    )


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_service(root: Path) -> MatVerseService:
    v1 = CausalConstraintRule(
        constraint_id=V1_ID,
        status="ACTIVE",
        mutation_class="UNSCOPED_WRITE",
        confidence_lt=0.80,
        allow_if_compensating_guard=False,
    )
    v2 = CausalConstraintRule(
        constraint_id=V2_ID,
        status="ACTIVE",
        mutation_class="UNSCOPED_WRITE",
        confidence_lt=0.60,
        allow_if_compensating_guard=True,
        supersedes=V1_ID,
    )
    records = [v1.model_dump(), v2.model_dump()]
    snapshot = _canonical_hash(records)
    registry_path = root / "causal_constraints.json"
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
    service.memory = GeometricMemory(root / "memory.json")
    service.ledger = Ledger(root / "ledger.jsonl")
    service.constraint_registry = ConstraintRegistry(
        registry_path,
        expected_snapshot_sha256=snapshot,
        expected_source_app_id=SOURCE_APP_ID,
    )
    return service


def run_stack_validation(root: Optional[Path] = None) -> Dict[str, Any]:
    if root is None:
        temp = tempfile.TemporaryDirectory(prefix="matverse-ih-v18-")
        root = Path(temp.name)
    else:
        temp = None
        root.mkdir(parents=True, exist_ok=True)

    try:
        service = _make_service(root)
        hazardous_effect = MemoryAppendEffectProposal(
            content="instrumented-homeostasis-hazard",
            source="instrumented_homeostasis_v1.8",
            metadata={"class": "hazardous"},
        )
        hazardous_mutation = MutationContext(
            mutation_id="ih-hazard-001",
            mutation_class="UNSCOPED_WRITE",
            confidence=0.40,
            compensating_guard=False,
            payload_hash=effect_payload_hash(hazardous_effect),
        )
        hazardous = service.execute_registered_effect(
            hazardous_mutation, V2_ID, hazardous_effect
        )

        safe_results = []
        for index in range(4):
            effect = MemoryAppendEffectProposal(
                content=f"instrumented-homeostasis-safe-{index}",
                source="instrumented_homeostasis_v1.8",
                metadata={"class": "safe_guarded", "index": index},
            )
            mutation = MutationContext(
                mutation_id=f"ih-safe-{index:03d}",
                mutation_class="UNSCOPED_WRITE",
                confidence=0.40,
                compensating_guard=True,
                payload_hash=effect_payload_hash(effect),
            )
            safe_results.append(service.execute_registered_effect(mutation, V2_ID, effect))

        fresh = GeometricMemory(service.memory.path)
        safe_false_blocks = sum(
            1 for result in safe_results if result["final_decision"] != "PASS"
        )
        return {
            "hazardous_final_decision": hazardous["final_decision"],
            "hazardous_effect_observed": hazardous["effect_observation"]["effect_observed"],
            "hazardous_effect_count": hazardous["effect_observation"]["effect_count_after"],
            "safe_cases": len(safe_results),
            "safe_false_blocks": safe_false_blocks,
            "safe_false_block_rate": safe_false_blocks / len(safe_results),
            "safe_persisted_count": len(fresh.ltm),
            "safe_readback_ok": all(
                result["effect_observation"]["readback_ok"] for result in safe_results
            ),
            "ledger_entries": len(service.ledger.entries),
            "ledger_integrity": service.ledger.verify_integrity(),
            "registry_snapshot_hash": safe_results[0]["registry_snapshot_hash"],
        }
    finally:
        if temp is not None:
            temp.cleanup()


def run_instrumented_experiment(n_pairs: int = 8) -> Dict[str, Any]:
    pairs = [run_resource_pair(index) for index in range(n_pairs)]
    reductions = [pair.cpu_reduction for pair in pairs]
    stack = run_stack_validation()
    primary_pass = (
        all(pair.regulated_final.process_cpu_ms < pair.control.process_cpu_ms for pair in pairs)
        and statistics.median(reductions) >= REQUIRED_CPU_DEVIATION_REDUCTION
        and max(pair.recovery_steps for pair in pairs) <= 4
        and all(pair.recovered for pair in pairs)
        and stack["ledger_integrity"]
        and stack["hazardous_effect_count"] == 0
        and stack["safe_false_block_rate"] <= 0.0
        and stack["safe_readback_ok"]
    )
    return {
        "experiment_id": "OCG-STACK-INSTRUMENTED-HOMEOSTASIS-2026-08-29",
        "protocol": PROTOCOL,
        "scope": "OCG_STACK_CANDIDATE_INSTRUMENTED_HOMEOSTASIS_ONLY",
        "prereg_sha256": PREREG_SHA256,
        "n_pairs": n_pairs,
        "median_cpu_reduction": statistics.median(reductions),
        "min_cpu_reduction": min(reductions),
        "max_recovery_steps": max(pair.recovery_steps for pair in pairs),
        "all_pairs_final_cpu_better": all(
            pair.regulated_final.process_cpu_ms < pair.control.process_cpu_ms for pair in pairs
        ),
        "all_pairs_recovered": all(pair.recovered for pair in pairs),
        "max_observed_rss_kb": max(
            [sample.max_rss_kb or 0.0 for pair in pairs for sample in pair.regulated_history]
            + [0.0]
        ),
        "pairs": [pair.model_dump() for pair in pairs],
        "stack_validation": stack,
        "primary_result": "PASS" if primary_pass else "FAIL",
        "claim_boundary": {
            "instrumented_process_telemetry_only": True,
            "not_biological_homeostasis": True,
            "not_production_autonomy": True,
            "not_os_resource_manager": True,
            "not_external_reproduction": True,
        },
    }
