# Copyright (c) 2026 Zhambyl Yermagambet
"""A stored core fact that the public API refuses does not stop a page or a replay.

Before the host checked facts against the public API, one Claude Code goal
check-in became an assignment finish with an empty ID. A projection rebuild
read it from the history and failed. Extensions never saw that fact.
"""

from pathlib import Path

from tests import canonical_sessiondata_values as session_values
from tests.canonical_sessiondata_components import domain
from tests.extension_host import reaction_fixture as reactions

# The extension facts before and after the refused core fact.
VISIBLE_FACTS = 2
EMPTY_ASSIGNMENT = "UPDATE canonical_events SET payload=json_set(payload, '$.assignment_id', '') WHERE cursor=?"


def test_a_refused_stored_fact_is_left_out(tmp_path: Path) -> None:
    """The page returns the facts before and after the refused one, so a consumer moves past it."""
    case = reactions.installed(tmp_path, core=False)
    reactions.append_extensions(case)
    reactions.append_core(case, _assignment_finish())
    refused = case.store.current_fact_page(0, 10).head
    with case.store.database.write() as connection:
        connection.execute(EMPTY_ASSIGNMENT, (refused,))
    reactions.append_extensions(case)

    page = case.store.current_fact_page(0, 10)

    assert refused not in {stored.cursor for stored in page.facts}
    assert len(page.facts) == VISIBLE_FACTS
    assert page.facts[-1].cursor > refused


def _assignment_finish() -> domain.event_base.EventPayload:
    return domain.event_actor.ActorAssignmentFinished(
        session_values.FIRST_ASSIGNMENT_ID, domain.outcomes.Outcome.SUCCEEDED, domain.content.TextContent("done"), None,
    )
