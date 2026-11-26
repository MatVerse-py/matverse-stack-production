from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Dict


class AnchorService:
    """
    Local receipt generator.

    This module does not submit transactions or verify on-chain inclusion.
    A configured Sepolia private key is intentionally ignored until a real,
    separately reviewed transaction sender and confirmation verifier exist.
    """

    def __init__(self):
        self.external_anchor_configured = bool(os.getenv("SEPOLIA_PRIVATE_KEY"))

    def anchor_hash(self, entry_hash: str) -> str:
        """Create a local receipt; this is not an on-chain anchor."""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", entry_hash):
            raise ValueError("entry_hash must be a 32-byte hexadecimal SHA-256 digest")

        timestamp_ns = time.time_ns()
        local_proof = hashlib.sha256(
            f"MATVERSE_LOCAL_RECEIPT_V1\0{entry_hash.lower()}\0{timestamp_ns}".encode("utf-8")
        ).hexdigest()
        return f"local-receipt-{local_proof}"

    def get_status(self) -> Dict[str, Any]:
        """Report the implemented local capability without claiming chain access."""
        return {
            "mode": "LOCAL_RECEIPT_ONLY",
            "enabled": False,
            "rpc_url": None,
            "can_anchor": False,
            "can_create_local_receipt": True,
            "external_anchor_configured": self.external_anchor_configured,
            "external_anchor_status": "NOT_IMPLEMENTED",
            "reason": (
                "No transaction sender or on-chain confirmation verifier is implemented; "
                "SEPOLIA_PRIVATE_KEY is not used."
            ),
        }
