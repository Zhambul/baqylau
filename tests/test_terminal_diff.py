from dataclasses import replace

from terminal.panes import views as terminal_views
from domain.events import FileAccessed
from domain.ids import ActorId, CanonicalEventId, OperationId, SessionId
from engine.projections import ActivityContext, FileActivity
from terminal.mirror.blocks import TerminalLine
from terminal.mirror.presenter import TerminalPresenter
from domain.values import TextContent
from terminal.mirror.renderer import TerminalRenderer


PATCH = """--- /work/example.py
+++ /work/example.py
@@ -4,3 +4,3 @@
 def answer():
-    return old_value
+    return new_value
 trailing_call()
"""


def file_activity() -> FileActivity:
    return FileActivity(
        ActivityContext(
            "file:one",
            (CanonicalEventId("file-event"),),
            SessionId("session-one"),
            ActorId("actor-one"),
            "assistant",
            None,
            None,
            1.0,
            2.0,
        ),
        FileAccessed(OperationId("edit-one"), "/work/example.py", "updated", lines_added=1,
                     lines_removed=1, unified_diff=PATCH),
        (),
        "succeeded",
        None,
        CanonicalEventId("file-event"),
        "unified_diff",
    )


def test_terminal_file_click_uses_view_link_and_expands_immutable_diff():
    activity = file_activity()
    collapsed = TerminalPresenter().present(activity)
    collapsed_frame = TerminalRenderer(80)
    collapsed_frame.apply(collapsed)
    assert "baqylau-view://file-event:unified_diff" in collapsed_frame.ansi()

    expanded = TerminalPresenter().present(activity, PATCH)
    rows = expanded.updated_blocks[0].rows
    assert len(rows) == 5
    assert all(isinstance(row, TerminalLine) for row in rows)
    assert not any("---" in part.text or "@@" in part.text for row in rows for part in row.content)
    assert [row.prefix[0].text.strip() for row in rows[1:]] == ["4", "5", "5", "6"]

    renderer = TerminalRenderer(80)
    renderer.apply(expanded)
    frame = renderer.ansi()
    assert "48;2;55;31;36" in frame
    assert "48;2;29;50;38" in frame
    assert "48;2;103;42;50" in frame
    assert "48;2;43;87;58" in frame
    assert "\033[38;2;198;120;221" in frame


def test_terminal_view_state_toggles_without_touching_canonical_content(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    reference = "historic-file-event:unified_diff"

    assert terminal_views.opened() == frozenset()
    assert terminal_views.toggle(reference) is True
    assert terminal_views.opened() == frozenset({reference})
    assert terminal_views.toggle(reference) is False
    assert terminal_views.opened() == frozenset()


def test_terminal_write_view_renders_the_captured_body_not_the_current_file(tmp_path):
    path = tmp_path / "example.py"
    path.write_text("current file\n")
    activity = file_activity()
    activity = replace(
        activity,
        file=FileAccessed(OperationId("write-one"), str(path), "created",
                          content=TextContent("historic = 42\n")),
        content_field="content",
    )

    update = TerminalPresenter().present(activity, "historic = 42\n")
    visible = "".join(
        part.text for row in update.updated_blocks[0].rows
        if isinstance(row, TerminalLine) for part in (*row.prefix, *row.content)
    )

    assert "historic = 42" in visible
    assert "current file" not in visible


def test_terminal_frame_keeps_earlier_rows_for_terminal_scrollback():
    renderer = TerminalRenderer(40, "header")
    renderer.apply(TerminalPresenter().present(file_activity(), PATCH))

    frame = renderer.ansi()
    visible_frame = frame.removeprefix("\033[H\033[2J\033[3J")

    assert visible_frame.count("\n") > 3
    assert "example.py" in visible_frame
    assert "trailing_call" in visible_frame


def test_terminal_frame_stops_at_the_scrollback_row_limit():
    renderer = TerminalRenderer(40, "header", row_limit=4)
    renderer.apply(TerminalPresenter().present(file_activity(), PATCH))

    frame = renderer.ansi().removeprefix("\033[H\033[2J\033[3J")

    assert frame.count("\n") == 3
    assert "trailing_call" in frame
