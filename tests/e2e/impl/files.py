"""File changes as typed, content-carrying dashboard entries."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from pytest_bdd import given, parsers, then

from api.sessiondata.models.entry import FileBodyResponse
from domain.values import FileAction
from impl.world import World, feed
from support import observe
from support.daemon import Daemon

FILE_OPERATION_FIXTURE = "baqylau-e2e-file.txt"
FEED_SETTLE_TIMEOUT_SECONDS = 60.0


@pytest.fixture
def file_operation_path(workspace: str) -> Iterator[str]:
    path = os.path.join(workspace, FILE_OPERATION_FIXTURE)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


@given("the file operation fixture does not exist")
def _file_operation_fixture_does_not_exist(file_operation_path: str) -> None:
    assert not os.path.exists(file_operation_path)


@then(parsers.parse(
    "the feed shows a succeeded {action} file operation containing '{content}'"
))
def _feed_shows_file_operation(
    world: World,
    daemon: Daemon,
    file_operation_path: str,
    action: str,
    content: str,
) -> None:
    expected_action = FileAction(action)

    def matching() -> list[FileBodyResponse] | None:
        found = [
            entry.body
            for entry in feed(world, daemon)
            if isinstance(entry.body, FileBodyResponse)
            and entry.body.path == file_operation_path
            and entry.body.action == expected_action
            and entry.body.state == "succeeded"
            and entry.body.content is not None
            and content in entry.body.content.text
        ]
        return found or None

    observe.until(
        f"a succeeded {action} entry for {file_operation_path} carrying {content!r}",
        matching,
        timeout=FEED_SETTLE_TIMEOUT_SECONDS,
    )
