from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .constraint_gate import CausalConstraintRule


REGISTRY_SCHEMA = "matverse.causal_constraint_registry/0.1.0"


class ConstraintRegistryError(RuntimeError):
    pass


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ConstraintRegistry:
    """Resolve authoritative typed constraints from a pinned canonical snapshot.

    The runtime never accepts a client-supplied rule as authority. The caller supplies
    only a constraint_id. A deployment must independently pin both the expected source
    app and the expected snapshot digest; missing, stale, duplicate, malformed, or
    mismatched authority fails closed.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        expected_snapshot_sha256: Optional[str] = None,
        expected_source_app_id: Optional[str] = None,
    ):
        configured_path = os.getenv("CAUSAL_CONSTRAINTS_PATH")
        self.path = path or (Path(configured_path) if configured_path else None)
        self.expected_snapshot_sha256 = (
            expected_snapshot_sha256 or os.getenv("CAUSAL_CONSTRAINTS_SNAPSHOT_SHA256")
        )
        self.expected_source_app_id = (
            expected_source_app_id or os.getenv("CAUSAL_CONSTRAINTS_SOURCE_APP_ID")
        )

    def _load(self) -> Tuple[list[Dict[str, Any]], str]:
        if self.path is None:
            raise ConstraintRegistryError("CAUSAL_CONSTRAINTS_PATH is not configured")
        if not self.expected_snapshot_sha256:
            raise ConstraintRegistryError("CAUSAL_CONSTRAINTS_SNAPSHOT_SHA256 is not configured")
        if not self.expected_source_app_id:
            raise ConstraintRegistryError("CAUSAL_CONSTRAINTS_SOURCE_APP_ID is not configured")
        if not self.path.exists():
            raise ConstraintRegistryError(f"constraint registry not found: {self.path}")

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConstraintRegistryError(f"constraint registry unreadable: {exc}") from exc

        if not isinstance(raw, dict):
            raise ConstraintRegistryError("constraint registry must be a versioned snapshot object")
        if raw.get("schema") != REGISTRY_SCHEMA:
            raise ConstraintRegistryError(
                f"constraint registry schema mismatch: {raw.get('schema')!r}"
            )
        if raw.get("source_app_id") != self.expected_source_app_id:
            raise ConstraintRegistryError("constraint registry source_app_id mismatch")

        records = raw.get("constraints")
        if not isinstance(records, list):
            raise ConstraintRegistryError("constraint registry constraints must be a list")
        if not all(isinstance(record, dict) for record in records):
            raise ConstraintRegistryError("constraint registry contains non-object records")

        computed_snapshot = _canonical_hash(records)
        declared_snapshot = raw.get("snapshot_sha256")
        if declared_snapshot != computed_snapshot:
            raise ConstraintRegistryError("constraint registry declared snapshot hash is invalid")
        if computed_snapshot != self.expected_snapshot_sha256:
            raise ConstraintRegistryError("constraint registry snapshot is not the deployment-pinned snapshot")

        return records, computed_snapshot

    def resolve(self, constraint_id: str) -> Tuple[CausalConstraintRule, str, str]:
        records, snapshot_hash = self._load()
        matches = [record for record in records if record.get("constraint_id") == constraint_id]
        if len(matches) != 1:
            raise ConstraintRegistryError(
                f"constraint_id must resolve exactly once; id={constraint_id!r}, matches={len(matches)}"
            )

        record = matches[0]
        try:
            rule = CausalConstraintRule(**record)
        except Exception as exc:
            raise ConstraintRegistryError(f"invalid constraint record for {constraint_id}: {exc}") from exc

        if rule.constraint_id != constraint_id:
            raise ConstraintRegistryError("resolved constraint_id does not match requested constraint_id")

        return rule, _canonical_hash(record), snapshot_hash
