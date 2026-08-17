"""Independent terminal and dashboard presentation over one semantic activity."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import re
from types import MappingProxyType

from terminal.models import RGB
from domain.ids import (
    ActorId,
    CanonicalEventId,
    AssignmentId,
    MessageId,
    OperationId,
    SessionId,
)
from domain.values import (
    AccountReference,
    ExecutionMode,
    MessageRole,
    ModelReference,
    Outcome,
    StructuredContent,
    TextContent,
    TokenUsage,
)
from dashboard.render.items import DashboardPresenter
from engine.projections import (
    ActivityContext,
    ActivityStatistics,
    ActorAssignmentActivity,
    EffortChangeActivity,
    MessageActivity,
    ModelChangeActivity,
    OperationActivity,
    ActorMessageActivity,
    ReasoningActivity,
    SessionSummary,
    UsageSummary,
)
from terminal.mirror.presenter import TerminalPresenter
from terminal.mirror.visibility import visible as terminal_activity_visible
from terminal.mirror.renderer import HEADER, TerminalRenderer
from terminal.scoreboard import ScoreboardPresenter, ScoreboardSnapshot
from terminal.theme import tab_appearance
from terminal.mirror.blocks import TerminalBlock, TerminalLine, TerminalText, TerminalUpdate


GOLDEN_DIRECTORY = Path(__file__).parent / "golden"


def _terminal_cells(frame: str) -> tuple[tuple, ...]:
    """Normalize equivalent SGR ordering and reset boundaries into visible cells."""
    # Two kinds of value share this dict: the four flags are bools, the two
    # colours are None or an (r,g,b) / ("indexed", n) tuple. Inferred from the
    # initialiser alone it reads as bool | None, and every colour written into
    # it below is then a mismatch.
    style: dict[str, object] = {
        "foreground": None,
        "background": None,
        "bold": False,
        "dim": False,
        "italic": False,
        "underline": False,
    }
    cells = []
    position = 0
    escape_pattern = re.compile(r"\x1b\[([0-9;?]*)([ -/]*)([@-~])")
    for match in escape_pattern.finditer(frame):
        for character in frame[position:match.start()]:
            cell_style = tuple(style.values()) if not character.isspace() else (None,) * 6
            cells.append((character, *cell_style))
        position = match.end()
        if match.group(3) != "m":
            continue
        codes = [int(code) if code else 0 for code in match.group(1).split(";")]
        code_index = 0
        while code_index < len(codes):
            code = codes[code_index]
            if code == 0:
                style.update(
                    foreground=None,
                    background=None,
                    bold=False,
                    dim=False,
                    italic=False,
                    underline=False,
                )
            elif code == 1:
                style["bold"] = True
            elif code == 2:
                style["dim"] = True
            elif code == 3:
                style["italic"] = True
            elif code == 4:
                style["underline"] = True
            elif code == 22:
                style["bold"] = style["dim"] = False
            elif code == 23:
                style["italic"] = False
            elif code == 24:
                style["underline"] = False
            elif code in (38, 48):
                field = "foreground" if code == 38 else "background"
                color_mode = codes[code_index + 1]
                if color_mode == 2:
                    style[field] = tuple(codes[code_index + 2:code_index + 5])
                    code_index += 4
                elif color_mode == 5:
                    style[field] = ("indexed", codes[code_index + 2])
                    code_index += 2
            elif code == 39:
                style["foreground"] = None
            elif code == 49:
                style["background"] = None
            code_index += 1
    for character in frame[position:]:
        cell_style = tuple(style.values()) if not character.isspace() else (None,) * 6
        cells.append((character, *cell_style))
    while cells and str(cells[-1][0]).isspace():
        cells.pop()
    return tuple(cells)


def _mirror_operation(
    identifier: str,
    command: str,
    result: str,
    execution: ExecutionMode,
    outcome: Outcome,
    exit_code: int,
    finished_at: float,
) -> OperationActivity:
    context = ActivityContext(
        activity_id=f"operation:{identifier}",
        source_event_ids=(CanonicalEventId(f"event-{identifier}"),),
        session_id=SessionId("session-one"),
        actor_id=ActorId("actor-one"),
        actor_name="assistant",
        parent_actor_id=None,
        turn_id=None,
        started_at=1.0,
        finished_at=finished_at,
    )
    return OperationActivity(
        context=context,
        operation_id=OperationId(identifier),
        category="shell",
        native_name="shell",
        execution=execution,
        arguments=StructuredContent(json.dumps({"command": command})),
        description=None,
        parent_operation_id=None,
        progress=(),
        state="finished",
        outcome=outcome,
        result=TextContent(result),
        exit_code=exit_code,
        content_event_id=CanonicalEventId(f"event-{identifier}"),
        content_field="operation_content",
    )


def operation_activity(*, state="running", outcome=None, result=None):
    context = ActivityContext(
        activity_id="operation:one",
        source_event_ids=(CanonicalEventId("event-one"),),
        session_id=SessionId("session-one"),
        actor_id=ActorId("actor-one"),
        actor_name="assistant",
        parent_actor_id=None,
        turn_id=None,
        started_at=1.0,
        finished_at=2.0 if state == "finished" else None,
    )
    return OperationActivity(
        context=context,
        operation_id=OperationId("one"),
        category="shell",
        native_name="shell",
        execution="foreground",
        arguments=StructuredContent('{"command":"echo <unsafe>"}'),
        description="Run <unsafe>",
        parent_operation_id=None,
        progress=(),
        state=state,
        outcome=outcome,
        result=result,
        exit_code=0 if outcome == "succeeded" else None,
        content_event_id=CanonicalEventId("event-one"),
        content_field="operation_content",
    )


def test_dashboard_and_terminal_present_the_same_fact_without_sharing_presentation_fields():
    activity = operation_activity()
    terminal = TerminalPresenter().present(activity)
    dashboard = DashboardPresenter().present(activity)
    assert terminal.updated_blocks[0].block_id == dashboard.item_id == "operation:one"
    assert any(
        "<unsafe>" in "".join(text.text for text in row.content)
        for row in terminal.updated_blocks[0].rows
        if isinstance(row, TerminalLine)
    )
    assert "Run &lt;unsafe&gt;" in dashboard.html
    assert "rgb" not in dashboard.html.lower()
    assert dashboard.item_type == "operation" and dashboard.state == "running"
    assert dashboard.summary_kind == "shell"


def _config_change_context(activity_id: str) -> ActivityContext:
    return ActivityContext(
        activity_id=activity_id,
        source_event_ids=(CanonicalEventId(activity_id),),
        session_id=SessionId("session-one"),
        actor_id=ActorId("session-one:lead"),
        actor_name=None,
        parent_actor_id=None,
        turn_id=None,
        started_at=None,
        finished_at=10.0,
    )


def test_model_change_presents_the_switch_with_both_values_on_both_surfaces():
    activity = ModelChangeActivity(
        _config_change_context("model_change:one"),
        ModelReference("opus", "opus-5", "opus"),
        ModelReference("sonnet", "sonnet-5", "sonnet"),
        "selected",
    )
    dashboard = DashboardPresenter().present(activity)
    assert dashboard.item_type == "model_changed" and dashboard.summary_kind == "model_changed"
    assert 'data-out="ok"' in dashboard.html
    assert "<strong>opus-5</strong> → <strong>sonnet-5</strong>" in dashboard.html
    terminal = TerminalPresenter().present(activity)
    row_text = "".join(text.text for text in terminal.updated_blocks[0].rows[0].content)
    assert "model opus-5 → sonnet-5" in row_text


def test_effort_change_presents_the_switch_with_both_values_on_both_surfaces():
    activity = EffortChangeActivity(
        _config_change_context("effort_change:one"),
        "high",
        "medium",
        "selected",
    )
    dashboard = DashboardPresenter().present(activity)
    assert dashboard.item_type == "effort_changed" and dashboard.summary_kind == "effort_changed"
    assert 'data-out="ok"' in dashboard.html
    assert "<strong>high</strong> → <strong>medium</strong>" in dashboard.html
    terminal = TerminalPresenter().present(activity)
    row_text = "".join(text.text for text in terminal.updated_blocks[0].rows[0].content)
    assert "effort high → medium" in row_text


def test_terminal_hides_lead_transcript_and_system_messages_but_keeps_subagent_messages():
    lead_actor_id = ActorId("session-one:lead")

    def message(actor_id: ActorId, role: MessageRole) -> MessageActivity:
        return MessageActivity(
            ActivityContext(
                activity_id=f"message:{actor_id}:{role}",
                source_event_ids=(CanonicalEventId(f"event:{actor_id}:{role}"),),
                session_id=SessionId("session-one"),
                actor_id=actor_id,
                actor_name="agent",
                parent_actor_id=None,
                turn_id=None,
                started_at=1.0,
                finished_at=1.0,
            ),
            MessageId(f"message:{actor_id}:{role}"),
            role,
            None,
            None,
            TextContent("message"),
        )

    assert not terminal_activity_visible(message(lead_actor_id, "user"), lead_actor_id)
    assert not terminal_activity_visible(message(lead_actor_id, "assistant"), lead_actor_id)
    assert not terminal_activity_visible(message(lead_actor_id, "system"), lead_actor_id)
    assert terminal_activity_visible(message(ActorId("child-one"), "user"), lead_actor_id)
    assert terminal_activity_visible(message(ActorId("child-one"), "assistant"), lead_actor_id)


def test_operation_finish_replaces_one_terminal_block_and_one_dashboard_item():
    renderer = TerminalRenderer(width=80)
    presenter = TerminalPresenter()
    started = operation_activity()
    finished = operation_activity(state="finished", outcome="succeeded", result=TextContent("passed"))
    renderer.apply(presenter.present(started))
    renderer.apply(presenter.present(finished))
    assert len(renderer.blocks()) == 1
    assert renderer.blocks()[0].block_id == "operation:one"
    assert any(
        text.text == "passed"
        for row in renderer.blocks()[0].rows
        if isinstance(row, TerminalLine)
        for text in row.content
    )
    dashboard = DashboardPresenter().present(finished)
    assert dashboard.item_id == "operation:one"
    assert dashboard.state == "succeeded"
    assert dashboard.plain_text == "passed"


def test_terminal_renderer_emits_the_private_terminal_model_as_ansi():
    renderer = TerminalRenderer(width=80)
    renderer.apply(TerminalPresenter().present(
        operation_activity(state="finished", outcome="succeeded", result=TextContent("done"))
    ))

    frame = renderer.ansi()

    assert frame.startswith("\033[H\033[2J\033[3J")
    assert "▶" in frame
    visible = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", frame)
    assert "echo" in visible and "<unsafe>" in visible
    assert "⧉cmd" in visible
    assert "⧉out" in visible
    assert "baqylau-content://event-one:operation_command" in frame
    assert "baqylau-content://event-one:operation_output" in frame
    assert "\033[38;2;86;182;194" in frame
    assert "DashboardItem" not in frame


def test_canonical_operations_preserve_the_frozen_terminal_surface():
    activities = (
        _mirror_operation(
            "foreground",
            "for i in $(seq 3); do echo line-$i; done",
            "line-1\nline-2\nline-3",
            "foreground",
            "succeeded",
            0,
            2.2,
        ),
        _mirror_operation(
            "background",
            "python3 train.py --epochs 10 --batch-size 32 --learning-rate 0.0003",
            "epoch 1/10 loss 2.31\nepoch 2/10 loss 1.94 \n" + "x" * 80,
            "background",
            "failed",
            1,
            188.0,
        ),
    )
    rendered_line_counts = []
    for width in (60, 100):
        renderer = TerminalRenderer(width, HEADER)
        for activity in activities:
            renderer.apply(TerminalPresenter().present(activity))
        actual = renderer.ansi()
        expected = (GOLDEN_DIRECTORY / f"mirror-w{width}.ansi").read_text().removesuffix(
            "END-OF-CANON\n"
        )
        assert _terminal_cells(actual) == _terminal_cells(expected)
        rendered_line_counts.append(actual.count("\n"))
    assert rendered_line_counts[0] > rendered_line_counts[1]


def test_terminal_renderer_honors_wrap_truncate_and_continuation_prefixes():
    renderer = TerminalRenderer(width=6)
    renderer.apply(
        TerminalUpdate(
            updated_blocks=(
                TerminalBlock(
                    "layout",
                    (
                        TerminalLine(
                            (TerminalText("abcdef"),),
                            prefix=(TerminalText("> "),),
                            continuation_prefix=(TerminalText("  "),),
                        ),
                        TerminalLine(
                            (TerminalText("abcdefgh"),),
                            layout="truncate",
                        ),
                    ),
                ),
            )
        )
    )

    visible = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", renderer.ansi())

    assert visible.splitlines() == ["> abcd", "  ef", "abcdef"]


def test_canonical_tab_states_keep_the_existing_terminal_palette():
    asking = tab_appearance("awaiting_attention")
    done = tab_appearance("awaiting_response")
    executing = tab_appearance("executing")

    assert asking.active_background == RGB(224, 108, 117)
    assert done.active_background == RGB(152, 195, 121)
    assert executing.active_background == RGB(97, 175, 239)


def test_scoreboard_preserves_the_five_row_surface_from_canonical_projections():
    snapshot = ScoreboardSnapshot(
        session=SessionSummary(
            SessionId("session-one"),
            "example",
            None,
            "/work",
            "/work",
            1.0,
            None,
            ActorId("actor-one"),
            None,
            None,
            AccountReference("account-one", "Primary"),
            1,
            None,
            "running",
        ),
        usage=UsageSummary(
            TokenUsage(428_000, 197_000, 55_000_000, 410_000),
            Decimal("1.20"),
            MappingProxyType({}),
            MappingProxyType({}),
        ),
        statistics=ActivityStatistics(
            45,
            5,
            56,
            791,
            29,
            5,
            MappingProxyType({"Read": 34, "Edit": 18, "Write": 4}),
        ),
        active_seconds=4_104,
    )
    renderer = TerminalRenderer(120)

    renderer.apply(ScoreboardPresenter().present(snapshot, 120))
    frame = renderer.ansi()
    visible = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", frame)

    assert frame.count("\n") == 4
    assert "⬡ session-one" in visible and "◈ Primary" in visible
    assert "✉ 5 msgs" in visible
    assert "▪ 45 cmds" in visible and "5✗" in visible and "⏱ 1h08m" in visible
    assert "Σ 56M total" in visible and "≈ $1.20" in visible
    assert "56 files" in visible and "+791" in visible and "Read 34" in visible


def test_dashboard_operation_is_one_complete_quiet_block():
    running = DashboardPresenter().present(operation_activity())
    assert running.html.startswith('<div class="blk"')
    assert 'data-open="0"' in running.html
    assert 'data-quiet="1"' in running.html
    assert 'data-out="run"' in running.html
    assert running.html.count('class="bhead"') == 1
    assert running.html.count('class="bbody"') == 1
    assert 'class="bchips"' in running.html
    assert 'class="bsum"' in running.html
    assert 'class="btail"' in running.html
    assert 'class="blinks"' in running.html
    assert 'class="chip blive" data-anchor="1.0"' in running.html
    assert not hasattr(running, "group_id")

    finished = DashboardPresenter().present(
        operation_activity(state="finished", outcome="succeeded", result=TextContent("passed"))
    )
    assert 'data-out="ok"' in finished.html
    assert '<span class="cqt">finished · 1.0s</span>' in finished.html
    assert "blive" not in finished.html


def test_dashboard_actor_assignment_is_one_complete_note_block():
    activity = ActorAssignmentActivity(
        context=ActivityContext(
            activity_id="actor_assignment:one",
            source_event_ids=(CanonicalEventId("child-event"),),
            session_id=SessionId("session-one"),
            actor_id=ActorId("actor-one"),
            actor_name="assistant",
            parent_actor_id=None,
            turn_id=None,
            started_at=1.0,
            finished_at=3.0,
        ),
        assignment_id=AssignmentId("assignment-one"),
        brief=TextContent("check the implementation"),
        state="finished",
        outcome="succeeded",
        result=TextContent("done"),
        reason=None,
    )

    item = DashboardPresenter().present(activity)

    assert item.html.startswith('<div class="blk"')
    assert 'data-note="1"' in item.html
    assert 'data-out="ok"' in item.html
    assert '<span class="anmark">⏺</span>' in item.html
    assert 'Agent &quot;check the implementation&quot; finished · 2.0s' in item.html
    assert '<div class="md"><p>done</p>' in item.html


def test_dashboard_actor_assignment_launch_card_expands_to_actor_name_and_prompt():
    # The header carries the short label; the expansion carries WHO was
    # launched and the verbatim prompt (when the harness exposes one).
    activity = ActorAssignmentActivity(
        context=ActivityContext(
            activity_id="actor_assignment:one",
            source_event_ids=(CanonicalEventId("child-event"),),
            session_id=SessionId("session-one"),
            actor_id=ActorId("actor-one"),
            actor_name="assistant",
            parent_actor_id=None,
            turn_id=None,
            started_at=1.0,
            finished_at=None,
        ),
        assignment_id=AssignmentId("assignment-one"),
        brief=TextContent("check the implementation"),
        state="running",
        outcome=None,
        result=None,
        reason=None,
        assigned_actor_name="general-purpose",
        prompt=TextContent("Review engine/projections.py for merge bugs.", "text/markdown"),
    )

    item = DashboardPresenter().present(activity)

    assert 'Agent &quot;check the implementation&quot; started' in item.html
    assert "<strong>agent:</strong> general-purpose" in item.html
    assert "Review engine/projections.py for merge bugs." in item.html
    assert item.plain_text == "Review engine/projections.py for merge bugs."


def test_dashboard_skill_is_one_complete_note_block():
    activity = operation_activity(state="finished", outcome="succeeded")
    activity = OperationActivity(
        context=activity.context,
        operation_id=activity.operation_id,
        category="skill",
        native_name="native-skill-tool",
        execution="foreground",
        arguments=TextContent("slack"),
        description=None,
        parent_operation_id=None,
        progress=(),
        state="finished",
        outcome="succeeded",
        result=None,
        exit_code=None,
    )

    item = DashboardPresenter().present(activity)

    assert 'data-note="1"' in item.html
    assert '<span class="anmark">⏺</span>' in item.html
    assert "Skill(slack)" in item.html
    assert "native-skill-tool" not in item.html


def test_dashboard_structured_tool_arguments_have_a_meaningful_one_line_summary():
    activity = operation_activity(state="finished", outcome="succeeded")
    activity = OperationActivity(
        context=activity.context,
        operation_id=activity.operation_id,
        category="search",
        native_name="ToolSearch",
        execution="foreground",
        arguments=StructuredContent('{"query":"select:WebSearch","max_results":1}'),
        description=None,
        parent_operation_id=None,
        progress=activity.progress,
        state=activity.state,
        outcome=activity.outcome,
        result=activity.result,
        exit_code=activity.exit_code,
    )

    item = DashboardPresenter().present(activity)

    assert '<div class="anote"><span class="anmark">⏺</span>' in item.html
    assert '<span class="atext">ToolSearch</span>' in item.html
    assert '<span class="chip">ToolSearch</span>' not in item.html
    assert (
        '<span class="bsum">{&quot;max_results&quot;:1,'
        '&quot;query&quot;:&quot;select:WebSearch&quot;}</span>'
    ) in item.html
    assert '<span class="bsum">{</span>' not in item.html


def test_dashboard_task_tools_use_the_same_dot_language_as_agents():
    activity = operation_activity(state="finished", outcome="succeeded")
    activity = OperationActivity(
        context=activity.context,
        operation_id=activity.operation_id,
        category="workspace",
        native_name="EnterWorktree",
        execution="foreground",
        arguments=TextContent("feature-branch"),
        description=None,
        parent_operation_id=None,
        progress=activity.progress,
        state=activity.state,
        outcome=activity.outcome,
        result=activity.result,
        exit_code=activity.exit_code,
    )

    item = DashboardPresenter().present(activity)

    assert '<div class="anote"><span class="anmark">⏺</span>' in item.html
    assert '<span class="atext">EnterWorktree</span>' in item.html
    assert '<span class="chip">' not in item.html


def test_dashboard_actor_message_is_one_complete_note_block():
    context = ActivityContext(
        activity_id="actor_message:one",
        source_event_ids=(CanonicalEventId("message-event"),),
        session_id=SessionId("session-one"),
        actor_id=ActorId("sender"),
        actor_name="reviewer",
        parent_actor_id=None,
        turn_id=None,
        started_at=1.0,
        finished_at=None,
    )
    activity = ActorMessageActivity(
        context,
        MessageId("message-one"),
        ActorId("recipient"),
        TextContent("The implementation is ready."),
    )

    item = DashboardPresenter().present(activity)

    assert 'data-note="1"' in item.html
    assert "Message reviewer → recipient: The implementation is ready." in item.html
    assert '<div class="md"><p>The implementation is ready.</p>' in item.html
    assert item.message_id == "message-one"


def test_dashboard_reasoning_uses_the_existing_message_surface():
    context = ActivityContext(
        activity_id="reasoning:one",
        source_event_ids=(CanonicalEventId("reasoning-event"),),
        session_id=SessionId("session-one"),
        actor_id=ActorId("assistant-one"),
        actor_name="codex",
        parent_actor_id=None,
        turn_id=None,
        started_at=1.0,
        finished_at=None,
    )
    item = DashboardPresenter().present(
        ReasoningActivity(context, "reasoning-one", TextContent("Consider **this**."), True)
    )

    assert item.item_type == "reasoning"
    assert item.conversation_kind == "message"
    assert item.html.startswith('<div class="msg message">')
    assert '<span class="who">codex</span>' in item.html
    assert '<strong>this</strong>' in item.html
    assert 'class="reasoning"' not in item.html


def test_large_dashboard_content_uses_an_event_derived_reference():
    large_result = TextContent("x" * 5000)
    item = DashboardPresenter().present(
        operation_activity(state="finished", outcome="succeeded", result=large_result)
    )
    assert item.content_reference == "event-one:operation_content"
    assert item.command_reference == "event-one:operation_command"
    assert item.output_reference == "event-one:operation_output"
