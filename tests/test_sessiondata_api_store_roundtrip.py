# Copyright (c) 2026 Zhambyl Yermagambet
"""Test sessiondata api store roundtrip."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from api.sessiondata import mapper
from api.sessiondata.models import entry as entry_responses
from domain import (
    content as domain_content,
    entry_shells,
    ids as domain_ids,
    outcomes,
)
from repository.contract import session_data as session_data_contract
from tests import canonical_sessiondata_api_entries as api_entries, canonical_sessiondata_api_values as api_values

if TYPE_CHECKING:

    from repository.impl.sqlite.session_data import SqliteSessionDataRepository


@pytest.mark.usefixtures("tmp_path")
def test_wire_shapes_survive_round_trip_through(
    session_data_store: SqliteSessionDataRepository,
) -> None:
    """End to end: what a writer produced, through SQLite, out as JSON."""
    read_model = session_data_store
    read_model.apply(
        api_values.SESSION,
        session_data_contract.SessionDataChanges(
            session=api_values.FACTS,
            actors=api_values.ACTOR_ROWS,
            entries=(api_entries.entry(
                entry_shells.ShellStartedBody(
                    domain_ids.ShellId("sh9"),
                    domain_content.TextContent("make test"),
                    outcomes.ExecutionMode.BACKGROUND,
                ),
            ),),
        ),
        1,
    )

    session_record = read_model.read(api_values.SESSION)
    assert session_record is not None
    response = mapper.session_data(session_record, live=True, repository_status=None, now=time.time())
    page = mapper.entry_page(read_model.entries_page(api_values.SESSION, limit=10))
    shell_body = page.entries[0].body

    assert (response.session.title, response.actors[0].model) == (
        api_values.SESSION_TITLE,
        api_values.MODEL_DISPLAY_NAME,
    )
    assert isinstance(shell_body, entry_responses.ShellStartedBodyResponse)
    assert (page.entries[0].type, shell_body.execution, shell_body.command.text) == (
        "shell_started",
        "background",
        "make test",
    )
