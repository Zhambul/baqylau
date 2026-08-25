"""Named web search and fetch acquisition and checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.entry import EntryResponse, SearchBodyResponse, WebBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    SearchRef,
    Searches,
    Turns,
    WebFetchRef,
    WebFetches,
)


def _search(snapshot: SessionSnapshot, reference: SearchRef) -> SearchBodyResponse:
    found = [
        entry.body
        for entry in snapshot.entries
        if entry.entry_id == reference.entry_id
        and isinstance(entry.body, SearchBodyResponse)
    ]
    if len(found) != 1:
        raise AssertionError(f"search {reference.entry_id!r} has {len(found)} matches")
    return found[0]


def _web_fetch_entry(
    snapshot: SessionSnapshot,
    reference: WebFetchRef,
) -> EntryResponse:
    found = [
        entry
        for entry in snapshot.entries
        if entry.entry_id == reference.entry_id and isinstance(entry.body, WebBodyResponse)
    ]
    if len(found) != 1:
        raise AssertionError(f"web fetch {reference.entry_id!r} has {len(found)} matches")
    return found[0]


@when(parsers.parse(
    'I name the search in work "{work_name}" with query containing '
    '\'{query}\' "{search_name}"'
))
def name_search(
    client: BaqylauClient,
    turns: Turns,
    searches: Searches,
    wait_policy: WaitPolicy,
    work_name: str,
    query: str,
    search_name: str,
) -> None:
    turn = turns.get(work_name)
    searches.bind(
        search_name,
        selectors.search(
            client.sessions.watch(turn.session),
            turn_reference=turn,
            query_contains=query,
            timeout=wait_policy.feed,
        ),
    )


@when(parsers.parse(
    'I name the web fetch in work "{work_name}" for URL '
    '\'{url}\' "{fetch_name}"'
))
def name_web_fetch(
    client: BaqylauClient,
    turns: Turns,
    web_fetches: WebFetches,
    wait_policy: WaitPolicy,
    work_name: str,
    url: str,
    fetch_name: str,
) -> None:
    turn = turns.get(work_name)
    web_fetches.bind(
        fetch_name,
        selectors.web_fetch(
            client.sessions.watch(turn.session),
            turn_reference=turn,
            url=url,
            timeout=wait_policy.feed,
        ),
    )


@then(parsers.parse('search "{name}" has state {state}'))
def search_has_state(
    client: BaqylauClient,
    searches: Searches,
    name: str,
    state: str,
) -> None:
    reference = searches.get(name)
    assert _search(client.sessions.snapshot(reference.session), reference).state == state


@then(parsers.parse('web fetch "{name}" has state {state}'))
def web_fetch_has_state(
    client: BaqylauClient,
    web_fetches: WebFetches,
    name: str,
    state: str,
) -> None:
    reference = web_fetches.get(name)
    entry = _web_fetch_entry(client.sessions.snapshot(reference.session), reference)
    assert isinstance(entry.body, WebBodyResponse)
    assert entry.body.state == state


@then(parsers.parse('web fetch "{name}" has result containing \'{text}\''))
def web_fetch_has_result(
    client: BaqylauClient,
    web_fetches: WebFetches,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    reference = web_fetches.get(name)

    def contains(snapshot: SessionSnapshot) -> bool | None:
        entry = _web_fetch_entry(snapshot, reference)
        assert isinstance(entry.body, WebBodyResponse)
        result = entry.body.result
        return True if result is not None and text in result.text else None

    client.sessions.watch(reference.session).wait(
        f"web fetch {name!r} result to contain {text!r}",
        contains,
        timeout=wait_policy.feed,
    )
