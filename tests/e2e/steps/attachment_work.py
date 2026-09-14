# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that assign work with attachments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, when

from tests.e2e.testkit import attachment_work
from tests.e2e.testkit.attachments import attachment_reference
from tests.e2e.testkit.references import WorkerKind
from tests.e2e.testkit.work_models import WorkRequest

if TYPE_CHECKING:
    from tests.e2e.testkit.references import AttachmentBundles, StagedAttachments
    from tests.e2e.testkit.work_contexts import WorkLaunchContext


@when(parsers.re(rf"I launch (?P<work_names>{attachment_work.LAUNCH_PATTERN}) and prompt"))
def launch_attachment_work(
    work_launch_context: WorkLaunchContext,
    staged_attachments: StagedAttachments,
    work_names: str,
    docstring: str,
) -> None:
    """Launch worker work with one staged attachment."""
    names = attachment_work.launch_names(work_names)
    staged = staged_attachments.get(names.attachment_source)
    started = work_launch_context.driver.launch(
        work_launch_context.session_specs.get(names.session),
        WorkRequest(
            names.work,
            docstring.strip(),
            worker_kind=WorkerKind(names.worker_type),
            attachments=(attachment_reference(staged),),
        ),
    )
    work_launch_context.sessions.bind(names.session, started.session)
    work_launch_context.works.bind(names.work, started.work)
    work_launch_context.turns.bind(names.work, started.work.turn)


@when(parsers.re(rf"I assign (?P<work_names>{attachment_work.ASSIGNMENT_PATTERN}) and prompt"))
def assign_attachment_work(
    work_launch_context: WorkLaunchContext,
    attachment_bundles: AttachmentBundles,
    work_names: str,
    docstring: str,
) -> None:
    """Assign work with one attachment bundle."""
    names = attachment_work.assignment_names(work_names)
    work = work_launch_context.driver.assign(
        work_launch_context.session_specs.get(names.session),
        work_launch_context.sessions.get(names.session),
        WorkRequest(
            names.work,
            docstring.strip(),
            worker_kind=WorkerKind(names.worker_type),
            attachments=attachment_bundles.get(names.attachment_source).attachments,
        ),
    )
    work_launch_context.works.bind(names.work, work)
    work_launch_context.turns.bind(names.work, work.turn)


@when(
    parsers.parse(
        'I assign attachment-only work "{work_name}" in session "{session_name}" '
        'with attachment bundle "{bundle_name}"',
    ),
)
def assign_attachment_only_work(
    work_launch_context: WorkLaunchContext,
    attachment_bundles: AttachmentBundles,
    session_name: str,
    work_name: str,
    bundle_name: str,
) -> None:
    """Assign lead work that has attachments but no prompt text."""
    work = work_launch_context.driver.assign(
        work_launch_context.session_specs.get(session_name),
        work_launch_context.sessions.get(session_name),
        WorkRequest(
            work_name,
            "",
            worker_kind=WorkerKind.LEAD,
            attachments=attachment_bundles.get(bundle_name).attachments,
        ),
    )
    work_launch_context.works.bind(work_name, work)
    work_launch_context.turns.bind(work_name, work.turn)
