"""Browser-origin actions and visible dashboard checks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pytest_bdd import given, parsers, then, when

from sdk.client import BaqylauClient, wait_for
from tests.e2e.testkit.browser import BrowserPlanAction, BrowserSessionDriver
from tests.e2e.testkit.planning import wait_for_plan_answer
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.process import ApplicationProcess
from tests.e2e.testkit.references import (
    BrowserActions,
    BrowserSessionForms,
    FileOperations,
    Plans,
    Questions,
    SessionContinuations,
    SessionSpecs,
    Sessions,
    Turns,
)
from tests.e2e.testkit.resume import assert_one_live_session, assert_saved_metadata
from tests.e2e.testkit.repository import RepositoryWorkspace

BROWSER_ACTIVE_RELEASE = ".baqylau-browser-active-release"


@given("the browser is on the session list")
def browser_is_on_session_list(browser_session_driver: BrowserSessionDriver) -> None:
    browser_session_driver.open_session_list()


@given(parsers.parse("the next browser application read omits usage for {harness}"))
def next_browser_application_read_omits_usage(
    browser_session_driver: BrowserSessionDriver,
    harness: str,
) -> None:
    browser_session_driver.omit_usage_from_next_application_read(harness)


@when("I open the browser session list")
def open_browser_session_list(browser_session_driver: BrowserSessionDriver) -> None:
    browser_session_driver.open_session_list()


@then(parsers.parse(
    "the browser shows the {harness} usage row without reloading the document"
))
def browser_shows_usage_without_reload(
    browser_session_driver: BrowserSessionDriver,
    harness: str,
) -> None:
    browser_session_driver.assert_usage_row_appears_without_reload(harness)


@when(parsers.parse('I release active browser work in session "{session_name}"'))
def release_active_browser_work(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
) -> None:
    working_directory = client.sessions.snapshot(
        sessions.get(session_name)
    ).data.session.working_directory
    Path(working_directory, BROWSER_ACTIVE_RELEASE).write_text(
        "release\n",
        encoding="utf-8",
    )


@when(parsers.parse(
    'I start browser session "{session_name}" as turn "{turn_name}" with prompt'
))
def start_browser_session(
    browser_session_driver: BrowserSessionDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    started = browser_session_driver.start(
        session_specs.get(session_name),
        docstring.strip(),
    )
    sessions.bind(session_name, started.session)
    turns.bind(turn_name, started.turn)


@when(parsers.parse(
    'I resume browser session "{session_name}" as turn "{turn_name}" with prompt'
))
def resume_browser_session(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_continuations: SessionContinuations,
    turns: Turns,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    resumed = browser_session_driver.resume(
        sessions.get(session_name),
        docstring.strip(),
    )
    sessions.replace(session_name, resumed.session)
    session_continuations.bind(session_name, resumed.continuation)
    turns.bind(turn_name, resumed.turn)


@when(parsers.parse(
    'I open fresh browser session form "{form_name}" for session "{session_name}"'
))
def open_fresh_browser_session_form(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    sessions: Sessions,
    form_name: str,
    session_name: str,
) -> None:
    browser_session_forms.bind(
        form_name,
        browser_session_driver.open_fresh_session_form(
            sessions.get(session_name),
        ),
    )


@when(parsers.parse(
    'I open configured browser session form "{form_name}" using session configuration '
    '"{configuration_name}"'
))
def open_configured_browser_session_form(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    session_specs: SessionSpecs,
    form_name: str,
    configuration_name: str,
) -> None:
    browser_session_forms.bind(
        form_name,
        browser_session_driver.open_configured_fresh_session_form(
            session_specs.get(configuration_name),
        ),
    )


@when(parsers.parse(
    'I type \'{text}\' in browser session form "{form_name}"'
))
def type_in_browser_session_form(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    form_name: str,
    text: str,
) -> None:
    browser_session_driver.type_session_form_prompt(
        browser_session_forms.get(form_name),
        text,
    )


@when(parsers.parse('I close browser session form "{form_name}"'))
def close_browser_session_form(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    form_name: str,
) -> None:
    browser_session_driver.close_session_form(browser_session_forms.get(form_name))


@then(parsers.parse(
    'browser session form "{form_name}" contains exact draft \'{text}\''
))
def browser_session_form_contains_draft(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    form_name: str,
    text: str,
) -> None:
    browser_session_driver.assert_session_form_prompt(
        browser_session_forms.get(form_name),
        text,
    )


@when(parsers.parse(
    'I switch browser session form "{form_name}" to resume mode'
))
def switch_browser_session_form_to_resume(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    form_name: str,
) -> None:
    browser_session_forms.replace(
        form_name,
        browser_session_driver.switch_session_form_to_resume(
            browser_session_forms.get(form_name),
        ),
    )


@then(parsers.parse(
    'browser session form "{form_name}" has not requested the resume catalog'
))
def fresh_browser_session_form_does_not_request_resume_catalog(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    form_name: str,
) -> None:
    browser_session_driver.assert_form_did_not_request_resume_catalog(
        browser_session_forms.get(form_name),
    )


@then(parsers.parse(
    'browser session form "{form_name}" requests the resume catalog'
))
def browser_session_form_requests_resume_catalog(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    form_name: str,
) -> None:
    browser_session_driver.assert_form_requested_resume_catalog(
        browser_session_forms.get(form_name),
    )


@then(parsers.parse(
    'browser session form "{form_name}" offers session "{session_name}"'
))
def browser_session_form_offers_session(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    sessions: Sessions,
    form_name: str,
    session_name: str,
) -> None:
    form = browser_session_forms.get(form_name)
    if form.source != sessions.get(session_name):
        raise AssertionError("browser session form belongs to a different session")
    browser_session_driver.assert_form_offers_source(form)


@when(parsers.parse(
    'I resume session "{session_name}" from browser session form "{form_name}" '
    'as turn "{turn_name}" with prompt'
))
def resume_from_browser_session_form(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    sessions: Sessions,
    session_continuations: SessionContinuations,
    turns: Turns,
    session_name: str,
    form_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    form = browser_session_forms.get(form_name)
    if form.source != sessions.get(session_name):
        raise AssertionError("browser session form belongs to a different session")
    resumed = browser_session_driver.resume_from_session_form(
        form,
        docstring.strip(),
    )
    sessions.replace(session_name, resumed.session)
    session_continuations.bind(session_name, resumed.continuation)
    turns.bind(turn_name, resumed.turn)


@then(parsers.parse('the browser shows session "{session_name}"'))
def browser_shows_session(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.assert_showing(sessions.get(session_name))


@when(parsers.parse('I close browser session "{session_name}"'))
def close_browser_session(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.close_session(sessions.get(session_name))


@when(parsers.parse('I open session "{session_name}" in the browser'))
def open_session_in_browser(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.open_session(sessions.get(session_name))


@when(parsers.parse(
    'I send browser prompt to session "{session_name}" as turn "{turn_name}"'
))
def send_browser_prompt(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    turns.bind(
        turn_name,
        browser_session_driver.send_prompt(
            sessions.get(session_name),
            docstring.strip(),
        ),
    )


@when(parsers.parse("I type composer draft '{text}' in the browser"))
def type_browser_composer_draft(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.type_composer_draft(text)


@then(parsers.parse("the browser composer contains exact draft '{text}'"))
def browser_composer_contains_draft(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.assert_composer_draft(text)


@then("the browser composer is empty")
def browser_composer_is_empty(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.assert_composer_draft("")


@when(parsers.parse(
    'I send the browser composer for session "{session_name}" as turn "{turn_name}"'
))
def send_browser_composer_draft(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    turn_name: str,
) -> None:
    turns.bind(
        turn_name,
        browser_session_driver.send_composer_draft(sessions.get(session_name)),
    )


@then(parsers.parse(
    'session "{session_name}" has composer draft \'{text}\' after a fresh application read'
))
def session_has_composer_draft_after_fresh_read(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    session_name: str,
    text: str,
) -> None:
    session = sessions.get(session_name)

    def saved() -> bool | None:
        draft = client.preferences.session_state(session).composer.draft
        return True if draft is not None and draft.text == text else None

    wait_for(
        f"session {session_name!r} composer draft to be saved",
        saved,
        timeout=wait_policy.feed,
    )


@then(parsers.parse(
    'session "{session_name}" has no composer draft after a fresh application read'
))
def session_has_no_composer_draft_after_fresh_read(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    session_name: str,
) -> None:
    session = sessions.get(session_name)

    def cleared() -> bool | None:
        draft = client.preferences.session_state(session).composer.draft
        return True if draft is None else None

    wait_for(
        f"session {session_name!r} composer draft to clear",
        cleared,
        timeout=wait_policy.feed,
    )


@when(parsers.parse('I reload browser session "{session_name}"'))
def reload_browser_session(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.reload(sessions.get(session_name))


@when(parsers.parse(
    'I reproduce a rebuild cursor overtake for session "{session_name}"'
))
def reproduce_rebuild_cursor_overtake(
    application_process: ApplicationProcess,
    sessions: Sessions,
    session_name: str,
) -> None:
    """Put the live process in the state observed in session 01a03de0.

    A rebuild process advanced the stored stream boundary while the daemon kept
    its earlier in-memory revision. Advancing both durable cursor spaces here
    reproduces that boundary without racing two test processes nondeterministically.
    The next real fact must use a cursor above this boundary.
    """
    session = sessions.get(session_name)
    path = application_process.config.data_directory / "main.db"
    with sqlite3.connect(path) as connection:
        found = connection.execute(
            "SELECT MAX(value) FROM ("
            "SELECT COALESCE(MAX(cursor), 0) AS value FROM canonical_events "
            "UNION ALL SELECT COALESCE(MAX(cursor), 0) FROM session_entries "
            "UNION ALL SELECT COALESCE(MAX(revision), 0) FROM session_data "
            "UNION ALL SELECT COALESCE(MAX(revision), 0) FROM session_data_actors)"
        ).fetchone()
        boundary = int(found[0]) + 1_000
        connection.execute(
            "UPDATE sqlite_sequence SET seq=? WHERE name='canonical_events'",
            (boundary,),
        )
        connection.execute(
            "UPDATE session_data SET revision=? WHERE session_id=?",
            (boundary, str(session.session_id)),
        )


@when("I reload the browser session list")
def reload_browser_session_list(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.reload_session_list()


@then(parsers.parse('the browser shows queued prompt \'{text}\''))
def browser_shows_queued_prompt(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.assert_queued_prompt(text)


@then(parsers.parse('the browser does not show queued prompt \'{text}\''))
def browser_does_not_show_queued_prompt(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.assert_no_queued_prompt(text)


@then(parsers.parse(
    'session "{session_name}" has queued prompt \'{text}\' after a fresh application read'
))
def session_has_queued_prompt_after_fresh_read(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
    text: str,
) -> None:
    queue = client.preferences.session_state(sessions.get(session_name)).composer.queue
    assert queue is not None
    assert [item.text for item in queue.items] == [text]


@then(parsers.parse('the browser shows the exact text \'{text}\''))
def browser_shows_exact_text(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.assert_text_visible(text)


@then(parsers.parse("the browser feed shows text containing '{text}'"))
def browser_feed_shows_text_containing(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.assert_feed_text_containing_visible(text)


@then(parsers.parse("the browser feed does not show text containing '{text}'"))
def browser_feed_does_not_show_text_containing(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.assert_feed_text_containing_absent(text)


@then(parsers.parse(
    'the browser renders added and removed colors for file operation "{operation_name}"'
))
def browser_renders_file_diff_colors(
    browser_session_driver: BrowserSessionDriver,
    file_operations: FileOperations,
    operation_name: str,
) -> None:
    browser_session_driver.assert_file_diff_colors(
        file_operations.get(operation_name),
    )


@then(parsers.parse(
    "the browser can load older session activity automatically containing '{text}'"
))
def browser_offers_older_session_activity(
    browser_session_driver: BrowserSessionDriver,
    text: str,
) -> None:
    browser_session_driver.assert_older_history_available(text)


@when("I scroll to older session activity in the browser")
def load_older_session_activity(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.load_older_history()


@when(parsers.parse(
    'I answer question "{question_name}" in the browser with option \'{option}\''
))
def answer_question_in_browser(
    browser_session_driver: BrowserSessionDriver,
    questions: Questions,
    turns: Turns,
    question_name: str,
    option: str,
) -> None:
    reference = questions.get(question_name)
    action = browser_session_driver.answer_question(reference, option)
    turns.replace(
        reference.turn_name,
        turns.get(reference.turn_name).resumed_after(action.cursor_before),
    )


@when(parsers.parse('I choose chat about question "{question_name}" in the browser'))
def discuss_question_in_browser(
    browser_session_driver: BrowserSessionDriver,
    questions: Questions,
    question_name: str,
) -> None:
    browser_session_driver.discuss_question(questions.get(question_name))


@when(parsers.parse(
    'I approve plan "{plan_name}" in the browser as action "{action_name}"'
))
def approve_plan_in_browser(
    browser_session_driver: BrowserSessionDriver,
    browser_actions: BrowserActions,
    plans: Plans,
    plan_name: str,
    action_name: str,
) -> None:
    reference = plans.get(plan_name)
    browser_actions.bind(
        action_name,
        browser_session_driver.decide_plan(
            reference,
            BrowserPlanAction.APPROVE,
        ),
    )


@then(parsers.parse(
    'plan "{plan_name}" is followed by final answer \'{text}\' '
    'after browser action "{action_name}"'
))
def plan_is_followed_by_browser_answer(
    client: BaqylauClient,
    browser_actions: BrowserActions,
    plans: Plans,
    wait_policy: WaitPolicy,
    plan_name: str,
    text: str,
    action_name: str,
) -> None:
    reference = plans.get(plan_name)
    action = browser_actions.get(action_name)
    if action.session != reference.session:
        raise AssertionError("browser action and plan belong to different sessions")
    wait_for_plan_answer(
        client,
        reference,
        after_cursor=action.cursor_before,
        text=text,
        name=plan_name,
        timeout=wait_policy.turn,
    )


@when(parsers.parse('I choose chat about plan "{plan_name}" in the browser'))
def discuss_plan_in_browser(
    browser_session_driver: BrowserSessionDriver,
    plans: Plans,
    plan_name: str,
) -> None:
    browser_session_driver.decide_plan(
        plans.get(plan_name),
        BrowserPlanAction.DISMISS,
    )


@when(parsers.parse(
    'I request plan changes \'{feedback}\' for plan "{plan_name}" in the browser'
))
def request_plan_changes_in_browser(
    browser_session_driver: BrowserSessionDriver,
    plans: Plans,
    feedback: str,
    plan_name: str,
) -> None:
    browser_session_driver.decide_plan(
        plans.get(plan_name),
        BrowserPlanAction.FEEDBACK,
        feedback=feedback,
    )


@then(parsers.parse(
    'the browser session card for "{session_name}" has status {status} and its canonical color'
))
def browser_session_card_has_status(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
    status: str,
) -> None:
    browser_session_driver.assert_session_card_status(
        sessions.get(session_name),
        status,
    )


@then(parsers.parse(
    "the browser session header has status {status} and its canonical color"
))
def browser_session_header_has_status(
    browser_session_driver: BrowserSessionDriver,
    status: str,
) -> None:
    browser_session_driver.assert_session_header_status(status)


@then(parsers.parse('the browser session header has title \'{title}\''))
def browser_session_header_has_title(
    browser_session_driver: BrowserSessionDriver,
    title: str,
) -> None:
    browser_session_driver.assert_session_header_title(title)


@then(parsers.parse(
    'the browser attention badge for "{session_name}" has status {status} '
    "and its canonical color"
))
def browser_attention_badge_has_status(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
    status: str,
) -> None:
    browser_session_driver.assert_attention_status(
        sessions.get(session_name),
        status,
    )


@then(parsers.parse("the browser has {count:d} asking session badges"))
def browser_has_asking_session_badges(
    browser_session_driver: BrowserSessionDriver,
    count: int,
) -> None:
    browser_session_driver.assert_asking_count(count)


@when(parsers.parse('I mute alerts for session "{session_name}" in the browser'))
def mute_session_alerts_in_browser(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.set_session_notifications_muted(
        sessions.get(session_name),
        True,
    )


@when(parsers.parse('I enable alerts for session "{session_name}" in the browser'))
def enable_session_alerts_in_browser(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.set_session_notifications_muted(
        sessions.get(session_name),
        False,
    )


@then(parsers.parse('browser alerts for session "{session_name}" are {state}'))
def browser_session_alerts_are(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
    state: str,
) -> None:
    if state not in ("muted", "enabled"):
        raise AssertionError(f"unknown browser alert state {state!r}")
    browser_session_driver.assert_session_notifications_muted(
        sessions.get(session_name),
        state == "muted",
    )


@when("I disable global alerts in the browser")
def disable_global_alerts_in_browser(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.set_global_notifications(False)


@when("I enable global alerts in the browser")
def enable_global_alerts_in_browser(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.set_global_notifications(True)


@then(parsers.parse("global browser alerts are {state}"))
def global_browser_alerts_are(
    browser_session_driver: BrowserSessionDriver,
    state: str,
) -> None:
    if state not in ("enabled", "disabled"):
        raise AssertionError(f"unknown global browser alert state {state!r}")
    browser_session_driver.assert_global_notifications(state == "enabled")


@then("the browser shows the configured workspace group")
def browser_shows_workspace_group(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.assert_workspace_visible()


@when("I hide the configured workspace group in the browser")
def hide_workspace_group_in_browser(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.hide_workspace()


@then("the browser hides the configured workspace group")
def browser_hides_workspace_group(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.assert_workspace_hidden()


@then("the browser event stream is connected")
def browser_event_stream_is_connected(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.assert_connected()


@when("I mark the current browser document for connection recovery")
def mark_browser_document_for_connection_recovery(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.mark_document_for_connection_recovery()


@then("the browser event stream reconnects without a reload")
def browser_event_stream_reconnects_without_reload(
    browser_session_driver: BrowserSessionDriver,
) -> None:
    browser_session_driver.assert_reconnected_without_reload()


@then(parsers.parse('the browser session list shows session "{session_name}"'))
def browser_session_list_shows_session(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.assert_session_card_visible(sessions.get(session_name))


@then(parsers.parse('the browser session list does not show session "{session_name}"'))
def browser_session_list_does_not_show_session(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    browser_session_driver.assert_session_card_absent(sessions.get(session_name))


@then(parsers.parse(
    'a fresh application session list does not contain session "{session_name}"'
))
def fresh_application_session_list_excludes_session(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
) -> None:
    excluded = sessions.get(session_name).session_id
    found = [item.session.session_id for item in client.sessions.list().sessions]
    assert excluded not in found, f"application session list contains {excluded!r}"


@then(parsers.parse(
    'browser sessions "{first_name}" and "{second_name}" share the isolated project group'
))
def browser_sessions_share_project_group(
    browser_session_driver: BrowserSessionDriver,
    repository_workspace: RepositoryWorkspace,
    sessions: Sessions,
    first_name: str,
    second_name: str,
) -> None:
    browser_session_driver.assert_shared_project_group(
        (sessions.get(first_name), sessions.get(second_name)),
        repository_workspace.repository_root,
        repository_workspace.working_directory,
    )


@then(parsers.parse(
    'browser resume "{session_name}" keeps its metadata and one live session'
))
def browser_resume_keeps_metadata_and_one_live_session(
    client: BaqylauClient,
    session_continuations: SessionContinuations,
    session_name: str,
) -> None:
    continuation = session_continuations.get(session_name)
    assert_saved_metadata(client, continuation)
    assert_one_live_session(client, continuation)


@then(parsers.parse(
    "the browser shows the {harness} {model} model usage limit for its default account"
))
def browser_shows_model_usage_limit(
    browser_session_driver: BrowserSessionDriver,
    harness: str,
    model: str,
) -> None:
    browser_session_driver.assert_default_model_usage_window(harness, model)


@then(parsers.parse(
    'browser session form "{form_name}" has no account selection'
))
def browser_session_form_has_no_account_selection(
    browser_session_driver: BrowserSessionDriver,
    browser_session_forms: BrowserSessionForms,
    form_name: str,
) -> None:
    browser_session_driver.assert_session_form_has_no_account_selection(
        browser_session_forms.get(form_name)
    )
