"""Shared session-launch action for Gherkin step modules."""

from __future__ import annotations

from api.controls.models.attachment_reference import AttachmentReferenceBody
from sdk.client import BaqylauClient
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import SessionSpecs, Sessions, Turns


def start_named_session(
    client: BaqylauClient,
    workspace: str,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    wait_policy: WaitPolicy,
    *,
    session_name: str,
    turn_name: str,
    prompt: str,
    attachments: tuple[AttachmentReferenceBody, ...] = (),
) -> None:
    spec = session_specs.get(session_name)
    launch = client.sessions.launch(
        spec.harness,
        workspace=spec.workspace or workspace,
        prompt=prompt,
        model=spec.model,
        effort=spec.effort,
        attachments=attachments,
        account_id=spec.account_id,
    )
    session = client.sessions.wait_for_session(launch, wait_policy.session_announcement)
    sessions.bind(session_name, session)
    turns.bind(
        turn_name,
        selectors.launched_turn(
            client.sessions.watch(session),
            wait_policy.feed,
        ),
    )
