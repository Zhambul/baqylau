# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply common updates in session data API stream tests."""

from domain import content as domain_content, entry_conversation, ids as domain_ids, messaging
from repository.contract import session_data as session_data_contract
from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from tests import canonical_sessiondata_api_entries as api_entries, canonical_sessiondata_api_values as api_values


def apply_snapshot(read_model: SqliteSessionDataRepository, cursor: int) -> None:
    """Apply the standard session and actor snapshot."""
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            session=api_values.FACTS,
            actors=api_values.ACTOR_ROWS,
        ),
        cursor,
    )


def apply_prompt(
    read_model: SqliteSessionDataRepository,
    cursor: int,
    *,
    message_id_text: str = api_values.MESSAGE_ID_TEXT,
    entry_id_text: str | None = None,
    text: str = api_values.PROMPT_TEXT,
) -> None:
    """Apply one prompt entry at the specified cursor."""
    body = entry_conversation.MessageBody(
        domain_ids.MessageId(message_id_text),
        messaging.MessageRole.USER,
        messaging.MessagePhase.PROMPT,
        domain_content.TextContent(text),
    )
    session_entry = (
        api_entries.entry(body)
        if entry_id_text is None
        else api_entries.entry(body, entry_id=domain_ids.CanonicalEventId(entry_id_text))
    )
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(session_entry,),
        ),
        cursor,
    )
