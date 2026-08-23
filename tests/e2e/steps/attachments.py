"""Attachment staging, launch delivery, and response checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.controls.models.attachment_reference import AttachmentReferenceBody
from sdk.client import BaqylauClient
from tests.e2e.testkit.launching import start_named_session
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    SessionSpecs,
    Sessions,
    StagedAttachments,
    Turns,
)


@when(parsers.parse(
    'I stage text attachment \'{file_name}\' with content '
    '\'{file_content}\' as "{attachment_name}"'
))
def stage_text_attachment(
    client: BaqylauClient,
    staged_attachments: StagedAttachments,
    file_name: str,
    file_content: str,
    attachment_name: str,
) -> None:
    staged_attachments.bind(
        attachment_name,
        client.uploads.stage(
            name=file_name,
            media_type="text/plain",
            data=file_content.encode(),
        ),
    )


@when(parsers.parse(
    'I launch session "{session_name}" as turn "{turn_name}" with attachment '
    '"{attachment_name}" and prompt'
))
def launch_with_attachment(
    client: BaqylauClient,
    workspace: str,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    wait_policy: WaitPolicy,
    staged_attachments: StagedAttachments,
    session_name: str,
    turn_name: str,
    attachment_name: str,
    docstring: str,
) -> None:
    staged = staged_attachments.get(attachment_name)
    start_named_session(
        client,
        workspace,
        session_specs,
        sessions,
        turns,
        wait_policy,
        session_name=session_name,
        turn_name=turn_name,
        prompt=docstring.strip(),
        attachments=(AttachmentReferenceBody(
            local_path=staged.path,
            display_name=staged.name,
            media_type=staged.mime,
        ),),
    )


@then(parsers.parse('staged attachment "{name}" is text file \'{file_name}\''))
def staged_attachment_is_text_file(
    staged_attachments: StagedAttachments,
    name: str,
    file_name: str,
) -> None:
    staged = staged_attachments.get(name)
    assert staged.ok
    assert staged.name == file_name
    assert staged.mime == "text/plain"
    assert not staged.is_image
