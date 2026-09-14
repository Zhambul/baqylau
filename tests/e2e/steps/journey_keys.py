# Copyright (c) 2026 Zhambyl Yermagambet
"""Operate native terminal controls and check their visible state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, then, when

if TYPE_CHECKING:
    from tests.e2e.testkit.journeys import JourneyDriver
    from tests.e2e.testkit.references import SessionJourneys


@when(parsers.parse("I press terminal key '{key}' in journey session \"{session_name}\""))
def press_journey_terminal_key(
    journey_driver: JourneyDriver, session_journeys: SessionJourneys, session_name: str, key: str,
) -> None:
    """Send one key to a journey terminal."""
    journey_driver.press_terminal_key(session_journeys.get(session_name), key)


@then(parsers.parse("journey session \"{session_name}\" terminal contains '{text}'"))
def journey_terminal_contains(
    journey_driver: JourneyDriver, session_journeys: SessionJourneys, session_name: str, text: str,
) -> None:
    """Check text in a journey terminal."""
    journey_driver.wait_for_terminal_text(session_journeys.get(session_name), text)
