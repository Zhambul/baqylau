# Copyright (c) 2026 Zhambyl Yermagambet
"""Build sessions for control effect tests."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from domain import ids as domain_ids
from harness.models.session import Session
from tests import control_effect_values as control_values
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS

if TYPE_CHECKING:
    from harness.contracts import plugin as plugin_contracts


def codex_session(
    actor_id: domain_ids.ActorId = control_values.TEST_ACTOR_ID,
    source_name: str = control_values.ROLLOUT_SOURCE_NAME,
) -> Session:
    """Build the standard Codex session fixture.

    Returns:
        The standard Codex session fixture.

    """
    return Session(
        control_values.TEST_SESSION_ID,
        actor_id,
        source_name,
        control_values.TEST_WORKING_DIRECTORY,
        plugin=cast(
            "plugin_contracts.HarnessPlugin",
            SimpleNamespace(harness_info=SimpleNamespace(name=CODEX_HARNESS)),
        ),
    )


def claude_session(source_name: str) -> Session:
    """Build the standard Claude Code session fixture.

    Returns:
        The standard Claude Code session fixture.

    """
    return Session(
        control_values.TEST_SESSION_ID,
        control_values.TEST_ACTOR_ID,
        source_name,
        control_values.TEST_WORKING_DIRECTORY,
        plugin=cast(
            "plugin_contracts.HarnessPlugin",
            SimpleNamespace(harness_info=SimpleNamespace(name=CLAUDE_CODE_HARNESS)),
        ),
    )
