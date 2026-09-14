# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the native Go model list through the dashboard API."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from harness.impl.opencode2 import native_probe
from tests import http_test_assets, http_test_controls
from tests.provider_graph import ProviderGraph

MODELS = "models"
FIXTURE = Path(__file__).parents[1] / "e2e" / "fixtures" / "opencode_go_models.json"

CATALOG_PATH = "/api/harnesses/opencode2/catalog"


# Harness limit: opencode2 only. Go model variants come from its native catalog.
def test_native_models_and_efforts_reach_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every native model, including models without effort values."""
    payload = FIXTURE.read_bytes()
    request = Mock(return_value=payload)
    monkeypatch.setattr(native_probe, "request", request)
    with http_test_assets.running_server(ProviderGraph()) as server:
        document = json.loads(http_test_controls.get(server, CATALOG_PATH).body.raw)
        repeated = json.loads(http_test_controls.get(server, CATALOG_PATH).body.raw)
    assert {
        model["model_id"]: [effort["value"] for effort in model["efforts"]]
        for model in document[MODELS]
    } == {
        f'opencode-go/{model["id"]}': [variant["id"] for variant in model["variants"]]
        for model in json.loads(payload)["output"][MODELS]
    }
    assert sum(model["default"] for model in document[MODELS]) == 1
    assert all(_valid_default_effort(model["efforts"]) for model in document[MODELS])
    assert repeated == document
    request.assert_called_once()


# Harness limit: opencode2 only. An empty native catalog must not restore a fixed model list.
def test_empty_native_catalog_has_no_fixed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an empty native model list empty in the API."""
    monkeypatch.setattr(native_probe, "request", Mock(return_value=b'{"output":{"models":[]}}'))
    with http_test_assets.running_server(ProviderGraph()) as server:
        document = json.loads(http_test_controls.get(server, CATALOG_PATH).body.raw)
    assert not document[MODELS]


def _valid_default_effort(efforts: list[dict[str, object]]) -> bool:
    defaults = [effort for effort in efforts if effort["default"]]
    return len(defaults) == bool(efforts)
