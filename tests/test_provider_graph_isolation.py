# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the test provider graph away from the user's data directory."""

from __future__ import annotations

import pytest

from tests.provider_graph import ProviderGraph


def test_graph_without_isolated_data_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the graph refuses the user's default data directory."""
    monkeypatch.delenv("BAQYLAU_DATA_DIR", raising=False)
    monkeypatch.delenv("BAQYLAU_DATA_DIRECTORY", raising=False)
    with pytest.raises(RuntimeError, match="isolated data directory"):
        ProviderGraph()
