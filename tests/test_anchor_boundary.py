import pytest

from matverse_stack.anchor import AnchorService


def test_configured_sepolia_key_does_not_claim_or_submit_chain_anchor(monkeypatch):
    monkeypatch.setenv("SEPOLIA_PRIVATE_KEY", "test-only-placeholder")
    monkeypatch.setenv("SEPOLIA_RPC_URL", "https://example.invalid")

    service = AnchorService()
    receipt = service.anchor_hash("a" * 64)
    status = service.get_status()

    assert receipt.startswith("local-receipt-")
    assert status["mode"] == "LOCAL_RECEIPT_ONLY"
    assert status["enabled"] is False
    assert status["can_anchor"] is False
    assert status["can_create_local_receipt"] is True
    assert status["external_anchor_configured"] is True
    assert status["external_anchor_status"] == "NOT_IMPLEMENTED"


def test_anchor_service_rejects_non_sha256_input():
    service = AnchorService()

    with pytest.raises(ValueError, match="32-byte hexadecimal"):
        service.anchor_hash("not-a-digest")
