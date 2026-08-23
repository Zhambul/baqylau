"""Application preference actions and preference state checks."""

from __future__ import annotations

import time

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from tests.e2e.testkit.references import Sessions


@when(parsers.parse(
    'I save new-session choices for {harness} model {model} and {effort} effort'
))
def save_new_session_choices(
    client: BaqylauClient,
    workspace: str,
    harness: str,
    model: str,
    effort: str,
) -> None:
    client.preferences.save_new_session_choices(
        workspace=workspace,
        harness=harness,
        model=model,
        effort=effort,
    )


@when(parsers.parse('I save new-session draft \'{text}\''))
def save_new_session_draft(
    client: BaqylauClient,
    workspace: str,
    text: str,
) -> None:
    client.preferences.save_new_session_draft(
        workspace=workspace,
        text=text,
        sequence=time.time(),
    )


@when(parsers.parse('I save composer draft \'{text}\' for session "{name}"'))
def save_composer_draft(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
    text: str,
) -> None:
    client.preferences.save_composer_draft(
        sessions.get(name),
        text=text,
        origin="e2e",
        sequence=time.time(),
    )


@when(parsers.parse('I queue message \'{text}\' for session "{name}"'))
def queue_message(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
    text: str,
) -> None:
    client.preferences.save_composer_queue(
        sessions.get(name),
        messages=(text,),
        origin="e2e",
    )


@when(parsers.parse('I set view mode {view_mode} for session "{name}"'))
def set_view_mode(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
    view_mode: str,
) -> None:
    client.preferences.set_view_mode(sessions.get(name), view_mode)


@when(parsers.parse('I mute notifications for session "{name}"'))
def mute_notifications(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
) -> None:
    client.preferences.set_notifications_muted(sessions.get(name), True)


@when(parsers.parse('I hide tasks for session "{name}"'))
def hide_tasks(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
) -> None:
    client.preferences.set_tasks_hidden(sessions.get(name), True)


@then(parsers.parse(
    'global new-session choices are {harness} model {model} and {effort} effort'
))
def global_new_session_choices_are_saved(
    client: BaqylauClient,
    workspace: str,
    harness: str,
    model: str,
    effort: str,
) -> None:
    found = client.preferences.global_state().preferences.new_session
    assert found.working_directory == workspace
    assert found.harness == harness
    assert found.model == model
    assert found.effort == effort


@then(parsers.parse('global new-session draft is \'{text}\''))
def global_new_session_draft_is_saved(
    client: BaqylauClient,
    workspace: str,
    text: str,
) -> None:
    found = [
        item
        for item in client.preferences.global_state().preferences.new_session_drafts
        if item.working_directory == workspace
    ]
    assert len(found) == 1, f"workspace {workspace!r} has {len(found)} new-session drafts"
    assert found[0].text == text


@then(parsers.parse('composer draft for session "{name}" is \'{text}\''))
def composer_draft_is_saved(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
    text: str,
) -> None:
    found = client.preferences.session_state(sessions.get(name)).composer.draft
    assert found is not None
    assert found.text == text
    assert found.origin == "e2e"


@then(parsers.parse('composer queue for session "{name}" contains \'{text}\''))
def composer_queue_contains(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
    text: str,
) -> None:
    found = client.preferences.session_state(sessions.get(name)).composer.queue
    assert found is not None
    assert [item.text for item in found.items] == [text]
    assert found.origin == "e2e"


@then(parsers.parse('view mode for session "{name}" is {view_mode}'))
def view_mode_is_saved(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
    view_mode: str,
) -> None:
    found = client.preferences.session_state(sessions.get(name)).preferences.view_mode
    assert found == view_mode


@then(parsers.parse('notifications for session "{name}" are muted'))
def notifications_are_muted(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
) -> None:
    found = client.preferences.session_state(sessions.get(name)).preferences
    assert found.notifications_muted


@then(parsers.parse('tasks for session "{name}" are hidden'))
def tasks_are_hidden(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
) -> None:
    found = client.preferences.session_state(sessions.get(name)).preferences
    assert found.tasks_hidden
