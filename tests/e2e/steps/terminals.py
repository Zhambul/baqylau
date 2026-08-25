"""Real terminal topology and pane gesture steps."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from tests.e2e.testkit.references import References, SessionJourneys
from tests.e2e.testkit.terminals import PaneGeometry, RealTerminalDriver, TerminalFocus


@then(parsers.parse('journey session "{session_name}" has its exact terminal pane set'))
def session_has_exact_terminal_panes(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
) -> None:
    real_terminal_driver.wait_for_panes(session_journeys.get(session_name))


@then(parsers.parse('journey session "{session_name}" has no auxiliary terminal panes'))
def session_has_no_auxiliary_panes(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
) -> None:
    real_terminal_driver.wait_for_no_auxiliary_panes(session_journeys.get(session_name))


@when(parsers.parse('I remember journey session "{session_name}" pane geometry as "{geometry_name}"'))
def remember_pane_geometry(
    real_terminal_driver: RealTerminalDriver,
    terminal_pane_geometries: References[PaneGeometry],
    session_journeys: SessionJourneys,
    session_name: str,
    geometry_name: str,
) -> None:
    terminal_pane_geometries.bind(
        geometry_name,
        real_terminal_driver.wait_for_panes(session_journeys.get(session_name)).geometry,
    )


@when(parsers.parse('I toggle journey session "{session_name}" terminal panes'))
def toggle_terminal_panes(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
) -> None:
    real_terminal_driver.toggle(session_journeys.get(session_name))


@when(parsers.parse('I grow journey session "{session_name}" activity pane by {columns:d} columns'))
def grow_activity_pane(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
    columns: int,
) -> None:
    real_terminal_driver.grow(session_journeys.get(session_name), columns)


@when(parsers.parse('I shrink journey session "{session_name}" activity pane by {columns:d} columns'))
def shrink_activity_pane(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
    columns: int,
) -> None:
    real_terminal_driver.shrink(session_journeys.get(session_name), columns)


@then(parsers.parse(
    'journey session "{session_name}" activity pane is {direction} than "{geometry_name}"'
))
def activity_pane_has_width_change(
    real_terminal_driver: RealTerminalDriver,
    terminal_pane_geometries: References[PaneGeometry],
    session_journeys: SessionJourneys,
    session_name: str,
    direction: str,
    geometry_name: str,
) -> None:
    if direction not in {"wider", "narrower"}:
        raise AssertionError(f"unknown pane width direction {direction!r}")
    real_terminal_driver.wait_for_width_change(
        session_journeys.get(session_name),
        terminal_pane_geometries.get(geometry_name),
        direction,
    )


@when(parsers.parse('I set journey session "{session_name}" activity pane to {percent:d} percent'))
def set_activity_pane_percent(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
    percent: int,
) -> None:
    real_terminal_driver.set_percent(session_journeys.get(session_name), percent)


@when(parsers.parse('I reset journey session "{session_name}" activity pane width'))
def reset_activity_pane_percent(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
) -> None:
    real_terminal_driver.reset(session_journeys.get(session_name))


@then(parsers.parse('journey session "{session_name}" activity pane uses {percent:d} percent'))
def activity_pane_uses_percent(
    real_terminal_driver: RealTerminalDriver,
    session_journeys: SessionJourneys,
    session_name: str,
    percent: int,
) -> None:
    real_terminal_driver.wait_for_percent(session_journeys.get(session_name), percent)


@when(parsers.parse('I remember current terminal focus as "{focus_name}"'))
def remember_current_terminal_focus(
    real_terminal_driver: RealTerminalDriver,
    terminal_focuses: References[TerminalFocus],
    focus_name: str,
) -> None:
    terminal_focuses.bind(focus_name, real_terminal_driver.current_focus())


@then(parsers.parse('current terminal focus remains "{focus_name}"'))
def current_terminal_focus_remains(
    real_terminal_driver: RealTerminalDriver,
    terminal_focuses: References[TerminalFocus],
    focus_name: str,
) -> None:
    real_terminal_driver.assert_focus_preserved(terminal_focuses.get(focus_name))
