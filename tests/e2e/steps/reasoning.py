"""Named reasoning-trace acquisition and checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.entry import ReasoningBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import ReasoningTraceRef, ReasoningTraces, Works


def _parts(
    snapshot: SessionSnapshot,
    reference: ReasoningTraceRef,
) -> tuple[ReasoningBodyResponse, ...]:
    by_id = {entry.entry_id: entry for entry in snapshot.entries}
    found: list[ReasoningBodyResponse] = []
    for entry_id in reference.entry_ids:
        entry = by_id.get(entry_id)
        if entry is None or not isinstance(entry.body, ReasoningBodyResponse):
            raise AssertionError(f"reasoning entry {entry_id!r} is absent")
        if entry.actor_id != reference.actor_id:
            raise AssertionError(
                f"reasoning entry {entry_id!r} belongs to actor {entry.actor_id!r}"
            )
        found.append(entry.body)
    return tuple(found)


@when(parsers.parse(
    'I name the reasoning trace in work "{work_name}" "{trace_name}"'
))
def name_reasoning_trace(
    client: BaqylauClient,
    works: Works,
    reasoning_traces: ReasoningTraces,
    wait_policy: WaitPolicy,
    work_name: str,
    trace_name: str,
) -> None:
    work = works.get(work_name)
    reasoning_traces.bind(
        trace_name,
        selectors.reasoning_trace(
            client.sessions.watch(work.session),
            turn_reference=work.turn,
            timeout=wait_policy.feed,
        ),
    )


@then(parsers.parse('reasoning trace "{name}" has at least {count:d} part'))
def reasoning_trace_has_parts(
    client: BaqylauClient,
    reasoning_traces: ReasoningTraces,
    name: str,
    count: int,
) -> None:
    reference = reasoning_traces.get(name)
    parts = _parts(client.sessions.snapshot(reference.session), reference)
    assert len(parts) >= count, f"reasoning trace {name!r} has {len(parts)} parts"


@then(parsers.parse('each part of reasoning trace "{name}" contains text'))
def reasoning_trace_parts_contain_text(
    client: BaqylauClient,
    reasoning_traces: ReasoningTraces,
    name: str,
) -> None:
    reference = reasoning_traces.get(name)
    parts = _parts(client.sessions.snapshot(reference.session), reference)
    empty = [index for index, part in enumerate(parts) if not part.content.text.strip()]
    assert not empty, f"reasoning trace {name!r} has empty parts at indexes {empty}"
