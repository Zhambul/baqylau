# Copyright (c) 2026 Zhambyl Yermagambet
"""Classify user-shaped Claude transcript text."""

import re

from pydantic import ValidationError

from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.canonical.transcript_model_core import TranscriptKind

TEAM_MESSAGE = re.compile(r"^\s*<teammate-message\b([^>]*)>\s*(.*?)\s*</teammate-message>\s*$", re.DOTALL)
TEAM_MESSAGE_BLOCK = re.compile(r"<teammate-message\b([^>]*)>\s*(.*?)\s*</teammate-message>", re.DOTALL)
TEAMMATE_ID = re.compile(r'teammate_id="([^"]*)"')
RECAP_HINT = re.compile(r"\s*\(disable recaps in /config\)\s*$")
TEAM_WRAPPER = re.compile(r"^\s*Another Claude session sent a message:\s*<teammate-message\b")
RESUMES_TURN = (re.compile(r"^\s*Stop hook feedback:"),)
LEAD_TEAMMATE_ID = "team-lead"
INTERRUPT_MARKERS = (
    "[Request interrupted by user]",
    "[Request interrupted by user for tool use]",
)


def classify_user_text(text: str) -> tuple[str, str, str | None]:
    """Classify a prompt or teammate message.

    Returns:
        The record kind, main text, and optional message body.

    """
    message_match = TEAM_MESSAGE.match(text)
    if message_match:
        sender_match = TEAMMATE_ID.search(message_match.group(1))
        return (
            TranscriptKind.TEAM_MESSAGE.value,
            sender_match.group(1) if sender_match else "",
            message_match.group(2),
        )
    return TranscriptKind.PROMPT.value, text, None


def teammate_idle_notifications(text: str) -> tuple[records.TeammateIdleNotificationDocument, ...]:
    """Read structured idle messages from a team-mail wrapper.

    Returns:
        The idle notifications.

    """
    if not TEAM_WRAPPER.match(text):
        return ()
    found = []
    for message_match in TEAM_MESSAGE_BLOCK.finditer(text):
        body = message_match.group(2).strip()
        try:
            header = records.TeammateMessageBodyHeader.model_validate_json(body)
        except ValidationError:
            continue
        if header.type == "idle_notification":
            found.append(records.TeammateIdleNotificationDocument.model_validate_json(body))
    return tuple(found)


def strip_recap_hint(text: str) -> str:
    """Remove the configuration hint from a recap.

    Returns:
        The recap text.

    """
    return RECAP_HINT.sub("", text).strip()


def injected(record: records.UserRecord, text: str = "") -> bool:
    """Return whether Claude created the user-shaped record.

    Returns:
        True when Claude injected the record.

    """
    return bool(
        record.is_meta
        or interrupted(record, text)
        or record.is_compact_summary
        or (text and TEAM_WRAPPER.match(text)),
    )


def interrupted(record: records.UserRecord, text: str) -> bool:
    """Recognize native interrupt markers with or without a message ID.

    Returns:
        True for an interrupt ID or an exact interrupt marker.

    """
    return bool(record.interrupted_message_id) or text.strip() in INTERRUPT_MARKERS


def resumes_turn(text: str) -> bool:
    """Return whether an injected prompt resumes a stopped turn.

    Returns:
        True when the prompt resumes the turn.

    """
    return any(pattern.match(text) for pattern in RESUMES_TURN)
