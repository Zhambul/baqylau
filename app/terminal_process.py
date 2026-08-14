#!/usr/bin/env python3
"""Render one canonical session inside its terminal mirror pane."""

from __future__ import annotations

import os
import shutil
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bootstrap import build_default_application
from app import pending_session
from app import terminal_views
from domain.ids import SessionId
from runtime.projections import ActivityScope
from runtime.projections import FileActivity
from terminal.presenter import TerminalPresenter
from terminal.activity import visible
from terminal.renderer import HEADER, TerminalRenderer
from terminal.tab_state import tab_appearance

POLL_SECONDS = 0.25
INITIAL_ACTIVITY_LIMIT = 1000
INITIAL_EVENT_LIMIT = 2000
SCROLLBACK_ROW_LIMIT = 4800


def terminal_size() -> os.terminal_size:
    return shutil.get_terminal_size()


def apply_activity(application, presenter, renderer, file_activities, opened_views,
                   activity) -> None:
    content_reference = None
    if isinstance(activity, FileActivity):
        if activity.content_event_id is not None and activity.content_field is not None:
            content_reference = f"{activity.content_event_id}:{activity.content_field}"
            file_activities[content_reference] = activity
    expanded_content = (
        application.content.resolve(content_reference)
        if content_reference in opened_views else None
    )
    renderer.apply(presenter.present(activity, expanded_content))


def run(session_id: SessionId) -> None:
    sys.stdout.write("\033[H\033[2J\033[3J" + HEADER + "\n")
    sys.stdout.flush()
    application = build_default_application()
    registered_session = application.registry.registered_session(session_id).session
    presenter = TerminalPresenter()
    size = terminal_size()
    renderer = TerminalRenderer(size.columns, HEADER, SCROLLBACK_ROW_LIMIT)
    file_activities = {}
    opened_views = terminal_views.opened()
    cursor = application.event_store.latest_session_cursor(session_id) or 0
    history = application.queries.activity_tail(
        session_id,
        ActivityScope(),
        INITIAL_EVENT_LIMIT,
        INITIAL_ACTIVITY_LIMIT,
        through_cursor=cursor,
    )
    for activity in history.activities:
        if visible(activity, registered_session.lead_actor_id):
            apply_activity(
                application, presenter, renderer, file_activities, opened_views, activity
            )
    painted_tab_state = application.queries.tab_state_tail(
        session_id,
        INITIAL_EVENT_LIMIT,
        cursor,
    )
    if painted_tab_state is not None:
        application.terminal.paint_session_tab(
            session_id,
            tab_appearance(painted_tab_state),
        )
    sys.stdout.write(renderer.ansi())
    sys.stdout.flush()
    while True:
        size = terminal_size()
        resized = size.columns != renderer.width
        if resized:
            renderer.reflow(size.columns)
        current_opened_views = terminal_views.opened()
        views_changed = current_opened_views != opened_views
        if views_changed:
            opened_views = current_opened_views
            for content_reference, activity in file_activities.items():
                expanded_content = (
                    application.content.resolve(content_reference)
                    if content_reference in opened_views else None
                )
                renderer.apply(presenter.present(activity, expanded_content))
        latest_cursor = application.event_store.latest_session_cursor(session_id) or 0
        if latest_cursor <= cursor:
            if resized or views_changed:
                sys.stdout.write(renderer.ansi())
                sys.stdout.flush()
            time.sleep(POLL_SECONDS)
            continue
        page = application.queries.activity_tail(
            session_id,
            ActivityScope(),
            INITIAL_EVENT_LIMIT,
            INITIAL_ACTIVITY_LIMIT,
            through_cursor=latest_cursor,
        )
        current_tab_state = application.queries.tab_state_tail(
            session_id,
            INITIAL_EVENT_LIMIT,
            latest_cursor,
        )
        if current_tab_state != painted_tab_state:
            if current_tab_state is None:
                application.terminal.clear_session_tab(session_id)
            else:
                application.terminal.paint_session_tab(
                    session_id,
                    tab_appearance(current_tab_state),
                )
            painted_tab_state = current_tab_state
        for activity in page.activities:
            if visible(activity, registered_session.lead_actor_id):
                apply_activity(
                    application, presenter, renderer, file_activities, opened_views, activity
                )
        if page.activities or resized or views_changed:
            sys.stdout.write(renderer.ansi())
            sys.stdout.flush()
        cursor = latest_cursor
        time.sleep(POLL_SECONDS)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--pending" and sys.argv[2]:
        sys.stdout.write("\033[H\033[2J\033[3J" + HEADER + "\n")
        sys.stdout.flush()
        run(pending_session.wait(SessionId(sys.argv[2])))
        return
    if len(sys.argv) == 2 and sys.argv[1]:
        run(SessionId(sys.argv[1]))
        return
    raise SystemExit("usage: app/terminal_process.py SESSION_ID | --pending PENDING_ID")


if __name__ == "__main__":
    main()
