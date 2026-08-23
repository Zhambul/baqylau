"""Named skill acquisition and lifecycle checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot, SkillState
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import SkillRef, Skills, Turns


def _skill(snapshot: SessionSnapshot, reference: SkillRef) -> SkillState:
    found = [item for item in snapshot.skills() if item.skill_id == reference.skill_id]
    if len(found) != 1:
        raise AssertionError(f"skill {reference.skill_id!r} has {len(found)} matches")
    return found[0]


@when(parsers.parse(
    'I name the skill in turn "{turn_name}" with exact name '
    '\'{exact_name}\' "{skill_name}"'
))
def name_skill(
    client: BaqylauClient,
    turns: Turns,
    skills: Skills,
    wait_policy: WaitPolicy,
    turn_name: str,
    exact_name: str,
    skill_name: str,
) -> None:
    turn = turns.get(turn_name)
    found = selectors.skill(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        exact_name=exact_name,
        timeout=wait_policy.feed,
    )
    skills.bind(skill_name, found)


@then(parsers.parse('skill "{name}" has state {state}'))
def skill_has_state(
    client: BaqylauClient,
    skills: Skills,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    reference = skills.get(name)
    client.sessions.watch(reference.session).wait(
        f"skill {name!r} to have state {state!r}",
        lambda snapshot: True if _skill(snapshot, reference).state == state else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('skill "{name}" has no arguments'))
def skill_has_no_arguments(
    client: BaqylauClient,
    skills: Skills,
    name: str,
) -> None:
    reference = skills.get(name)
    assert not _skill(client.sessions.snapshot(reference.session), reference).arguments
