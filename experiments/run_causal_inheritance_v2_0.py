from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path

from matverse_stack.causal_inheritance import (
    AuthoritySeparationError,
    CausalRejection,
    ConstraintGenerator,
    IndependentAdjudicator,
)
from matverse_stack.constraint_gate import MutationContext
from matverse_stack.ledger import Ledger
from matverse_stack.service import MatVerseService


ROOT = Path(__file__).resolve().parents[1]
PREREG_PATH = ROOT / "experiments" / "causal_inheritance_v2.0_prereg_v1.1.json"
EXPECTED_PREREG_SHA256 = "38f71dba67894dcede6884a074c756e1beeedc05e9fed535773d17ca04ba3765"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _initial_state(prereg: dict) -> dict:
    return dict(prereg["design"]["common_initial_state"])


def _service(ledger_path: Path) -> MatVerseService:
    service = MatVerseService.__new__(MatVerseService)
    service.ledger = Ledger(ledger_path)
    return service


def _exact_two_sided_sign_p(a_only: int, b_only: int) -> float:
    """Exact paired sign/binomial p-value for discordant pairs.

    Reported only as descriptive evidence over the frozen fixture set; it is not a
    population-inference claim.
    """
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def run_experiment() -> dict:
    prereg_sha = _sha256_file(PREREG_PATH)
    if prereg_sha != EXPECTED_PREREG_SHA256:
        raise RuntimeError(
            f"preregistration hash mismatch: {prereg_sha} != {EXPECTED_PREREG_SHA256}"
        )
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))

    rejection = CausalRejection(
        rejection_id="reject-unscoped-write-001",
        mutation_class=prereg["design"]["mutation_class"],
        observed_confidence=0.20,
        reason="unscoped persistent write rejected by governed state policy",
        causal_attribution="unsafe_unscoped_persistent_write",
        context_id="origin-context-A",
        substrate_id="origin-substrate-A",
    )
    placebo_rejection = CausalRejection(
        rejection_id="reject-placebo-001",
        mutation_class="PLACEBO_UNRELATED_CLASS",
        observed_confidence=0.20,
        reason="placebo control rejection",
        causal_attribution="unrelated_placebo_cause",
        context_id="origin-context-placebo",
        substrate_id="origin-substrate-A",
    )

    generator = ConstraintGenerator("generator-A")
    adjudicator = IndependentAdjudicator("adjudicator-B")

    candidate = generator.generate(
        rejection,
        confidence_lt=prereg["design"]["constraint_threshold"],
        allow_if_compensating_guard=prereg["design"]["allow_if_compensating_guard"],
    )
    rule_a, receipt_a = adjudicator.adjudicate(
        candidate, promote=True, evidence_ok=True
    )
    rule_b, receipt_b = adjudicator.adjudicate(
        candidate, promote=False, evidence_ok=True
    )

    placebo_candidate = generator.generate(
        placebo_rejection,
        confidence_lt=prereg["design"]["constraint_threshold"],
        allow_if_compensating_guard=prereg["design"]["allow_if_compensating_guard"],
    )
    rule_c, receipt_c = adjudicator.adjudicate(
        placebo_candidate, promote=True, evidence_ok=True
    )

    ablated_candidate = generator.generate(
        rejection,
        confidence_lt=prereg["design"]["constraint_threshold"],
        allow_if_compensating_guard=prereg["design"]["allow_if_compensating_guard"],
        ablate_attribution=True,
    )
    rule_d, receipt_d = adjudicator.adjudicate(
        ablated_candidate, promote=True, evidence_ok=True
    )

    authority_ablation_rejected = False
    try:
        IndependentAdjudicator("generator-A").adjudicate(
            candidate, promote=True, evidence_ok=True
        )
    except AuthoritySeparationError:
        authority_ablation_rejected = True

    rules = {
        "A_PROMOTED_MATCHING": rule_a,
        "B_NOT_PROMOTED": rule_b,
        "C_PLACEBO": rule_c,
        "D_ATTRIBUTION_ABLATION": rule_d,
    }

    hazardous_confidences = prereg["design"]["hazardous_confidences"]
    high_confidences = prereg["design"]["high_confidence_negative_controls"]
    guard_confidences = prereg["design"]["compensating_guard_negative_controls"]

    with tempfile.TemporaryDirectory() as td:
        service = _service(Path(td) / "causal_inheritance_ledger.jsonl")
        fixture_results = []
        arm_blocks = {arm: 0 for arm in rules}
        same_input_state_all = True
        baseline_sgsi_pass_all = True
        high_conf_false_blocks = 0
        guard_false_blocks = 0
        high_control_evaluations = 0
        guard_control_evaluations = 0

        for fixture_index, confidence in enumerate(hazardous_confidences):
            mutation = MutationContext(
                mutation_id=f"hazard-{fixture_index:02d}",
                mutation_class=prereg["design"]["mutation_class"],
                confidence=confidence,
                compensating_guard=False,
                payload_hash=hashlib.sha256(
                    f"hazard-payload-{fixture_index:02d}".encode("utf-8")
                ).hexdigest(),
            )
            arm_results = {}
            input_hashes = set()
            state_hashes = set()

            for arm, rule in rules.items():
                result = service.evaluate_mutation(
                    mutation,
                    rule,
                    initial_state=_initial_state(prereg),
                    constraint_authority="INDEPENDENT_ADJUDICATION_LAB_RECEIPT",
                )
                blocked = result["final_decision"] == "BLOCK"
                arm_blocks[arm] += int(blocked)
                baseline_sgsi_pass_all = baseline_sgsi_pass_all and (
                    result["sgsi_decision"] == "PASS"
                )
                input_hashes.add(result["input_hash"])
                state_hashes.add(result["state_hash_before"])
                arm_results[arm] = {
                    "blocked": blocked,
                    "final_decision": result["final_decision"],
                    "constraint_decision": result["constraint_decision"]["decision"],
                    "activated_constraint_ids": result["activated_constraint_ids"],
                    "input_hash": result["input_hash"],
                    "state_hash_before": result["state_hash_before"],
                }

            fixture_same = len(input_hashes) == 1 and len(state_hashes) == 1
            same_input_state_all = same_input_state_all and fixture_same
            fixture_results.append(
                {
                    "fixture_id": f"CI-{fixture_index:02d}",
                    "confidence": confidence,
                    "same_input_and_state_across_arms": fixture_same,
                    "arms": arm_results,
                }
            )

        for fixture_index, confidence in enumerate(high_confidences):
            mutation = MutationContext(
                mutation_id=f"safe-high-confidence-{fixture_index:02d}",
                mutation_class=prereg["design"]["mutation_class"],
                confidence=confidence,
                compensating_guard=False,
            )
            for rule in rules.values():
                result = service.evaluate_mutation(
                    mutation,
                    rule,
                    initial_state=_initial_state(prereg),
                    constraint_authority="INDEPENDENT_ADJUDICATION_LAB_RECEIPT",
                )
                high_control_evaluations += 1
                if result["final_decision"] != "PASS":
                    high_conf_false_blocks += 1

        for fixture_index, confidence in enumerate(guard_confidences):
            mutation = MutationContext(
                mutation_id=f"safe-compensating-guard-{fixture_index:02d}",
                mutation_class=prereg["design"]["mutation_class"],
                confidence=confidence,
                compensating_guard=True,
            )
            for rule in rules.values():
                result = service.evaluate_mutation(
                    mutation,
                    rule,
                    initial_state=_initial_state(prereg),
                    constraint_authority="INDEPENDENT_ADJUDICATION_LAB_RECEIPT",
                )
                guard_control_evaluations += 1
                if result["final_decision"] != "PASS":
                    guard_false_blocks += 1

        ledger_integrity = service.ledger.verify_integrity()

    n = len(hazardous_confidences)
    rates = {arm: arm_blocks[arm] / n for arm in rules}
    y_a = [int(x["arms"]["A_PROMOTED_MATCHING"]["blocked"]) for x in fixture_results]
    y_b = [int(x["arms"]["B_NOT_PROMOTED"]["blocked"]) for x in fixture_results]
    y_c = [int(x["arms"]["C_PLACEBO"]["blocked"]) for x in fixture_results]
    y_d = [int(x["arms"]["D_ATTRIBUTION_ABLATION"]["blocked"]) for x in fixture_results]

    ate_ab = sum(a - b for a, b in zip(y_a, y_b)) / n
    contrast_ac = sum(a - c for a, c in zip(y_a, y_c)) / n
    contrast_ad = sum(a - d for a, d in zip(y_a, y_d)) / n
    a_only = sum(a == 1 and b == 0 for a, b in zip(y_a, y_b))
    b_only = sum(a == 0 and b == 1 for a, b in zip(y_a, y_b))
    descriptive_p = _exact_two_sided_sign_p(a_only, b_only)

    criteria = {
        "a_blocks_all_hazardous": arm_blocks["A_PROMOTED_MATCHING"] == n,
        "b_blocks_none": arm_blocks["B_NOT_PROMOTED"] == 0,
        "c_blocks_none": arm_blocks["C_PLACEBO"] == 0,
        "d_blocks_none": arm_blocks["D_ATTRIBUTION_ABLATION"] == 0,
        "fixture_ate_ab_is_one": ate_ab == 1.0,
        "placebo_contrast_is_one": contrast_ac == 1.0,
        "attribution_contrast_is_one": contrast_ad == 1.0,
        "descriptive_p_lt_0_05": descriptive_p < 0.05,
        "high_conf_false_block_rate_zero": high_conf_false_blocks == 0,
        "compensating_guard_false_block_rate_zero": guard_false_blocks == 0,
        "authority_ablation_rejected": authority_ablation_rejected,
        "same_input_and_state_across_arms": same_input_state_all,
        "baseline_sgsi_pass_all": baseline_sgsi_pass_all,
        "ledger_integrity": ledger_integrity,
    }
    primary_result = "PASS" if all(criteria.values()) else "FAIL"

    return {
        "schema": "matverse.causal_inheritance_experiment_report/1.1",
        "experiment_id": prereg["experiment_id"],
        "scope": prereg["scope"],
        "prereg_sha256": prereg_sha,
        "primary_result": primary_result,
        "n_hazardous_fixtures": n,
        "arm_block_counts": arm_blocks,
        "arm_block_rates": rates,
        "fixture_ate_A_minus_B": ate_ab,
        "fixture_placebo_contrast_A_minus_C": contrast_ac,
        "fixture_attribution_contrast_A_minus_D": contrast_ad,
        "a_only_discordant": a_only,
        "b_only_discordant": b_only,
        "paired_exact_sign_binomial_p_descriptive": descriptive_p,
        "high_confidence_negative_control": {
            "evaluations": high_control_evaluations,
            "false_blocks": high_conf_false_blocks,
            "false_block_rate": high_conf_false_blocks / high_control_evaluations,
        },
        "compensating_guard_negative_control": {
            "evaluations": guard_control_evaluations,
            "false_blocks": guard_false_blocks,
            "false_block_rate": guard_false_blocks / guard_control_evaluations,
        },
        "authority_ablation_rejected": authority_ablation_rejected,
        "adjudication_receipts": {
            "A_PROMOTED_MATCHING": receipt_a.model_dump(),
            "B_NOT_PROMOTED": receipt_b.model_dump(),
            "C_PLACEBO": receipt_c.model_dump(),
            "D_ATTRIBUTION_ABLATION": receipt_d.model_dump(),
        },
        "criteria": criteria,
        "fixtures": fixture_results,
        "claim_boundary": prereg["claim_boundary"],
        "interpretation": (
            "PASS establishes a fixture-level algorithmic causal effect of an independently "
            "promoted, rejection-derived matching constraint on later governed decisions. "
            "It does not establish population-level, real-world, cross-substrate, external, "
            "biological, or OCG-class causality."
        ),
    }


def main() -> int:
    report = run_experiment()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["primary_result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
