import hashlib
from pathlib import Path

from matverse_stack.instrumented_homeostasis import (
    BASELINE_UNITS,
    PERTURBED_UNITS,
    PREREG_SHA256,
    TARGET_CPU_RATIO,
    run_instrumented_experiment,
    run_resource_pair,
    run_stack_validation,
)


def test_preregistration_was_frozen_as_exact_artifact():
    path = Path("experiments/instrumented_homeostasis_v1.8_prereg.json")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PREREG_SHA256


def test_resource_pair_uses_observed_process_telemetry_and_recovers():
    pair = run_resource_pair(77)

    assert pair.baseline.work_units == BASELINE_UNITS
    assert pair.control.work_units == PERTURBED_UNITS
    assert pair.regulated_history[0].work_units == PERTURBED_UNITS
    assert pair.target_cpu_ms == pair.baseline.process_cpu_ms * TARGET_CPU_RATIO
    assert pair.regulated_final.process_cpu_ms <= pair.target_cpu_ms
    assert pair.regulated_final.process_cpu_ms < pair.control.process_cpu_ms
    assert pair.cpu_reduction >= 0.60
    assert pair.recovery_steps <= 4
    assert pair.recovered is True
    assert pair.control.wall_ms > 0
    assert pair.control.process_cpu_ms > 0


def test_stack_validation_blocks_hazard_and_preserves_safe_guarded_writes(tmp_path):
    result = run_stack_validation(tmp_path)

    assert result["hazardous_final_decision"] == "BLOCK"
    assert result["hazardous_effect_observed"] is False
    assert result["hazardous_effect_count"] == 0
    assert result["safe_cases"] == 4
    assert result["safe_false_blocks"] == 0
    assert result["safe_false_block_rate"] == 0.0
    assert result["safe_persisted_count"] == 4
    assert result["safe_readback_ok"] is True
    assert result["ledger_integrity"] is True


def test_full_instrumented_report_meets_preregistered_primary_criteria():
    report = run_instrumented_experiment()

    assert report["n_pairs"] == 8
    assert report["median_cpu_reduction"] >= 0.60
    assert report["max_recovery_steps"] <= 4
    assert report["all_pairs_final_cpu_better"] is True
    assert report["all_pairs_recovered"] is True
    assert report["stack_validation"]["hazardous_effect_count"] == 0
    assert report["stack_validation"]["safe_false_block_rate"] == 0.0
    assert report["stack_validation"]["ledger_integrity"] is True
    assert report["primary_result"] == "PASS"


def test_report_preserves_claim_boundary():
    report = run_instrumented_experiment(n_pairs=1)
    boundary = report["claim_boundary"]

    assert boundary["instrumented_process_telemetry_only"] is True
    assert boundary["not_biological_homeostasis"] is True
    assert boundary["not_production_autonomy"] is True
    assert boundary["not_os_resource_manager"] is True
    assert boundary["not_external_reproduction"] is True
