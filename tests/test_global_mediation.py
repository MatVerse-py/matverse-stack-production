from pathlib import Path

import pytest

from matverse_stack.ledger import Ledger
from matverse_stack.memory import GeometricMemory, MNB, UnmediatedMemoryWriteError
from matverse_stack.service import GLOBAL_MEDIATION_PROTOCOL, MatVerseService


class _DummyUpstream:
    def process_query(self, query: str, top_k: int):
        return {"mode": "fixture", "answer": f"answer:{query}", "results": []}


class _DummySGSI:
    def process(self, payload):
        return {
            "governance": {"decision": "PASS"},
            "skill": {"mutation_applied": False},
            "metrics": {"fixture": True},
        }


def _service(tmp_path: Path) -> MatVerseService:
    service = MatVerseService.__new__(MatVerseService)
    service.memory = GeometricMemory(tmp_path / "memory.json")
    service.ledger = Ledger(tmp_path / "ledger.jsonl")
    service.upstream_api = _DummyUpstream()
    service.sgsi = _DummySGSI()
    return service


def test_direct_geometric_memory_add_fails_closed_without_persistence(tmp_path):
    memory = GeometricMemory(tmp_path / "memory.json")

    with pytest.raises(UnmediatedMemoryWriteError):
        memory.add("forbidden", "test", {"surface": "direct"})

    assert GeometricMemory(memory.path).ltm == []
    assert not memory.path.exists()


def test_direct_memory_flush_fails_closed_even_after_in_memory_tampering(tmp_path):
    memory = GeometricMemory(tmp_path / "memory.json")
    memory.ltm.append(
        MNB(
            content="transient-only",
            content_hash="0" * 64,
            embedding=[0.0] * 16,
            geometric_anchor=[0.0] * 16,
            metadata={"surface": "manual_ltm_tamper"},
        )
    )

    with pytest.raises(UnmediatedMemoryWriteError):
        memory._save()

    assert not memory.path.exists()
    assert GeometricMemory(memory.path).ltm == []


def test_legacy_service_add_mnb_fails_closed_but_evidence_is_recorded(tmp_path):
    service = _service(tmp_path)
    before_entries = len(service.ledger.entries)

    with pytest.raises(UnmediatedMemoryWriteError):
        service.add_mnb("forbidden", "legacy", {"surface": "service"})

    assert GeometricMemory(service.memory.path).ltm == []
    assert len(service.ledger.entries) == before_entries + 1
    entry = service.ledger.entries[-1]
    assert entry.event_type == "unmediated_memory_write_blocked"
    assert entry.payload["status"] == "BLOCKED_GLOBAL_MEDIATION_REQUIRED"
    assert entry.payload["global_mediation_protocol"] == GLOBAL_MEDIATION_PROTOCOL
    assert service.ledger.verify_integrity() is True


def test_process_query_write_request_is_compatibility_block_not_memory_mutation(tmp_path):
    service = _service(tmp_path)

    result = service.process_query(
        "persist me",
        add_to_memory=True,
        metadata={"source": "legacy_process_query"},
    )

    assert result["memory_write_status"] == "BLOCKED_GLOBAL_MEDIATION_REQUIRED"
    assert result["mnb_id"] is None
    assert GeometricMemory(service.memory.path).ltm == []
    entry = service.ledger.entries[-1]
    assert entry.event_type == "query_processed"
    assert entry.payload["memory_write_requested"] is True
    assert entry.payload["memory_write_status"] == "BLOCKED_GLOBAL_MEDIATION_REQUIRED"
    assert service.ledger.verify_integrity() is True


def test_mediation_status_separates_state_plane_from_evidence_plane(tmp_path):
    service = _service(tmp_path)
    status = service.get_mediation_status()

    assert status["protocol"] == GLOBAL_MEDIATION_PROTOCOL
    assert status["governed_state_plane"] == "MNB_MEMORY"
    assert status["allowed_persistent_effect"] == "MNB_APPEND_VIA_MUTATION_EXECUTE"
    assert status["legacy_memory_add"] == "BLOCKED"
    assert status["process_query_memory_write"] == "BLOCKED"
    assert status["direct_geometric_memory_add"] == "BLOCKED"
    assert status["evidence_plane_ledger"] == "EXEMPT_APPEND_ONLY_EVIDENCE"


def test_public_api_and_ui_have_no_direct_memory_write_surface():
    api_source = Path("src/matverse_stack/api.py").read_text(encoding="utf-8")
    main_source = Path("src/matverse_stack/main.py").read_text(encoding="utf-8")

    assert '@router.post("/memory/add")' not in api_source
    assert "MemoryAddRequest" not in api_source
    assert "Add MNB to Memory" not in main_source
    assert "add_mnb_ui" not in main_source
    assert "add_to_memory_checkbox" not in main_source


def test_governed_persistence_capability_is_confined_to_effect_executor():
    source_root = Path("src/matverse_stack")
    governed_append_users = set()
    private_capability_users = set()
    private_append_method_users = set()
    direct_memory_add_users = set()
    safe_write_importers = set()
    memory_path_users = set()

    for path in source_root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "_governed_memory_append" in text:
            governed_append_users.add(path.name)
        if "_MEMORY_WRITE_CAPABILITY" in text:
            private_capability_users.add(path.name)
        if "_append_with_capability" in text:
            private_append_method_users.add(path.name)
        if "memory.add(" in text or "self.memory.add(" in text:
            direct_memory_add_users.add(path.name)
        if "from .utils import safe_write_json" in text:
            safe_write_importers.add(path.name)
        if "MEMORY_PATH" in text:
            memory_path_users.add(path.name)

    assert governed_append_users == {"memory.py", "effect_binding.py"}
    assert private_capability_users == {"memory.py"}
    assert private_append_method_users == {"memory.py"}
    assert direct_memory_add_users == set()
    assert safe_write_importers == {"memory.py"}
    assert memory_path_users == {"config.py", "memory.py"}
