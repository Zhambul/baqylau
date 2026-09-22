# Copyright (c) 2026 Zhambyl Yermagambet
"""Build bounded audit summaries for one pipeline case."""

from dataclasses import dataclass
from itertools import starmap

from domain.records import InterpretationAudit, InterpretationAuditStep
from extensions.models import interpretation_steps as steps, interpretations
from harness.models.raw_events import RawEventAudit
from repository.impl.sqlite.raw_event_audits import SqliteRawEventAuditRepository
from tests.extension_host import processing_pipeline_fixture as pipeline

DEFAULT_HISTORY = "default"
NORMALIZED_FORMAT_VERSION = 2
TRUNCATED_STEP_COUNT = 1
LIMIT_REASON = "journal_limit"


@dataclass(frozen=True)
class StepSummary:
    """Compare one proposal step with its audit metadata."""

    index: int
    stage: str
    owner: str | None
    outcome: str | None
    reason: str | None


def audit(case: pipeline.PipelineCase) -> RawEventAudit | None:
    """Read the raw-event audit of one pipeline case.

    Returns:
        The stored audit, or no matching raw event.

    """
    repository = SqliteRawEventAuditRepository(case.original.store.database)
    return repository.audit(case.stored.observation.raw_event_id)


def step_summaries(
    commit: interpretations.InterpretationCommit, case: pipeline.PipelineCase,
) -> tuple[tuple[StepSummary, ...], tuple[StepSummary, ...]]:
    """Compare every proposal step with its bounded audit metadata.

    Returns:
        The expected and observed summaries in order.

    """
    interpretation = _require_audit(case)
    proposal_steps = commit.proposal.steps
    expected = tuple(starmap(_proposal_summary, enumerate(proposal_steps)))
    observed = tuple(map(audit_step_summary, interpretation.steps))
    return expected, observed


def limit_audit_steps(case: pipeline.PipelineCase) -> tuple[InterpretationAuditStep, ...]:
    """Read the limit records of one stored audit.

    Returns:
        The limit steps in order.

    """
    interpretation = _require_audit(case)
    return tuple(step for step in interpretation.steps if step.stage == "limit")


def audit_step_summary(step: InterpretationAuditStep) -> StepSummary:
    """Map one audit step to its comparison summary.

    Returns:
        The observed summary.

    """
    return StepSummary(step.step_index, step.stage, step.owner, step.outcome, step.reason)


def _require_audit(case: pipeline.PipelineCase) -> InterpretationAudit:
    """Require the stored audit of one pipeline case.

    Returns:
        The stored interpretation audit.

    Raises:
        ValueError: If the pipeline case has no stored audit.

    """
    stored = audit(case)
    if stored is None or stored.interpretation is None:
        message = "pipeline case has no stored audit"
        raise ValueError(message)
    return stored.interpretation


def _proposal_summary(index: int, step: steps.InterpretationStep) -> StepSummary:
    if isinstance(step, steps.LimitStep):
        return StepSummary(index, step.stage, step.extension_id, None, step.reason)
    if isinstance(step, steps.RawTransformStep | steps.CanonicalTransformStep | steps.ExtensionTranslationStep):
        return StepSummary(index, step.stage, step.request.context.extension_id, step.outcome.kind, None)
    return StepSummary(index, step.stage, None, None, None)
