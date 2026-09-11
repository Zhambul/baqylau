# Copyright (c) 2026 Zhambyl Yermagambet
"""Inference missing-executable tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from inference import (
    audit as inference_audit,
    contract as inference_contract,
    default as inference_default,
    errors as inference_errors,
    options as inference_options,
    provider_state,
)
from tests.harness_names import CODEX_HARNESS
from tests.inference_support import Audit, InferenceTerminal, Usage

if TYPE_CHECKING:
    from audit.recorder import AuditRecorder


def test_missing_executables_report_config_names() -> None:
    """Verify missing executables report configured names."""
    audit = Audit()
    terminal = InferenceTerminal(())
    model_factory = inference_default.DefaultModelFactory(
        terminal.plugin(),
        Usage(),
        cast("AuditRecorder", audit),
        inference_options.DefaultModelOptions(executable_resolver=lambda _executable_name: None),
    )
    with pytest.raises(inference_errors.ModelUnavailableError):
        model_factory.small().send(inference_contract.ModelPromptRequest("name this", "session-one"))
    assert not terminal.opened_tabs
    context = cast("inference_audit.ModelUnavailableAudit", audit.errors[0][2])
    assert context.providers[0] == provider_state.ExecutableUnavailable(
        provider=CODEX_HARNESS,
        status="executable unavailable",
        configuration=model_factory.runtime_configs.for_harness(CODEX_HARNESS).executable,
    )
