from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .instrumented_homeostasis import run_stack_validation

PROTOCOL = "matverse.ocg.deployed_closed_loop_homeostasis_preflight/0.1.0"
PREREG_SHA256 = "427f59f607cdbfa0c759ce369d605f1cd9905a9f91d1448ab683a761c7196fc5"
BASELINE_UNITS = 20_000
PERTURBED_UNITS = 160_000
REGULATION_FACTOR = 0.5
MAX_RECOVERY_STEPS = 4
MIN_DEVIATION_REDUCTION = 0.60


class WorkObservation(BaseModel):
    work_units: int
    wall_ms: float = Field(ge=0.0)
    process_cpu_ms: float = Field(ge=0.0)
    rss_kb: Optional[float] = Field(default=None, ge=0.0)
    digest: str


class PairReceipt(BaseModel):
    pair_id: str
    baseline: WorkObservation
    control: WorkObservation
    target_cpu_ms: float = Field(ge=0.0)
    regulated_history: List[WorkObservation]
    regulated_final: WorkObservation
    recovery_steps: int
    deviation_reduction: float
    recovered: bool


def _rss_kb() -> Optional[float]:
    try:
        import resource

        return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, OSError):
        return None


def _work(units: int, seed: bytes) -> str:
    digest = hashlib.sha256(seed).digest()
    for index in range(units):
        digest = hashlib.sha256(digest + index.to_bytes(8, "big")).digest()
    return digest.hex()


