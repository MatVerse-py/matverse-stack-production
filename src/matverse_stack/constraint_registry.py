from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .constraint_gate import CausalConstraintRule


class ConstraintRegistryError(RuntimeError):
    pass


def _canonical_hash(value: Dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ConstraintRegistry:
    """Resolve authoritative typed constraints from a local canonical registry.

    The runtime never accepts a client-supplied rule as authority. The caller supplies
    only a constraint_id; this registry resolves the machine-readable rule. Missing,
    duplicate, malformed, or mismatched records fail closed.
    """

    def __init__(self, path: Optional[Path] = None):
        configured = os.getenv("CAUSAL_CONSTRAINTS_PATH")
        self.path = path or (Path(configured) if configured else None)

    def _load(self) -> list[Dict[str, Any]]:
        if self.path is None:
            raise ConstraintRegistryError("CAUSAL_CONSTRAINTS_PATH is not configured")
        if not self.path.exists():
            raise ConstraintRegistryError(f"constraint registry not found: {self.path}")

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConstraintRegistryError(f"constraint registry unreadable: {exc}") from exc

        records = raw.get("constraints") if isinstance(raw, dict) else raw
        if not isinstance(records, list):
            raise ConstraintRegistryError("constraint registry must be a list or contain a constraints list")
        return records

    def resolve(self, constraint_id: str) -> Tuple[CausalConstraintRule, str]:
        records = [record for record in self._load() if isinstance(record, dict) and record.get("constraint_id") == constraint_id]
        if len(records) != 1:
            raise ConstraintRegistryError(
                f"constraint_id must resolve exactly once; id={constraint_id!r}, matches={len(records)}"
            )

        record = records[0]
        try:
            rule = CausalConstraintRule(**record)
        except Exception as exc:
            raise ConstraintRegistryError(f"invalid constraint record for {constraint_id}: {exc}") from exc

        if rule.constraint_id != constraint_id:
            raise ConstraintRegistryError("resolved constraint_id does not match requested constraint_id")

        return rule, _canonical_hash(record)
