from __future__ import annotations

import json

from matverse_stack.instrumented_homeostasis import run_instrumented_experiment


if __name__ == "__main__":
    report = run_instrumented_experiment()
    print(json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
