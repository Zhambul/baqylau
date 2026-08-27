"""Session control actions and control outcome checks."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from pytest_bdd import parsers, then, when

from api.controls.models.control_outcome_response import (
    MessageDeliveryResultResponse,
    RewindResultResponse,
)
from api.sessiondata.models.entry import MessageBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit.references import Controls, SessionSpecs, Sessions, Turns
from tests.e2e.testkit.policy import WaitPolicy


@when(parsers.parse(
    'I request backgrounding in session "{session_name}" as control "{control_name}"'
))
def request_backgrounding(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.background(sessions.get(session_name)))


@when(parsers.parse(
    'I request interruption in session "{session_name}" as control "{control_name}"'
))
def request_interruption(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.interrupt(sessions.get(session_name)))


@when(parsers.parse(
    'I send native command \'{command}\' to session "{session_name}" '
    'as control "{control_name}"'
))
def send_native_command(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    command: str,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(
        control_name,
        client.sessions.send(sessions.get(session_name), command),
    )


@when(parsers.parse(
    'I rename session "{session_name}" to \'{new_name}\' as control "{control_name}"'
))
def rename_session(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    new_name: str,
    control_name: str,
) -> None:
    controls.bind(
        control_name,
        client.sessions.rename(sessions.get(session_name), new_name),
    )


@when(parsers.parse(
    'I request an automatic name for session "{session_name}" as control "{control_name}"'
))
def auto_name_session(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.auto_name(sessions.get(session_name)))


@then(parsers.parse('session "{session_name}" has a concise title unlike \'{fallback}\''))
def session_has_concise_title(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    session_name: str,
    fallback: str,
) -> None:
    def concise(snapshot: SessionSnapshot) -> bool | None:
        title = snapshot.data.session.title
        if not title or title == fallback:
            return None
        return True if (
            "\n" not in title
            and len(title) <= 80
            and 2 <= len(title.split()) <= 8
            and "http" not in title.casefold()
            and "<" not in title
            and ">" not in title
        ) else None

    client.sessions.watch(sessions.get(session_name)).wait(
        f"session {session_name!r} to receive a concise automatic title",
        concise,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('the application contains exactly session "{session_name}"'))
def application_contains_exactly_session(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
) -> None:
    found = tuple(item.session.session_id for item in client.sessions.list().sessions)
    assert found == (sessions.get(session_name).session_id,)


@when(parsers.parse(
    'I select model {model} in session "{session_name}" as control "{control_name}"'
))
def select_model(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    model: str,
    control_name: str,
) -> None:
    controls.bind(
        control_name,
        client.sessions.select_model(sessions.get(session_name), model),
    )


@when(parsers.parse(
    'I select {effort} effort in session "{session_name}" as control "{control_name}"'
))
def select_effort(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    effort: str,
    control_name: str,
) -> None:
    controls.bind(
        control_name,
        client.sessions.select_effort(sessions.get(session_name), effort),
    )


@when(parsers.parse(
    'I request compaction in session "{session_name}" as control "{control_name}"'
))
def request_compaction(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.compact(sessions.get(session_name)))


@when(parsers.parse(
    'I rewind session "{session_name}" to turn "{turn_name}" with {mode} mode '
    'as control "{control_name}"'
))
def apply_rewind(
    client: BaqylauClient,
    sessions: Sessions,
    turns: Turns,
    controls: Controls,
    session_name: str,
    turn_name: str,
    mode: str,
    control_name: str,
) -> None:
    session = sessions.get(session_name)
    target = turns.get(turn_name)
    if target.session != session:
        raise AssertionError(f"turn {turn_name!r} does not belong to session {session_name!r}")
    if (
        target.actor_id is None
        or target.prompt_cursor is None
        or target.prompt_message_id is None
    ):
        raise AssertionError(f"turn {turn_name!r} does not have a resolved prompt identity")
    snapshot = client.sessions.snapshot(session)
    newer_prompt_count = sum(
        1
        for entry in snapshot.entries
        if entry.cursor > target.prompt_cursor
        and entry.actor_id == target.actor_id
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.role == "user"
        and entry.body.phase == "prompt"
    )
    controls.bind(
        control_name,
        client.sessions.apply_rewind(
            session,
            target_message_id=target.prompt_message_id,
            target_text=target.prompt,
            newer_prompt_count=newer_prompt_count,
            mode=mode,
        ),
    )


@when(parsers.parse(
    'I close session "{session_name}" as control "{control_name}"'
))
def close_session(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.close(sessions.get(session_name)))


@then(parsers.parse('control "{name}" response is accepted'))
def control_response_is_accepted(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.status_code == 200, f"control {name!r} returned {receipt.outcome}"


@then(parsers.parse('control "{name}" response is rejected'))
def control_response_is_rejected(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.status_code == 409, f"control {name!r} returned {receipt.outcome}"


@then(parsers.parse('control "{name}" outcome is acknowledged'))
def control_outcome_is_acknowledged(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.outcome.status == "acknowledged"


@then(parsers.parse('control "{name}" outcome is rejected'))
def control_outcome_is_rejected(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.outcome.status == "rejected"


@then(parsers.parse('control "{name}" reports queued delivery'))
def control_reports_queued_delivery(controls: Controls, name: str) -> None:
    outcome = controls.get(name).outcome
    assert isinstance(outcome, MessageDeliveryResultResponse)
    assert outcome.status == "queued"


def _codex_queue_contains(
    codex_home: Path,
    session_id: str,
) -> bool:
    queue_path = codex_home / "queue_1.sqlite"
    if not queue_path.is_file():
        return False
    with sqlite3.connect(f"file:{queue_path}?mode=ro", uri=True) as connection:
        return connection.execute(
            "SELECT 1 FROM queued_items WHERE thread_id = ? LIMIT 1",
            (session_id,),
        ).fetchone() is not None


def _claude_queue_contains(
    claude_home: Path,
    session_id: str,
    text: str,
) -> bool:
    for source in claude_home.rglob("*.jsonl"):
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                record.get("type") == "queue-operation"
                and record.get("operation") == "enqueue"
                and record.get("sessionId") == session_id
                and record.get("content") == text
            ):
                return True
    return False


@then(parsers.parse(
    'harness queue for session "{session_name}" contains prompt \'{text}\''
))
def harness_queue_contains_prompt(
    isolated_codex_home: Path,
    isolated_claude_home: Path,
    session_specs: SessionSpecs,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    session_name: str,
    text: str,
) -> None:
    spec = session_specs.get(session_name)
    session_id = sessions.get(session_name).session_id
    deadline = time.monotonic() + wait_policy.pipeline
    while True:
        found = (
            _codex_queue_contains(isolated_codex_home, session_id)
            if spec.harness == "codex"
            else _claude_queue_contains(isolated_claude_home, session_id, text)
        )
        if found:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"{spec.harness} queue does not contain the expected prompt"
            )
        time.sleep(0.05)


@then(parsers.parse(
    'session "{session_name}" has control "{control_name}" queued as prompt '
    "'{text}' after a fresh application read"
))
def session_has_durable_queued_prompt(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    wait_policy: WaitPolicy,
    session_name: str,
    control_name: str,
    text: str,
) -> None:
    expected = [(controls.get(control_name).request_id, text)]
    deadline = time.monotonic() + wait_policy.pipeline
    while True:
        queue = client.preferences.session_state(
            sessions.get(session_name)
        ).composer.queue
        if queue is not None and [
            (item.request_id, item.text) for item in queue.items
        ] == expected:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"queue does not contain {expected!r}")
        time.sleep(0.05)


@then(parsers.parse(
    'session "{session_name}" has no queued prompts after a fresh application read'
))
def session_has_no_durable_queued_prompts(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
) -> None:
    queue = client.preferences.session_state(
        sessions.get(session_name)
    ).composer.queue
    assert queue is None


@then(parsers.parse('control "{control_name}" restores turn "{turn_name}"'))
def control_restores_turn(
    controls: Controls,
    turns: Turns,
    control_name: str,
    turn_name: str,
) -> None:
    outcome = controls.get(control_name).outcome
    assert isinstance(outcome, RewindResultResponse)
    assert outcome.restored_text == turns.get(turn_name).prompt
