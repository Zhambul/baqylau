# Copyright (c) 2026 Zhambyl Yermagambet
"""Store pre-split journal JSON without using the new write path for old steps."""

from extensions.models import interpretation_steps as steps, interpretations
from tests.extension_host import (
    interpretation_core as core,
    interpretation_fixture as fixtures,
    lifecycle_journal_changes as changes,
)


def stored_legacy(
    case: fixtures.InterpretationCase, *, core_input: bool,
) -> interpretations.InterpretationCommit:
    """Replace only the private fixture journal with the old inline layout.

    Returns:
        The exact old proposal, with its already accepted facts and original time.

    """
    request = core.core_proposal(case) if core_input else fixtures.proposal(case)
    case.store.record_interpretation(request)
    journal = request.proposal.steps
    if core_input:
        activity = changes.activity(request)
        journal = (steps.CoreTranslationStep(
            source=activity.source, content_snapshot=activity.content_snapshot,
            translator_version=request.proposal.translator_version,
            decision=request.proposal.decision, facts=changes.required(request).facts,
        ),)
    request = request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "format_version": 1, "steps": journal,
    })})
    with case.store.database.write() as connection:
        connection.execute(
            "UPDATE interpretation_journals SET proposal=?, codec_version=1 "
            "WHERE history_revision=? AND raw_event_id=?",
            (request.proposal.model_dump_json(exclude={"format_version"}), request.proposal.binding.history_revision,
             request.proposal.binding.raw_event_id),
        )
    return request
