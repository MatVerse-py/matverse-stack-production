from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .config import LEDGER_PATH
from .utils import safe_append_jsonl


ZERO_HASH = "0" * 64


def canonical_hash(value: Any) -> str:
    """Calcula SHA-256 sobre uma representação JSON determinística."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class LedgerEntry(BaseModel):
    index: int
    timestamp: float = Field(default_factory=time.time)
    event_type: str
    payload: Dict[str, Any]
    prev_hash: str = ""
    entry_hash: str = ""
    merkle_receipt: str = ""
    anchor_tx: Optional[str] = None

    def calculate_hash(self) -> str:
        """Hash do bloco, excluindo o anchor para permitir ancoragem posterior."""
        return canonical_hash(
            {
                "index": self.index,
                "timestamp": self.timestamp,
                "event_type": self.event_type,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "merkle_receipt": self.merkle_receipt,
            }
        )


class Ledger:
    """Ledger append-only local, com Genesis obrigatório e receipts verificáveis."""

    def __init__(self, path: Path = LEDGER_PATH):
        self.path = path
        self.entries: List[LedgerEntry] = []
        self._load_or_genesis()

    @staticmethod
    def _receipt_for(index: int, payload: Dict[str, Any], prev_hash: str) -> str:
        return canonical_hash(
            {
                "index": index,
                "payload": payload,
                "prev_hash": prev_hash,
            }
        )

    def _load_or_genesis(self) -> None:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        self.entries.append(LedgerEntry(**json.loads(line)))

        if not self.entries:
            self._create_genesis_block()

    def _create_genesis_block(self) -> None:
        payload = {
            "organism": "MatVerse",
            "version": "1.0.0",
            "author": "Mateus Alves Arêas",
            "orcid": "0009-0008-2973-4047",
            "message": "Genesis Block: autogênese soberana iniciada.",
            "network": "Sepolia Testnet / Local Sovereign Node",
        }
        receipt = self._receipt_for(0, payload, ZERO_HASH)
        entry = LedgerEntry(
            index=0,
            event_type="GENESIS",
            payload=payload,
            prev_hash=ZERO_HASH,
            merkle_receipt=receipt,
        )
        entry.entry_hash = entry.calculate_hash()
        self.entries.append(entry)
        self._rewrite_jsonl()

    def _rewrite_jsonl(self) -> None:
        """Reescreve o JSONL via arquivo temporário e replace atômico."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for entry in self.entries:
                    handle.write(json.dumps(entry.model_dump(), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def add_entry(
        self,
        event_type: str,
        payload: Dict[str, Any],
        anchor_tx: Optional[str] = None,
    ) -> LedgerEntry:
        previous = self.entries[-1] if self.entries else None
        prev_hash = previous.entry_hash if previous else ZERO_HASH
        index = previous.index + 1 if previous else 0
        receipt = self._receipt_for(index, payload, prev_hash)

        entry = LedgerEntry(
            index=index,
            event_type=event_type,
            payload=payload,
            prev_hash=prev_hash,
            merkle_receipt=receipt,
            anchor_tx=anchor_tx,
        )
        entry.entry_hash = entry.calculate_hash()
        self.entries.append(entry)
        safe_append_jsonl(self.path, entry.model_dump())
        return entry

    def set_anchor(self, index: int, anchor_ref: str) -> LedgerEntry:
        """Anexa a referência on-chain/local sem alterar o hash do bloco."""
        for entry in self.entries:
            if entry.index == index:
                entry.anchor_tx = anchor_ref
                self._rewrite_jsonl()
                return entry
        raise ValueError(f"Ledger entry {index} not found")

    def merkle_root(self) -> str:
        """Calcula a raiz Merkle dos receipts de todos os blocos atuais."""
        nodes = [entry.merkle_receipt for entry in self.entries]
        if not nodes:
            return ZERO_HASH
        while len(nodes) > 1:
            if len(nodes) % 2:
                nodes.append(nodes[-1])
            nodes = [
                hashlib.sha256((left + right).encode("ascii")).hexdigest()
                for left, right in zip(nodes[0::2], nodes[1::2])
            ]
        return nodes[0]

    def receipt(self, index: int) -> Dict[str, Any]:
        """Retorna o receipt de um bloco e sua prova de inclusão Merkle."""
        if index < 0 or index >= len(self.entries):
            raise ValueError(f"Ledger entry {index} not found")

        leaves = [entry.merkle_receipt for entry in self.entries]
        position = index
        proof: List[Dict[str, str]] = []
        level = leaves
        while len(level) > 1:
            if len(level) % 2:
                level = level + [level[-1]]
            sibling = position - 1 if position % 2 else position + 1
            proof.append(
                {
                    "position": "left" if position % 2 else "right",
                    "hash": level[sibling],
                }
            )
            level = [
                hashlib.sha256((left + right).encode("ascii")).hexdigest()
                for left, right in zip(level[0::2], level[1::2])
            ]
            position //= 2

        entry = self.entries[index]
        return {
            "index": entry.index,
            "entry_hash": entry.entry_hash,
            "leaf": entry.merkle_receipt,
            "merkle_root": self.merkle_root(),
            "proof": proof,
            "anchor_tx": entry.anchor_tx,
        }

    def get_all_entries(self) -> List[LedgerEntry]:
        return self.entries

    def verify_integrity(self) -> bool:
        """Verifica Genesis, hashes, receipts e encadeamento completo."""
        if not self.entries:
            return False
        for position, entry in enumerate(self.entries):
            expected_prev = ZERO_HASH if position == 0 else self.entries[position - 1].entry_hash
            if entry.index != position or entry.prev_hash != expected_prev:
                return False
            if entry.merkle_receipt != self._receipt_for(entry.index, entry.payload, entry.prev_hash):
                return False
            if entry.calculate_hash() != entry.entry_hash:
                return False
        return self.entries[0].event_type == "GENESIS"

    def status(self) -> Dict[str, Any]:
        return {
            "entries": len(self.entries),
            "genesis_hash": self.entries[0].entry_hash if self.entries else None,
            "latest_hash": self.entries[-1].entry_hash if self.entries else None,
            "merkle_root": self.merkle_root(),
            "integrity_ok": self.verify_integrity(),
            "latest_anchor": self.entries[-1].anchor_tx if self.entries else None,
        }

    def close_genesis(self, anchor_ref: Optional[str] = None) -> LedgerEntry:
        """Garante a existência do Genesis e registra sua referência de anchor."""
        genesis = self.entries[0]
        if anchor_ref and not genesis.anchor_tx:
            self.set_anchor(genesis.index, anchor_ref)
        return self.entries[0]

    def close_cycle(self, payload: Dict[str, Any], anchor_ref: Optional[str] = None) -> LedgerEntry:
        """Registra um fechamento de ciclo com receipt e anchor opcional."""
        entry = self.add_entry("AUTOGENESIS_CLOSED", payload)
        if anchor_ref:
            entry = self.set_anchor(entry.index, anchor_ref)
        return entry
