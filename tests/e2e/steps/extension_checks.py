# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check what the enabled extension packages see of a live session."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

from sdk.client import wait_for
from tests.e2e.testkit import extension_bursts, extension_queries as reads
from tests.e2e.testkit.extension_sources import PACKAGES

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tests.e2e.testkit.expected_findings import ExpectedAuditErrors
    from tests.e2e.testkit.extension_context import ExtensionContext

FINISHED_OUTCOMES = frozenset(("succeeded", "failed"))
READY_STATUS = 200
UNAVAILABLE_STATUS = 503


def _call_finished(context: ExtensionContext, scope: Mapping[str, str], group: str) -> bool | None:
    return True if reads.invocation_outcomes(context.host, scope, group) & FINISHED_OUTCOMES else None


def _activity_has(context: ExtensionContext, scope: Mapping[str, str], operation: str) -> bool | None:
    return True if operation in reads.git_operations(context.host, scope) else None


@then(parsers.parse('extension package "{name}" projects a "{entry_type}" entry in session "{session_name}"'))
def package_projects_entry(extension_context: ExtensionContext, name: str, entry_type: str, session_name: str) -> None:
    """Wait for one entry of the package in the session feed."""
    owner = PACKAGES[name].owner
    found = partial(extension_context.has_entry, session_name, entry_type, owner)
    wait_for(f"a {entry_type!r} entry of {owner}", found, timeout=extension_context.wait_policy.feed)


@then(parsers.parse('the adapters "{group}" call in session "{session_name}" finishes'))
def adapters_call_finishes(extension_context: ExtensionContext, group: str, session_name: str) -> None:
    """Wait for the adapters projector to record one finished call of the group from the real CLI."""
    finished = partial(_call_finished, extension_context, extension_context.session_scope(session_name), group)
    wait_for(f"a finished adapters {group!r} call", finished, timeout=extension_context.wait_policy.feed)


@then(parsers.parse(
    '{count:d} parallel reads of the adapters calls of session "{session_name}" are ready or unavailable',
))
def adapters_reads_in_parallel(
    extension_context: ExtensionContext,
    expected_audit_errors: ExpectedAuditErrors,
    count: int,
    session_name: str,
) -> None:
    """Read the adapters calls from many feed rows at the same moment.

    The worker's live lane holds 16 calls. The host answers a read that the
    worker refuses with 503, never with an internal error, and records the
    refusals of one burst as one audit row, which this scenario expects.
    """
    scope = extension_context.session_scope(session_name)
    query_id = f"{reads.ADAPTERS}.invocations"
    reads_burst = extension_bursts.burst(extension_context, reads.ADAPTERS, query_id, scope, count)
    statuses = reads_burst.statuses
    assert set(statuses) <= {READY_STATUS, UNAVAILABLE_STATUS}, sorted(statuses)
    assert READY_STATUS in statuses, sorted(statuses)
    assert len(reads_burst.audit_rows) == int(UNAVAILABLE_STATUS in statuses), reads_burst
    expected_audit_errors.expect(reads_burst.audit_rows)


@then(parsers.parse('the adapters extension answers no calls for session "{session_name}"'))
def adapters_answers_nothing(extension_context: ExtensionContext, session_name: str) -> None:
    """Refuse the query of a disabled package."""
    scope = extension_context.session_scope(session_name)
    assert not reads.answers(extension_context.host, reads.ADAPTERS, f"{reads.ADAPTERS}.invocations", scope)


@then(parsers.parse('the git activity of the repository of session "{session_name}" has a "{operation}" call'))
def git_activity_has_call(extension_context: ExtensionContext, session_name: str, operation: str) -> None:
    """Resolve the session's repository, and wait for its adapters activity through the service."""
    snapshot = extension_context.client.sessions.snapshot(extension_context.sessions.get(session_name))
    scope = reads.repository_scope(extension_context.host, snapshot.session_data.session.working_directory)
    found = partial(_activity_has, extension_context, scope, operation)
    wait_for(f"a {operation!r} call in the git activity", found, timeout=extension_context.wait_policy.feed)
