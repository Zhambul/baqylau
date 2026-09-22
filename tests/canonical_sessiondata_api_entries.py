# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata api entries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import (
    content as domain_content,
    entries as domain_entries,
    entry_attention,
    entry_base,
    entry_conversation,
    entry_lifecycle,
    entry_resources,
    entry_shells,
)

# Keep entry models separate from shared identity and outcome vocabulary.
# isort: split

from domain import (
    ids as domain_ids,
    messaging,
    outcomes,
)
from domain.entry_extensions import ExtensionEntryBody, ExtensionSchemaIdentity
from tests import canonical_sessiondata_api_values as api_values

DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH

if TYPE_CHECKING:
    from collections.abc import Callable


def entry(
    body: entry_base.EntryBody,
    *,
    entry_id: domain_ids.CanonicalEventId = api_values.AN_ENTRY_ID,
    occurred_at: float = api_values.ENTRY_TIME,
) -> domain_entries.SessionEntry:
    """Build a session entry with the supplied body, identity, and time.

    Returns:
        The entry for the fixed session, lead actor, and turn.

    """
    return domain_entries.SessionEntry(
        entry_id=entry_id,
        session_id=api_values.SESSION,
        actor_id=api_values.LEAD,
        parent_actor_id=None,
        turn_id=domain_ids.TurnId("turn-7"),
        occurred_at=occurred_at,
        summary=None,
        body=body,
        cursor=api_values.ENTRY_CURSOR,
    )


def sample_body(body_type: type[entry_base.EntryBody]) -> entry_base.EntryBody:
    """Build a minimal instance of a supported entry body.

    Returns:
        The sample body for the supplied type.

    """
    samples: dict[str, Callable[[], entry_base.EntryBody]] = {
        "TurnStartedBody": entry_conversation.TurnStartedBody,
        "TurnFinishedBody": lambda: entry_conversation.TurnFinishedBody(entry_base.TurnState.FINISHED),
        "MessageBody": lambda: entry_conversation.MessageBody(
            domain_ids.MessageId("m"),
            messaging.MessageRole.USER,
            messaging.MessagePhase.PROMPT,
            domain_content.TextContent("x"),
        ),
        "ReasoningBody": lambda: entry_conversation.ReasoningBody(
            domain_ids.ReasoningId("r"), domain_content.TextContent("x"),
        ),
        "ShellStartedBody": lambda: entry_shells.ShellStartedBody(
            domain_ids.ShellId(api_values.SHELL_ID_TEXT),
            domain_content.TextContent("ls"),
            outcomes.ExecutionMode.FOREGROUND,
        ),
        "ShellOutputBody": lambda: entry_shells.ShellOutputBody(
            domain_ids.ShellId(api_values.SHELL_ID_TEXT),
            outcomes.ProgressStream.OUTPUT,
            outcomes.OutputMode.APPEND,
            domain_content.TextContent("x"),
        ),
        "ShellBackgroundedBody": lambda: entry_shells.ShellBackgroundedBody(
            domain_ids.ShellId(api_values.SHELL_ID_TEXT),
        ),
        "ShellFinishedBody": lambda: entry_shells.ShellFinishedBody(
            domain_ids.ShellId(api_values.SHELL_ID_TEXT), entry_base.RunState.SUCCEEDED,
        ),
        "FileBody": lambda: entry_resources.FileBody("/p", outcomes.FileAction.READ, entry_base.FileState.SUCCEEDED),
        "SearchBody": lambda: entry_resources.SearchBody(
            "Grep", domain_content.TextContent("q"), entry_base.FileState.SUCCEEDED,
        ),
        "WebBody": lambda: entry_resources.WebBody("https://x", entry_base.FileState.SUCCEEDED),
        "BrowserBody": lambda: entry_resources.BrowserBody("Refresh the fixture", entry_base.FileState.SUCCEEDED),
        "WorktreeBody": lambda: entry_resources.WorktreeBody(
            outcomes.WorktreeAction.ENTERED, entry_base.FileState.SUCCEEDED,
        ),
        "SkillStartedBody": lambda: entry_attention.SkillStartedBody(domain_ids.SkillId("k"), "audit-debug"),
        "SkillFinishedBody": lambda: entry_attention.SkillFinishedBody(
            domain_ids.SkillId("k"), entry_base.RunState.SUCCEEDED,
        ),
        "QuestionAskedBody": lambda: entry_attention.QuestionAskedBody(
            domain_ids.AttentionId(api_values.ATTENTION_ID_TEXT), (),
        ),
        "QuestionAnsweredBody": lambda: entry_attention.QuestionAnsweredBody(
            domain_ids.AttentionId(api_values.ATTENTION_ID_TEXT),
        ),
        "PlanProposedBody": lambda: entry_attention.PlanProposedBody(
            domain_ids.AttentionId(api_values.ATTENTION_ID_TEXT), domain_content.TextContent("plan"),
        ),
        "PlanResolvedBody": lambda: entry_attention.PlanResolvedBody(
            domain_ids.AttentionId(api_values.ATTENTION_ID_TEXT), outcomes.PlanState.APPROVED,
        ),
        "CompactionStartedBody": entry_lifecycle.CompactionStartedBody,
        "CompactionFinishedBody": entry_lifecycle.CompactionFinishedBody,
        "AssignmentStartedBody": lambda: entry_lifecycle.AssignmentStartedBody(domain_ids.AssignmentId("as")),
        "AssignmentFinishedBody": lambda: entry_lifecycle.AssignmentFinishedBody(domain_ids.AssignmentId("as")),
        "ModelChangeBody": lambda: entry_lifecycle.ModelChangeBody(api_values.MODEL_DISPLAY_NAME),
        "EffortChangeBody": lambda: entry_lifecycle.EffortChangeBody("high"),
        "ExtensionEntryBody": lambda: ExtensionEntryBody(
            owner="test.sample",
            entry_type="test.sample.card",
            source_event_id=domain_ids.CanonicalEventId("fact/native/1"),
            schema_ref=ExtensionSchemaIdentity(
                owner="test.sample", name="text", version=1, digest=DIGEST,
            ),
            document='"card"',
        ),
    }
    return samples[body_type.__name__]()
