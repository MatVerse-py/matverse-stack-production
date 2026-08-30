from __future__ import annotations

import json

from matverse_stack.homeostasis import run_controlled_homeostasis


if __name__ == "__main__":
    report = run_controlled_homeostasis(gain=0.60, max_steps=5)
    print(
        json.dumps(
            report.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
