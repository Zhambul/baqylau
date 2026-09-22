# Copyright (c) 2026 Zhambyl Yermagambet
"""Test sessiondata api store activity."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain import (
    content as domain_content,
    entry_conversation,
    ids as domain_ids,
    messaging,
)
from repository.contract import session_data as session_data_contract
from tests import (
    canonical_sessiondata_api_entries as api_entries,
    canonical_sessiondata_api_store as store_support,
    canonical_sessiondata_api_values as api_values,
)

if TYPE_CHECKING:

    from repository.impl.sqlite.session_data import SqliteSessionDataRepository


@pytest.mark.usefixtures("tmp_path")
def test_last_activity_is_newest_entry_not_stored(
    session_data_store: SqliteSessionDataRepository,
) -> None:
    """Verify the last activity is the newest entry not a stored clock.

    Two consumers need it — the resume picker and the list — and storing it on
        the session row would rewrite that row on every single fact.
    """
    read_model = session_data_store
    read_model.apply(api_values.SESSION, session_data_contract.SessionDataChanges(session=api_values.FACTS), 1)
    assert store_support.stored_data(read_model).last_activity_at is None

    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            entries=(api_entries.entry(
                entry_conversation.MessageBody(
                    domain_ids.MessageId(api_values.MESSAGE_ID_TEXT),
                    messaging.MessageRole.USER,
                    messaging.MessagePhase.PROMPT,
                    domain_content.TextContent(api_values.PROMPT_TEXT),
                ),
                occurred_at=api_values.LATEST_ACTIVITY_TIME,
            ),),
        ),
        2,
    )
    assert store_support.stored_data(read_model).last_activity_at == pytest.approx(api_values.LATEST_ACTIVITY_TIME)


@pytest.mark.usefixtures("tmp_path")
def test_unknown_session_has_no_aggregate_at_all(
    session_data_store: SqliteSessionDataRepository,
) -> None:
    """Verify an unknown session has no aggregate at all."""
    assert session_data_store.read(domain_ids.SessionId("never-seen")) is None
