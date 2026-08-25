"""Attachment staging, launch delivery, and response checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from tests.e2e.testkit.attachments import attachment_reference, marker_png
from tests.e2e.testkit.launching import start_named_session
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    AttachmentBundleRef,
    AttachmentBundles,
    SessionSpecs,
    Sessions,
    StagedAttachments,
    Turns,
    WorkerKind,
    Works,
)
from tests.e2e.testkit.work import WorkDriver


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
    'I stage marker image \'{file_name}\' showing \'{marker}\' as "{attachment_name}"'
))
def stage_marker_image(
    client: BaqylauClient,
    staged_attachments: StagedAttachments,
    file_name: str,
    marker: str,
    attachment_name: str,
) -> None:
    staged_attachments.bind(
        attachment_name,
        client.uploads.stage(
            name=file_name,
            media_type="image/png",
            data=marker_png(marker),
        ),
    )


@when(parsers.parse('I group staged attachments as "{bundle_name}"'))
def group_staged_attachments(
    staged_attachments: StagedAttachments,
    attachment_bundles: AttachmentBundles,
    bundle_name: str,
    datatable: list[list[str]],
) -> None:
    if not datatable or datatable[0] != ["attachment"]:
        raise AssertionError("attachment bundle table must have an attachment column")
    names = tuple(row[0].strip() for row in datatable[1:] if len(row) == 1)
    if not names or len(names) != len(datatable) - 1 or any(not name for name in names):
        raise AssertionError("attachment bundle names must not be empty")
    attachment_bundles.bind(
        bundle_name,
        AttachmentBundleRef(tuple(
            attachment_reference(staged_attachments.get(name))
            for name in names
        )),
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
        attachments=(attachment_reference(staged),),
    )


@when(parsers.parse(
    'I launch session "{session_name}" and assign work "{work_name}" to the '
    '{worker_type} with attachment "{attachment_name}" and prompt'
))
def launch_attachment_work(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    staged_attachments: StagedAttachments,
    session_name: str,
    work_name: str,
    worker_type: str,
    attachment_name: str,
    docstring: str,
) -> None:
    staged = staged_attachments.get(attachment_name)
    started = work_driver.launch(
        session_specs.get(session_name),
        work_name=work_name,
        worker_kind=WorkerKind(worker_type),
        prompt=docstring.strip(),
        attachments=(attachment_reference(staged),),
    )
    sessions.bind(session_name, started.session)
    works.bind(work_name, started.work)
    turns.bind(work_name, started.work.turn)


@when(parsers.parse(
    'I assign work "{work_name}" in session "{session_name}" to the {worker_type} '
    'with attachment bundle "{bundle_name}" and prompt'
))
def assign_attachment_work(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    attachment_bundles: AttachmentBundles,
    session_name: str,
    work_name: str,
    worker_type: str,
    bundle_name: str,
    docstring: str,
) -> None:
    work = work_driver.assign(
        session_specs.get(session_name),
        sessions.get(session_name),
        work_name=work_name,
        worker_kind=WorkerKind(worker_type),
        prompt=docstring.strip(),
        attachments=attachment_bundles.get(bundle_name).attachments,
    )
    works.bind(work_name, work)
    turns.bind(work_name, work.turn)


@when(parsers.parse(
    'I assign attachment-only work "{work_name}" in session "{session_name}" '
    'with attachment bundle "{bundle_name}"'
))
def assign_attachment_only_work(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    attachment_bundles: AttachmentBundles,
    session_name: str,
    work_name: str,
    bundle_name: str,
) -> None:
    work = work_driver.assign(
        session_specs.get(session_name),
        sessions.get(session_name),
        work_name=work_name,
        worker_kind=WorkerKind.LEAD,
        prompt="",
        attachments=attachment_bundles.get(bundle_name).attachments,
    )
    works.bind(work_name, work)
    turns.bind(work_name, work.turn)


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


@then(parsers.parse('staged attachment "{name}" is PNG image \'{file_name}\''))
def staged_attachment_is_png_image(
    staged_attachments: StagedAttachments,
    name: str,
    file_name: str,
) -> None:
    staged = staged_attachments.get(name)
    assert staged.ok
    assert staged.name == file_name
    assert staged.mime == "image/png"
    assert staged.is_image
