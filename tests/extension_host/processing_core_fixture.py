# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise the actual core interpretation adapter with controlled harness calls."""

from dataclasses import dataclass
from unittest.mock import Mock

from domain import records
from engine.interpret import dependencies, snapshots, translation
from harness.contract import CanonicalEventReaction, HarnessPlugin, HarnessTranslator
from harness.models.raw_events import TranslationResult
from tests import foundation_test_primitives, sqlite_test_fixtures as native


@dataclass(frozen=True)
class CoreCase:
    """Keep core consumer calls observable without a live harness process."""

    phase: translation.TranslationPhase
    plugin: Mock
    reaction: Mock
    cache: Mock
    failures: Mock
    canonical: Mock


def phase() -> CoreCase:
    """Use the actual translation phase and core consistency checks.

    Returns:
        A strict core adapter with a valid fixture session-start result.

    """
    plugin = Mock(spec=HarnessPlugin)
    plugin.harness_info = Mock(plugin_version="core-v1")
    plugin.translator = Mock(spec=HarnessTranslator, wraps=foundation_test_primitives.FixedTranslator(TranslationResult(
        (native.a_started_event(),), records.RecordedTranslationDecision.TRANSLATED, None,
    )))
    plugin.sources = Mock()
    reaction = Mock(spec=CanonicalEventReaction)
    cache = Mock(spec=snapshots.TerminalSnapshotCache)
    failures = Mock()
    selected = Mock(spec=dependencies.InterpreterDependencies)
    selected.services = Mock(core_translators={}, inputs=(reaction,))
    selected.services.harnesses.plugin.return_value = plugin
    selected.repositories = Mock()
    return CoreCase(
        translation.TranslationPhase(selected, cache, failures), plugin, reaction, cache, failures,
        selected.repositories.canonical_events,
    )
