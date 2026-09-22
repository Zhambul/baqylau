# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata api change reads."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from api.sessiondata import streams
from core.change_signal import ChangeSignal
from domain import (
    actor_state,
    content,
    entry_conversation,
    ids as domain_ids,
    messaging,
)
from repository.contract import session_data as session_data_contract
from tests import (
    canonical_sessiondata_api_entries as api_entries,
    canonical_sessiondata_api_stream_models as stream_models,
    canonical_sessiondata_api_values as api_values,
)

if TYPE_CHECKING:
    from repository.impl.sqlite.session_data import SqliteSessionDataRepository


async def read_two_session_frames(
    read_model: SqliteSessionDataRepository,
) -> tuple[str, str]:
    """Read session frames before and after a recorded message change.

    Returns:
        The initial and changed session frames.

    """
    changes = ChangeSignal()
    read_model.sqlite_database.changes = changes
    reader = stream_models.FrameReader(
        streams.session_frames(
            streams.SessionStreamServices(read_model, stream_models.SilentAudit(), changes=changes),
            api_values.SESSION,
            0,
        ),
    )
    first = await reader.next()
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_conversation.MessageBody(
                    domain_ids.MessageId(api_values.MESSAGE_ID_TEXT),
                    messaging.MessageRole.USER,
                    messaging.MessagePhase.PROMPT,
                    content.TextContent(api_values.PROMPT_TEXT),
                ),
            ),),
            actors=(replace(api_values.ACTOR, status=actor_state.ActorStatus.THINKING),),
        ),
        2,
    )
    second = await reader.next()
    await reader.aclose()
    return first, second
