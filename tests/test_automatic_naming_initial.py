# Copyright (c) 2026 Zhambyl Yermagambet
"""Durable jobs, title safety, and generic naming semantics."""

from dataclasses import replace

import pytest

from domain import (
    event_session,
    ids as domain_ids,
    work_state,
)
from engine.interpret.translators import AutomaticTitleTranslator
from harness import contract as harness_contract, registry as harness_registry
from harness.impl.claude_code.plugin import plugin as claude_plugin
from harness.impl.codex.plugin import plugin as codex_plugin
from naming.reaction import AutomaticNamingReaction
from repository.impl.sqlite.naming import SqliteNamingJobRepository
from tests import (
    automatic_naming_models_one,
    automatic_naming_namer_helper,
    automatic_naming_prompt_helper,
    automatic_naming_session_helper,
    automatic_naming_values,
)
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS


def test_first_prompt_enqueues_once_and_restart(naming_jobs: SqliteNamingJobRepository) -> None:
    """Verify first prompt enqueues once and a restart cannot claim it twice."""
    repository = naming_jobs
    registry = harness_registry.HarnessRegistry()
    registry.register(
        replace(
            codex_plugin,
            harness_info=replace(
                codex_plugin.harness_info,
                supports_native_initial_naming=False,
            ),
        ),
    )
    reaction = AutomaticNamingReaction(registry, repository)

    reaction.react(automatic_naming_prompt_helper.prompt_event())
    reaction.react(automatic_naming_prompt_helper.prompt_event())
    claimed = repository.claim_next()

    assert claimed is not None
    assert claimed.key == f"initial:{automatic_naming_values.SESSION_ID}"
    assert claimed.prompt == "A very long first semantic prompt"
    restarted = SqliteNamingJobRepository(repository.database)
    assert restarted.claim_next() is None


@pytest.mark.parametrize("harness", [CODEX_HARNESS, CLAUDE_CODE_HARNESS])
def test_native_initial_naming_never_enqueues(
    naming_jobs: SqliteNamingJobRepository,
    harness: domain_ids.HarnessName,
) -> None:
    """Verify native initial naming never enqueues a model job."""
    repository = naming_jobs
    registry = harness_registry.HarnessRegistry()
    registry.register(codex_plugin)
    registry.register(claude_plugin)

    AutomaticNamingReaction(registry, repository).react(
        automatic_naming_prompt_helper.prompt_event(claude=harness == CLAUDE_CODE_HARNESS),
    )

    assert repository.claim_next() is None


def test_installed_harnesses_validate_native() -> None:
    """Verify installed harnesses validate native and generic naming routes."""
    registry = harness_registry.HarnessRegistry()
    registry.register(codex_plugin)
    registry.register(claude_plugin)

    registry.validate()


@pytest.mark.parametrize(
    "changed_plugin",
    [
        replace(
            codex_plugin,
            harness_info=replace(
                codex_plugin.harness_info,
                supports_native_automatic_renaming=True,
            ),
        ),
        replace(
            claude_plugin,
            harness_info=replace(
                claude_plugin.harness_info,
                supports_native_automatic_renaming=False,
            ),
        ),
    ],
)
def test_registry_rejects_capability_that(
    changed_plugin: harness_contract.HarnessPlugin,
) -> None:
    """Verify registry rejects a capability that disagrees with its handler."""
    registry = harness_registry.HarnessRegistry()
    registry.register(changed_plugin)
    registry.register(
        claude_plugin if changed_plugin.harness_info.name == CODEX_HARNESS else codex_plugin,
    )

    with pytest.raises(harness_registry.HarnessRegistryError):
        registry.validate()


def test_initial_name_records_only_auto_title(naming_jobs: SqliteNamingJobRepository) -> None:
    """Verify initial name records only an automatic title observation."""
    jobs = naming_jobs
    raw_events = automatic_naming_models_one.RawEvents()
    service = automatic_naming_namer_helper.namer(
        automatic_naming_models_one.FixedModels(("Concise automatic session title",)), jobs, raw_events,
    )

    title = service.initial_name(automatic_naming_session_helper.session(), "Implement automatic concise naming")

    assert title == "Concise automatic session title"
    assert len(raw_events.events) == 1
    translated = AutomaticTitleTranslator().translate(raw_events.events[0])
    assert translated.canonical_events[0].payload == event_session.SessionTitleChanged(
        title,
        work_state.TitleOrigin.AUTOMATIC,
    )
