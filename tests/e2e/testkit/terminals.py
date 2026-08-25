"""Typed observations and gestures for a real session terminal."""

from __future__ import annotations

from dataclasses import dataclass

from sdk.client import BaqylauClient, wait_for
from terminal.contract import TerminalPlugin
from terminal.models.values import (
    ACTIVITY_PANE_TAG,
    SCOREBOARD_PANE_TAG,
    SESSION_WINDOW_TAG,
    WindowInfo,
)
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import SessionJourneyRef


@dataclass(frozen=True)
class SessionPaneSet:
    host: WindowInfo
    activity: WindowInfo
    scoreboard: WindowInfo

    @property
    def geometry(self) -> PaneGeometry:
        return PaneGeometry(
            activity_columns=self.activity.columns,
            total_columns=self.host.columns + self.activity.columns,
        )


@dataclass(frozen=True)
class PaneGeometry:
    activity_columns: int
    total_columns: int

    @property
    def percent(self) -> int:
        return round(100 * self.activity_columns / self.total_columns)


@dataclass(frozen=True)
class TerminalFocus:
    window_id: str
    tab_id: str
    kitty_focused: bool


class RealTerminalDriver:
    """One black-box boundary for a session's real Kitty tab and panes."""

    def __init__(
        self,
        client: BaqylauClient,
        terminal: TerminalPlugin,
        wait_policy: WaitPolicy,
    ) -> None:
        self._client = client
        self._terminal = terminal
        self._wait_policy = wait_policy

    def wait_for_panes(self, journey: SessionJourneyRef) -> SessionPaneSet:
        return wait_for(
            lambda: f"session {journey.session.session_id!r} to own one host, activity pane, and scoreboard",
            lambda: self._pane_set(journey),
            timeout=self._wait_policy.feed,
        )

    def wait_for_no_auxiliary_panes(self, journey: SessionJourneyRef) -> None:
        wait_for(
            lambda: f"session {journey.session.session_id!r} to keep only its host window",
            lambda: True if self._host_only(journey) else None,
            timeout=self._wait_policy.feed,
        )

    def assert_host_window_exists(self, journey: SessionJourneyRef) -> None:
        windows = self._terminal.metadata.windows()
        assert any(
            str(window.window_id) == journey.window_id for window in windows
        ), f"session host window {journey.window_id!r} is not on screen"

    def toggle(self, journey: SessionJourneyRef) -> None:
        outcome = self._client.terminal.toggle_panes(
            window_id=journey.window_id,
            workspace=self._workspace(journey),
        )
        self._assert_outcome("toggle panes", outcome.handled, outcome.succeeded, outcome.reason)

    def grow(self, journey: SessionJourneyRef, columns: int) -> None:
        outcome = self._client.terminal.grow_activity_pane(
            window_id=journey.window_id,
            workspace=self._workspace(journey),
            columns=columns,
        )
        self._assert_outcome("grow activity pane", outcome.handled, outcome.succeeded, outcome.reason)

    def shrink(self, journey: SessionJourneyRef, columns: int) -> None:
        outcome = self._client.terminal.shrink_activity_pane(
            window_id=journey.window_id,
            workspace=self._workspace(journey),
            columns=columns,
        )
        self._assert_outcome("shrink activity pane", outcome.handled, outcome.succeeded, outcome.reason)

    def set_percent(self, journey: SessionJourneyRef, percent: int) -> None:
        outcome = self._client.terminal.set_activity_pane_width(
            window_id=journey.window_id,
            workspace=self._workspace(journey),
            percent=percent,
        )
        self._assert_outcome("set activity pane width", outcome.handled, outcome.succeeded, outcome.reason)

    def reset(self, journey: SessionJourneyRef) -> None:
        outcome = self._client.terminal.reset_activity_pane(
            window_id=journey.window_id,
            workspace=self._workspace(journey),
        )
        self._assert_outcome("reset activity pane width", outcome.handled, outcome.succeeded, outcome.reason)

    def wait_for_width_change(
        self,
        journey: SessionJourneyRef,
        before: PaneGeometry,
        direction: str,
    ) -> PaneGeometry:
        observed: list[PaneGeometry] = []

        def changed() -> PaneGeometry | None:
            found = self._pane_set(journey)
            if found is None:
                return None
            observed[:] = [found.geometry]
            moved = (
                found.activity.columns > before.activity_columns
                if direction == "wider"
                else found.activity.columns < before.activity_columns
            )
            return found.geometry if moved else None

        return wait_for(
            lambda: (
                f"activity pane to become {direction} than {before}; observed "
                f"{observed[-1] if observed else 'no complete pane set'}"
            ),
            changed,
            timeout=self._wait_policy.feed,
        )

    def wait_for_percent(self, journey: SessionJourneyRef, percent: int) -> PaneGeometry:
        return wait_for(
            f"activity pane to have {percent} percent width",
            lambda: self._geometry_with_percent(journey, percent),
            timeout=self._wait_policy.feed,
        )

    def current_focus(self) -> TerminalFocus:
        current = self._terminal.metadata.current_window_id()
        if current is None:
            raise AssertionError("the E2E process has no terminal window")
        window_id = str(current)
        windows = self._terminal.metadata.windows()
        found = next((item for item in windows if str(item.window_id) == window_id), None)
        if found is None:
            raise AssertionError(f"terminal window {window_id!r} is not on screen")
        return TerminalFocus(window_id, str(found.tab_id), found.tab_is_focused)

    def assert_focus_preserved(self, before: TerminalFocus) -> None:
        windows = self._terminal.metadata.windows()
        found = next((item for item in windows if str(item.window_id) == before.window_id), None)
        if found is None:
            raise AssertionError(f"focused terminal window {before.window_id!r} is not on screen")
        focused = tuple(
            item
            for item in windows
            if item.tab_is_focused and item.is_active_in_tab
        )
        if before.kitty_focused:
            assert found.tab_is_focused
            assert found.tab_is_active
            assert found.is_active_in_tab
            assert str(found.tab_id) == before.tab_id
            assert {str(item.window_id) for item in focused} == {before.window_id}
        else:
            assert focused == (), "the dashboard launch raised Kitty from the background"

    def _pane_set(self, journey: SessionJourneyRef) -> SessionPaneSet | None:
        windows = self._terminal.metadata.windows()
        host = next((item for item in windows if str(item.window_id) == journey.window_id), None)
        if host is None:
            return None
        tab = tuple(item for item in windows if item.tab_id == host.tab_id)
        activity = tuple(
            item
            for item in tab
            if item.tags.get(ACTIVITY_PANE_TAG) == journey.session.session_id
        )
        scoreboard = tuple(
            item
            for item in tab
            if item.tags.get(SCOREBOARD_PANE_TAG) == journey.session.session_id
        )
        if (
            len(tab) != 3
            or len(activity) != 1
            or len(scoreboard) != 1
            or not host.is_first_in_tab
            or host.tags.get(SESSION_WINDOW_TAG) != journey.session.session_id
            or not host.processes
            or not activity[0].processes
            or not scoreboard[0].processes
            or scoreboard[0].lines != 5
            or activity[0].columns != scoreboard[0].columns
        ):
            return None
        return SessionPaneSet(host, activity[0], scoreboard[0])

    def _host_only(self, journey: SessionJourneyRef) -> bool:
        windows = self._terminal.metadata.windows()
        host = next((item for item in windows if str(item.window_id) == journey.window_id), None)
        if host is None:
            return False
        tab = tuple(item for item in windows if item.tab_id == host.tab_id)
        return (
            tab == (host,)
            and host.is_first_in_tab
            and host.tags.get(SESSION_WINDOW_TAG) == journey.session.session_id
        )

    def _geometry_with_percent(
        self,
        journey: SessionJourneyRef,
        percent: int,
    ) -> PaneGeometry | None:
        found = self._pane_set(journey)
        if found is None or found.geometry.percent != percent:
            return None
        return found.geometry

    def _workspace(self, journey: SessionJourneyRef) -> str:
        return self._client.sessions.snapshot(journey.session).data.session.working_directory

    @staticmethod
    def _assert_outcome(action: str, handled: bool, succeeded: bool, reason: str | None) -> None:
        if not handled or not succeeded:
            raise AssertionError(
                f"{action} failed: handled={handled}, succeeded={succeeded}, reason={reason!r}"
            )
