"""Server-side pane presentation behind the daemon's SSE endpoints.

The pane processes used to build the whole application graph each and render
straight off the store; they are thin byte-copying clients now, and the
presentation runs here, once, in the daemon:

- The MIRROR keeps ONE shared block model per session. The block model is
  width-independent (wrapping happens at render time), so any number of client
  connections share it and each renders at its own width; whichever connection
  polls first advances the model under its lock — a single writer at a time,
  with no feeder thread to manage. Tab painting rides the same advance, so a
  session's tab is painted exactly once per state change no matter how many
  clients watch.
- The SCOREBOARD is five rows rebuilt from projections every frame, so each
  connection just keeps its own renderer.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from app import terminal_views
from domain.ids import SessionId
from runtime.projections import ActivityScope, FileActivity
from terminal.mirror.visibility import visible
from terminal.mirror.presenter import TerminalPresenter
from terminal.mirror.renderer import HEADER, TerminalRenderer
from terminal.scoreboard import ScoreboardPresenter, ScoreboardSnapshot
from terminal.adapter import TerminalAdapter
from terminal.theme import tab_appearance

EVENT_LIMIT = 2000
ACTIVITY_LIMIT = 1000
SCROLLBACK_ROW_LIMIT = 4800
# A model whose last client disconnected stays warm for quick reconnects
# (a pane resize is a reconnect); one nobody polls is dropped after this.
MODEL_IDLE_SECONDS = 600.0
# The block model is width-independent; renders reflow to the client's width,
# so the width a renderer is constructed with never reaches a client.
INITIAL_MODEL_WIDTH = 80


@dataclass
class _MirrorModel:
    lead_actor_id: object
    presenter: TerminalPresenter = field(default_factory=TerminalPresenter)
    renderer: TerminalRenderer = field(
        default_factory=lambda: TerminalRenderer(
            INITIAL_MODEL_WIDTH, HEADER, SCROLLBACK_ROW_LIMIT
        )
    )
    lock: threading.Lock = field(default_factory=threading.Lock)
    file_activities: dict = field(default_factory=dict)
    opened_views: frozenset = field(default_factory=frozenset)
    cursor: int | None = None
    version: int = 0
    painted_tab_state: object = None
    polled_at: float = field(default_factory=time.monotonic)


class PaneStreamService:
    def __init__(self, canonical_store, queries, sessions, content, terminal: TerminalAdapter) -> None:
        self._canonical_store = canonical_store
        self._queries = queries
        self._sessions = sessions
        self._content = content
        self._terminal = terminal
        self._models: dict[SessionId, _MirrorModel] = {}
        self._models_lock = threading.Lock()

    # -- mirror ----------------------------------------------------------------

    def mirror_frame(
        self,
        session_id: SessionId,
        width: int,
        rendered_version: int | None,
    ) -> tuple[int, str] | None:
        """Advance the session's shared model and render it at `width` when the
        caller has not yet seen the current version; None when it has."""
        model = self._model(session_id)
        with model.lock:
            model.polled_at = time.monotonic()
            self._advance(session_id, model)
            if rendered_version is not None and model.version == rendered_version:
                return None
            model.renderer.reflow(width)
            return model.version, model.renderer.ansi()

    def _model(self, session_id: SessionId) -> _MirrorModel:
        with self._models_lock:
            now = time.monotonic()
            for stale_id in [
                model_id
                for model_id, model in self._models.items()
                if model_id != session_id and now - model.polled_at > MODEL_IDLE_SECONDS
            ]:
                del self._models[stale_id]
            model = self._models.get(session_id)
            if model is None:
                session = self._sessions.find_by_id(session_id)
                if session is None:
                    raise KeyError(str(session_id))
                model = _MirrorModel(session.lead_actor_id)
                self._models[session_id] = model
            return model

    def _advance(self, session_id: SessionId, model: _MirrorModel) -> None:
        changed = False
        latest_cursor = self._canonical_store.latest_session_cursor(session_id) or 0
        if model.cursor is None or latest_cursor > model.cursor:
            page = self._queries.activity_tail(
                session_id,
                ActivityScope(),
                EVENT_LIMIT,
                ACTIVITY_LIMIT,
                through_cursor=latest_cursor,
            )
            for activity in page.activities:
                if visible(activity, model.lead_actor_id):
                    self._apply_activity(model, activity)
            current_tab_state = self._queries.tab_state_tail(
                session_id,
                EVENT_LIMIT,
                latest_cursor,
            )
            if current_tab_state != model.painted_tab_state:
                if current_tab_state is None:
                    self._terminal.clear_session_tab(session_id)
                else:
                    self._terminal.paint_session_tab(
                        session_id,
                        tab_appearance(current_tab_state),
                    )
                model.painted_tab_state = current_tab_state
            model.cursor = latest_cursor
            changed = True
        current_opened_views = terminal_views.opened()
        if current_opened_views != model.opened_views:
            model.opened_views = current_opened_views
            for content_reference, activity in model.file_activities.items():
                expanded_content = (
                    self._content.resolve(content_reference)
                    if content_reference in model.opened_views
                    else None
                )
                model.renderer.apply(model.presenter.present(activity, expanded_content))
            changed = True
        if changed:
            model.version += 1

    def _apply_activity(self, model: _MirrorModel, activity) -> None:
        content_reference = None
        if isinstance(activity, FileActivity):
            if activity.content_event_id is not None and activity.content_field is not None:
                content_reference = f"{activity.content_event_id}:{activity.content_field}"
                model.file_activities[content_reference] = activity
        expanded_content = (
            self._content.resolve(content_reference)
            if content_reference in model.opened_views
            else None
        )
        model.renderer.apply(model.presenter.present(activity, expanded_content))

    # -- scoreboard ------------------------------------------------------------

    def scoreboard_stream(self, session_id: SessionId, width: int) -> "ScoreboardStream":
        return ScoreboardStream(self._canonical_store, self._queries, session_id, width)


class ScoreboardStream:
    """Per-connection scoreboard state: a frame per cursor change, per second
    (the active-time clock ticks), and only then."""

    def __init__(self, canonical_store, queries, session_id: SessionId, width: int) -> None:
        self._canonical_store = canonical_store
        self._queries = queries
        self._session_id = session_id
        self._presenter = ScoreboardPresenter()
        self._renderer = TerminalRenderer(width)
        self._rendered_cursor: int | None = None
        self._rendered_second: int | None = None
        self._summary = None
        self._usage = None
        self._statistics = None
        self._active_seconds = 0.0
        self._measured_at = time.time()
        self._active = False

    def frame(self) -> str | None:
        cursor = self._canonical_store.latest_session_cursor(self._session_id) or 0
        current_time = time.time()
        current_second = int(current_time)
        if cursor == self._rendered_cursor and current_second == self._rendered_second:
            return None
        if cursor != self._rendered_cursor:
            self._summary = self._queries.summary(self._session_id, cursor)
            if self._summary is None:
                raise RuntimeError(
                    f"session {self._session_id} has no canonical start event"
                )
            self._usage = self._queries.usage(self._session_id, cursor)
            self._statistics = self._queries.statistics(
                self._session_id,
                ActivityScope(),
                cursor,
            )
            self._active_seconds = self._queries.active_seconds(
                self._session_id,
                current_time,
                cursor,
            )
            tab_state = self._queries.tab_state(self._session_id, cursor)
            self._active = tab_state not in (None, "idle")
            self._measured_at = current_time
        assert self._summary is not None and self._usage is not None
        assert self._statistics is not None
        snapshot = ScoreboardSnapshot(
            session=self._summary,
            usage=self._usage,
            statistics=self._statistics,
            active_seconds=(
                self._active_seconds + current_time - self._measured_at
                if self._active
                else self._active_seconds
            ),
        )
        self._renderer.apply(self._presenter.present(snapshot, self._renderer.width))
        self._rendered_cursor = cursor
        self._rendered_second = current_second
        return self._renderer.ansi()
