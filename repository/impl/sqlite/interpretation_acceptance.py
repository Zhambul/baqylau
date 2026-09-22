# Copyright (c) 2026 Zhambyl Yermagambet
"""Select complete stored input and compare all state used by a pure interpretation."""

import sqlite3
from types import MappingProxyType

from baqylau_extension_api.models.canonical import CommittedFact

from domain.ids import CanonicalEventId
from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_context import InterpretationContext, ProcessingPackage
from extensions.models.interpretation_reads import TranslationStateKey
from extensions.models.interpretation_snapshot import PriorStateRequest
from extensions.models.interpretations import InterpretationBinding, InterpretationCommit
from extensions.models.lifecycle_selection import RuntimeSelection
from repository.impl.sqlite import (
    interpretation_reads as reads,
    interpretation_selection as selection,
    interpretation_snapshot,
    interpretation_state,
    processing_runtime,
)


def select_context(connection: sqlite3.Connection, request: InterpretationCommit) -> InterpretationContext:
    """Retain exact package declarations with the original observation.

    Returns:
        A data-only context selected under the repository write lock.

    """
    binding = request.proposal.binding
    runtime = selection.selected_runtime(connection, binding)
    selection.require_history(connection, binding)
    original = selection.original_input(connection, binding)
    return InterpretationContext(binding, original, _packages(connection, runtime))


def _packages(connection: sqlite3.Connection, runtime: RuntimeSelection) -> MappingProxyType[str, ProcessingPackage]:
    packages: dict[str, ProcessingPackage] = {}
    for selected in runtime.packages:
        package = processing_runtime.retained_package(connection, selected)
        packages[package.manifest.extension_id] = package
    return MappingProxyType(packages)


def require_boundary(connection: sqlite3.Connection, context: InterpretationContext) -> None:
    """Reject stale input, a changed accepted boundary, or an existing legacy verdict.

    Raises:
        ValueError: If this proposal cannot start a new interpretation.

    """
    binding = context.binding
    if selection.canonical_head(connection, binding.history_revision) != binding.expected_canonical_cursor:
        message = "interpretation canonical snapshot is stale"
        raise ValueError(message)
    existing = connection.execute(
        "SELECT 1 FROM interpretations WHERE history_revision=? AND raw_event_id=?",
        (binding.history_revision, binding.raw_event_id),
    ).fetchone()
    if existing is not None:
        message = "raw input already has a legacy interpretation in this history"
        raise ValueError(message)
    pending = connection.execute(
        "SELECT 1 FROM pending_raw_events WHERE raw_event_id=?", (binding.raw_event_id,),
    ).fetchone()
    if binding.mode == "live" and pending is None:
        message = "live interpretation input is not pending"
        raise ValueError(message)


def require_prior_state(connection: sqlite3.Connection, request: InterpretationCommit) -> None:
    """Check every supplied prior fact against the selected history, not a worker claim."""
    selected = (step for step in request.proposal.steps if isinstance(step, steps.CanonicalTransformStep))
    for step in selected:
        for prior in step.request.prior_state.facts:
            _require_prior_fact(connection, request.proposal.binding.history_revision, prior)
        binding = request.proposal.binding
        interpretation_snapshot.require_complete(connection, PriorStateRequest(
            history_revision=binding.history_revision, scope=binding.scope,
            expected_canonical_cursor=binding.expected_canonical_cursor,
        ), step.request.prior_state)


def _require_prior_fact(connection: sqlite3.Connection, history_revision: str, prior: CommittedFact) -> None:
    actual = reads.read_fact(connection, history_revision, CanonicalEventId(prior.fact.event_id))
    expected = prior.fact, prior.cursor, prior.accepted_at
    if actual is None or (actual.fact, actual.cursor, actual.accepted_at) != expected:
        message = "canonical step supplied a different prior fact snapshot"
        raise ValueError(message)


def write_decoder_state(connection: sqlite3.Connection, request: InterpretationCommit) -> None:
    """Commit the decoder's next state with its complete journal and final facts."""
    for step in request.proposal.steps:
        if isinstance(step, steps.ExtensionTranslationStep):
            _write_decoder_step(connection, request.proposal.binding, step)


def _write_decoder_step(
    connection: sqlite3.Connection, binding: InterpretationBinding, step: steps.ExtensionTranslationStep,
) -> None:
    source = step.request.inputs[0].source
    key = TranslationStateKey(
        extension_id=step.request.context.extension_id, history_revision=binding.history_revision,
        scope=binding.scope, source_identity=source.source.source_identity,
    )
    if isinstance(step.outcome, steps.AppliedStep):
        interpretation_state.write_state(connection, key, step.request.state, step.outcome.reply.next_state)
    elif interpretation_state.read_state(connection, key) != step.request.state:
        message = "failed interpretation decoder state is stale"
        raise ValueError(message)