def _measure(units: int, seed: bytes) -> WorkObservation:
    wall_before = time.perf_counter_ns()
    cpu_before = time.process_time_ns()
    digest = _work(units, seed)
    cpu_after = time.process_time_ns()
    wall_after = time.perf_counter_ns()
    return WorkObservation(
        work_units=units,
        wall_ms=(wall_after - wall_before) / 1_000_000.0,
        process_cpu_ms=(cpu_after - cpu_before) / 1_000_000.0,
        rss_kb=_rss_kb(),
        digest=digest,
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class PersistentReceiptLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        result: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result.append(json.loads(line))
        return result

    def append(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        prior = self.entries()
        prev_hash = prior[-1]["entry_hash"] if prior else "GENESIS"
        body = {
            "index": len(prior),
            "timestamp_ns": time.time_ns(),
            "event_type": event_type,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        entry = dict(body)
        entry["entry_hash"] = _sha(body)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(entry) + "\n")
        return entry

    def verify(self) -> bool:
        expected_prev = "GENESIS"
        for expected_index, entry in enumerate(self.entries()):
            body = {
                "index": entry.get("index"),
                "timestamp_ns": entry.get("timestamp_ns"),
                "event_type": entry.get("event_type"),
                "payload": entry.get("payload"),
                "prev_hash": entry.get("prev_hash"),
            }
            if entry.get("index") != expected_index:
                return False
            if entry.get("prev_hash") != expected_prev:
                return False
            if entry.get("entry_hash") != _sha(body):
                return False
            expected_prev = entry["entry_hash"]
        return True


class DeployedCandidateRuntime:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = PersistentReceiptLedger(root / "v1.9_receipts.jsonl")
        self.last_report_path = root / "v1.9_last_report.json"
        self.started_ns = time.time_ns()

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "protocol": PROTOCOL,
            "scope": "OCG_DEPLOYMENT_PREFLIGHT_HTTP_SERVICE_ONLY",
            "ledger_integrity": self.ledger.verify(),
            "receipt_count": len(self.ledger.entries()),
            "started_ns": self.started_ns,
            "deployment_boundary": "HOLD_EXTERNAL_PROVIDER_UNAVAILABLE",
        }

    def run_pair(self, pair_index: int) -> PairReceipt:
        seed = f"matverse-v1.9-http-{pair_index}".encode("utf-8")
        baseline = _measure(BASELINE_UNITS, seed)
        control = _measure(PERTURBED_UNITS, seed)
        deviation = max(0.0, control.process_cpu_ms - baseline.process_cpu_ms)
        target_cpu_ms = baseline.process_cpu_ms + (1.0 - MIN_DEVIATION_REDUCTION) * deviation

        units = PERTURBED_UNITS
        history: List[WorkObservation] = []
        recovered = False
        for step in range(MAX_RECOVERY_STEPS + 1):
            sample = _measure(units, seed)
            history.append(sample)
            self.ledger.append(
                "controller_observation",
                {
                    "pair_index": pair_index,
                    "step": step,
                    "work_units": units,
                    "process_cpu_ms": sample.process_cpu_ms,
                    "wall_ms": sample.wall_ms,
                    "rss_kb": sample.rss_kb,
                    "target_cpu_ms": target_cpu_ms,
                },
            )
            if sample.process_cpu_ms <= target_cpu_ms:
                recovered = True
                break
            if step >= MAX_RECOVERY_STEPS:
                break
            next_units = max(BASELINE_UNITS, int(units * REGULATION_FACTOR))
            self.ledger.append(
                "controller_action",
                {
                    "pair_index": pair_index,
                    "step": step,
                    "from_work_units": units,
                    "to_work_units": next_units,
                    "reason": "observed_cpu_above_feedback_target",
                },
            )
            units = next_units

        final = history[-1]
        final_deviation = max(0.0, final.process_cpu_ms - baseline.process_cpu_ms)
        deviation_reduction = 1.0 if deviation <= 1e-12 else 1.0 - (final_deviation / deviation)
        pair = PairReceipt(
            pair_id=f"DH-{pair_index:02d}",
            baseline=baseline,
            control=control,
            target_cpu_ms=target_cpu_ms,
            regulated_history=history,
            regulated_final=final,
            recovery_steps=max(0, len(history) - 1),
            deviation_reduction=deviation_reduction,
            recovered=recovered,
        )
        self.ledger.append("pair_receipt", pair.model_dump())
        return pair

    def run_experiment(self) -> Dict[str, Any]:
        pairs = [self.run_pair(index) for index in range(8)]
        stack_root = self.root / "governed_effect_validation"
        stack = run_stack_validation(stack_root)
        reductions = [pair.deviation_reduction for pair in pairs]
        preflight_pass = (
            all(pair.regulated_final.process_cpu_ms < pair.control.process_cpu_ms for pair in pairs)
            and statistics.median(reductions) >= MIN_DEVIATION_REDUCTION
            and max(pair.recovery_steps for pair in pairs) <= MAX_RECOVERY_STEPS
            and all(pair.recovered for pair in pairs)
            and stack["hazardous_effect_count"] == 0
            and stack["safe_false_block_rate"] == 0.0
            and stack["safe_readback_ok"]
            and stack["ledger_integrity"]
            and self.ledger.verify()
        )
        report = {
            "experiment_id": "OCG-DEPLOYED-CLOSED-LOOP-HOMEOSTASIS-2026-08-29-PREFLIGHT",
            "protocol": PROTOCOL,
            "prereg_sha256": PREREG_SHA256,
            "scope": "OCG_DEPLOYMENT_PREFLIGHT_HTTP_SERVICE_ONLY",
            "n_pairs": 8,
            "median_cpu_deviation_reduction": statistics.median(reductions),
            "min_cpu_deviation_reduction": min(reductions),
            "max_recovery_steps": max(pair.recovery_steps for pair in pairs),
            "all_test_final_cpu_below_control": all(
                pair.regulated_final.process_cpu_ms < pair.control.process_cpu_ms for pair in pairs
            ),
            "all_pairs_recovered": all(pair.recovered for pair in pairs),
            "pairs": [pair.model_dump() for pair in pairs],
            "governed_state_validation": stack,
            "receipt_ledger_integrity": self.ledger.verify(),
            "receipt_count_before_summary": len(self.ledger.entries()),
            "preflight_result": "PASS" if preflight_pass else "FAIL",
            "deployed_primary_result": "HOLD",
            "deployment_boundary": {
                "replit_attempt": "BLOCKED_REQUIRES_ACTIVE_SUBSCRIPTION",
                "base44_external_sandbox": "BLOCKED_REQUIRES_BUILDER_PLAN",
                "external_persistent_host_executed": False,
            },
            "claim_boundary": {
                "http_process_boundary_exercised": True,
                "process_restart_persistence_to_be_verified_by_client": True,
                "not_matverse_production": True,
                "not_biological_homeostasis": True,
                "not_general_os_resource_management": True,
                "not_external_reproduction": True,
                "not_ocg_scientific_class_proof": True,
            },
        }
        self.ledger.append(
            "experiment_summary",
            {
                "preflight_result": report["preflight_result"],
                "deployed_primary_result": report["deployed_primary_result"],
                "report_payload_sha256": _sha(report),
            },
        )
        report["receipt_count_after_summary"] = len(self.ledger.entries())
        report["receipt_ledger_integrity"] = self.ledger.verify()
        self.last_report_path.write_text(_canonical_json(report) + "\n", encoding="utf-8")
        return report

    def last_report(self) -> Optional[Dict[str, Any]]:
        if not self.last_report_path.exists():
            return None
        return json.loads(self.last_report_path.read_text(encoding="utf-8"))


DATA_ROOT = Path(os.environ.get("MATVERSE_V19_DATA_DIR", "/tmp/matverse-v1.9-service"))
runtime = DeployedCandidateRuntime(DATA_ROOT)
app = FastAPI(title="MatVerse OCG v1.9 Deployment Preflight", version="0.1.0")


@app.get("/health")
def health() -> Dict[str, Any]:
    return runtime.health()


@app.get("/state")
def state() -> Dict[str, Any]:
    return {"health": runtime.health(), "last_report": runtime.last_report()}


@app.get("/receipts")
def receipts() -> Dict[str, Any]:
    entries = runtime.ledger.entries()
    return {"count": len(entries), "integrity": runtime.ledger.verify(), "entries": entries}


@app.get("/ledger/verify")
def ledger_verify() -> Dict[str, Any]:
    return {"integrity": runtime.ledger.verify(), "count": len(runtime.ledger.entries())}


@app.post("/experiment/run")
def experiment_run() -> Dict[str, Any]:
    return runtime.run_experiment()
