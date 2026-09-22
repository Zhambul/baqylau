# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep raw transform results and their applied or failed outcomes."""

from baqylau_extension_api.models import raw_transforms

from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_bodies import (
    CONTENT_BUNDLE_KIND,
    BodyResolver,
    BodyStore,
)
from extensions.models.interpretation_storage_operations import (
    _load_raw_operation,
    _store_raw_operation,
)
from extensions.models.interpretation_storage_outcomes import (
    StoredAppliedStep,
    StoredFailedStep,
    StoredRawTransformResult,
    StoredStepOutcome,
    _required_reply,
)


def _store_raw_outcome(
    outcome: steps.StepOutcome[raw_transforms.RawTransformResult], store: BodyStore,
) -> StoredStepOutcome[StoredRawTransformResult]:
    if isinstance(outcome, steps.RejectedStep):
        return outcome
    reply = None if outcome.reply is None else _store_raw_result(outcome.reply, store)
    if isinstance(outcome, steps.AppliedStep):
        return StoredAppliedStep(reply=_required_reply(reply))
    return StoredFailedStep(diagnostic=outcome.diagnostic, reply=reply)


def _load_raw_outcome(
    outcome: StoredStepOutcome[StoredRawTransformResult], resolver: BodyResolver,
) -> steps.StepOutcome[raw_transforms.RawTransformResult]:
    if isinstance(outcome, steps.RejectedStep):
        return outcome
    if isinstance(outcome, StoredAppliedStep):
        return steps.AppliedStep(reply=_load_raw_result(outcome.reply, resolver))
    return steps.FailedStep(
        diagnostic=outcome.diagnostic,
        reply=None if outcome.reply is None else _load_raw_result(outcome.reply, resolver),
    )


def _store_raw_result(result: raw_transforms.RawTransformResult, store: BodyStore) -> StoredRawTransformResult:
    operations = tuple(_store_raw_operation(operation, store) for operation in result.operations)
    return StoredRawTransformResult(
        operations=operations, diagnostics=result.diagnostics,
        content_snapshot=store.intern(result.content_snapshot, CONTENT_BUNDLE_KIND),
    )


def _load_raw_result(result: StoredRawTransformResult, resolver: BodyResolver) -> raw_transforms.RawTransformResult:
    return raw_transforms.RawTransformResult(
        operations=tuple(_load_raw_operation(operation, resolver) for operation in result.operations),
        diagnostics=result.diagnostics,
        content_snapshot=resolver.resolve_content(result.content_snapshot),
    )
