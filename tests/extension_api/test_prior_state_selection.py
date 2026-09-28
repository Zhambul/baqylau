# Copyright (c) 2026 Zhambyl Yermagambet
"""Only a canonical transformer can ask for prior state, and it asks explicitly (P08-T04)."""

import pytest
from baqylau_extension_api.manifest.data import ProcessingSelection, ScopeKinds
from pydantic import ValidationError

SCOPES: ScopeKinds = ("session",)
INPUTS = ("session.started",)


def test_canonical_transformer_asks() -> None:
    """The default is no prior state; a canonical transformer can ask for it."""
    selected = ProcessingSelection(capability="canonical_transformer", scopes=SCOPES, input_types=INPUTS)

    assert not selected.prior_state
    assert selected.model_copy(update={"prior_state": True}).prior_state


def test_other_capabilities_cannot_ask() -> None:
    """A raw transformer or a projector refuses the flag."""
    with pytest.raises(ValidationError, match="only a canonical transformer"):
        ProcessingSelection(capability="raw_transformer", scopes=SCOPES, input_types=INPUTS, prior_state=True)
