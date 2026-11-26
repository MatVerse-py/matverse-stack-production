import pytest
from fastapi import HTTPException

from matverse_stack import utils


def test_missing_api_key_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(utils, "APP_API_KEY", "")

    with pytest.raises(HTTPException) as error:
        utils.require_api_key("anything")

    assert error.value.status_code == 503
    assert error.value.detail == "server_api_key_not_configured"


def test_wrong_api_key_is_rejected(monkeypatch):
    monkeypatch.setattr(utils, "APP_API_KEY", "configured-secret")

    with pytest.raises(HTTPException) as error:
        utils.require_api_key("wrong-secret")

    assert error.value.status_code == 401


def test_correct_api_key_is_accepted(monkeypatch):
    monkeypatch.setattr(utils, "APP_API_KEY", "configured-secret")

    assert utils.require_api_key("configured-secret") is None
