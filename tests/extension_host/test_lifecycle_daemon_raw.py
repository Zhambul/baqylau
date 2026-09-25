# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept raw input changes through actual daemon and worker processes."""

from pathlib import Path

from baqylau_extension_api.models import transforms

from extensions.models import interpretation_steps as steps, interpretations
from tests.extension_host import (
    lifecycle_daemon_checks as checks,
    lifecycle_daemon_fixture as fixture,
    lifecycle_daemon_payloads as payloads,
    process_fixture,
    public_audit_checks as audit,
)

RAW_DROP_BEHAVIOR = "rawdrop"
RAW_REPLACE_BEHAVIOR = "rawreplace"
RAW_INSERT_BEHAVIOR = "rawinsert"
REQUIRED_KINDS = ("session.started", "actor.started")
RAW_STAGE = "raw"
RAW_STAGES = ("core_lifecycle", RAW_STAGE)
TRANSLATED_STAGES = (*RAW_STAGES, "core_activity", "canonical")
INSERTED_STAGES = (*RAW_STAGES, "core_activity", "core_activity", "canonical")
ENABLE_ACTION: fixture.LifecycleAction = "enable"
STOP_HOOK = "Stop"
STOP_IDENTITY = "stop-one"


def raw_step(commit: interpretations.InterpretationCommit) -> steps.RawTransformStep:
    """Read the single recorded raw step of one journal.

    Returns:
        The stored raw transform step.

    """
    recorded = [step for step in commit.proposal.steps if step.stage == RAW_STAGE]
    assert len(recorded) == 1
    step = recorded[0]
    assert isinstance(step, steps.RawTransformStep)
    return step


def raw_operation_kinds(step: steps.RawTransformStep) -> tuple[str, ...]:
    """Read the raw reply's ordered operation kinds.

    Returns:
        The operation kinds.

    """
    outcome = step.outcome
    assert isinstance(outcome, steps.AppliedStep)
    return tuple(operation.kind for operation in outcome.reply.operations)


def original_content_ids(step: steps.RawTransformStep) -> tuple[str, ...]:
    """Read the content identities the raw request carried.

    Returns:
        The original content identities.

    """
    return tuple(source.content.content_id for source in step.request.inputs)


def replaced_content_ids(step: steps.RawTransformStep) -> tuple[str, ...]:
    """Read the content identities a raw replacement introduced.

    Returns:
        The replaced content identities.

    """
    outcome = step.outcome
    assert isinstance(outcome, steps.AppliedStep)
    replacements = (
        operation for operation in outcome.reply.operations if isinstance(operation, transforms.Replace)
    )
    return tuple(operation.document.content.content_id for operation in replacements)


def test_raw_dropped_input_keeps_session(tmp_path: Path, runtime_wheels: Path) -> None:
    """A dropped raw input keeps the required session and the exact original bytes."""
    case = fixture.installed(tmp_path, runtime_wheels, RAW_DROP_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        hook = fixture.hook(STOP_HOOK, STOP_IDENTITY)
        fixture.deliver_hook(client, hook)
        fixture.require_core_facts(case, REQUIRED_KINDS)
        checks.require_drained(case)

        commit = checks.only_journal(case)
        assert checks.step_stages(commit) == RAW_STAGES
        assert checks.fact_kinds(commit) == REQUIRED_KINDS
        assert audit.public_step(client, case, RAW_STAGE).operation_kinds == ("drop",)
        payloads.require_payload(case, hook)


def test_raw_replaced_input_changes_activity(tmp_path: Path, runtime_wheels: Path) -> None:
    """A replaced raw input changes the translation content and keeps its original bytes."""
    case = fixture.installed(tmp_path, runtime_wheels, RAW_REPLACE_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        hook = fixture.hook(STOP_HOOK, STOP_IDENTITY)
        fixture.deliver_hook(client, hook)
        fixture.require_core_facts(case, (*REQUIRED_KINDS, "turn.finished"))
        checks.require_drained(case)

        commit = checks.only_journal(case)
        step = raw_step(commit)
        assert checks.step_stages(commit) == TRANSLATED_STAGES
        assert raw_operation_kinds(step) == ("replace",)
        assert audit.public_step(client, case, RAW_STAGE).operation_kinds == ("replace",)
        assert set(replaced_content_ids(step)).isdisjoint(original_content_ids(step))
        payloads.require_payload(case, hook)


def test_raw_inserted_input_adds_activity(tmp_path: Path, runtime_wheels: Path) -> None:
    """An inserted raw input is translated after its anchor without changing the original."""
    case = fixture.installed(tmp_path, runtime_wheels, RAW_INSERT_BEHAVIOR)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, ENABLE_ACTION)
        hook = fixture.hook(STOP_HOOK, STOP_IDENTITY)
        fixture.deliver_hook(client, hook)
        fixture.require_core_facts(case, (*REQUIRED_KINDS, "turn.finished"))
        checks.require_drained(case)

        commit = checks.only_journal(case)
        assert checks.step_stages(commit) == INSERTED_STAGES
        assert raw_operation_kinds(raw_step(commit)) == ("keep", "insert")
        assert audit.public_step(client, case, RAW_STAGE).operation_kinds == ("keep", "insert")
        payloads.require_payload(case, hook)
