import hashlib
from pathlib import Path

from experiments.run_deployed_closed_loop_homeostasis_v1.9_preflight import run_preflight
from matverse_stack.deployed_homeostasis_service import PREREG_SHA256


def test_v19_preregistration_hash_is_frozen():
    path = Path("experiments/deployed_closed_loop_homeostasis_v1.9_prereg.json")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PREREG_SHA256


def test_v19_http_process_restart_preflight_passes_but_deployment_stays_hold():
    report = run_preflight()
    run = report["preflight_run"]

    assert report["preflight_result"] == "PASS"
    assert report["deployed_primary_result"] == "HOLD"
    assert report["service_process_restarted"] is True
    assert report["restart_readback_ok"] is True
    assert report["ledger_after_restart"]["integrity"] is True
    assert run["n_pairs"] == 8
    assert run["median_cpu_deviation_reduction"] >= 0.60
    assert run["max_recovery_steps"] <= 4
    assert run["all_test_final_cpu_below_control"] is True
    assert run["all_pairs_recovered"] is True
    assert run["governed_state_validation"]["hazardous_effect_count"] == 0
    assert run["governed_state_validation"]["safe_false_block_rate"] == 0.0
    assert run["governed_state_validation"]["ledger_integrity"] is True
