# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep normal send confirmation local to its session."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from harness.impl.codex.canonical import rollout, source_catalog
from harness.impl.codex.controls import controller_rollout, controller_rollout_modes, controller_values


def test_normal_send_does_not_scan_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Take only the target session's initial position."""
    source = tmp_path / "session.jsonl"
    source.write_text("old input\n")
    catalog = source_catalog.RolloutCatalog(str(tmp_path))
    monkeypatch.setattr(catalog, "paths", Mock(side_effect=AssertionError("Unrelated history was scanned")))
    positions = controller_rollout.source_positions(catalog, str(source), discover=False)
    assert len(positions) == 1
    assert positions[0].position == source.stat().st_size


def test_confirmation_stops_at_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not translate later activity before confirming the prompt."""
    source = tmp_path / "session.jsonl"
    prompt = '{"type":"response_item","payload":{"type":"message","role":"user","content":"Go"}}'
    source.write_text(f"{prompt}\nnot a record\n")
    parse = Mock(wraps=rollout.parse_line)
    monkeypatch.setattr(rollout, "parse_line", parse)
    assert controller_rollout.confirmed_prompt_after(str(source), 0, "Go")
    parse.assert_called_once_with(prompt)


OBJECTIVE = "do all tasks"
GOAL_EVENT = (
    '{"timestamp":"2026-09-14T08:52:42.009Z","type":"event_msg","payload":{"type":"thread_goal_updated",'
    '"threadId":"01a09ebf-67cb-7212-9cd4-a31aab2e4243","goal":{"threadId":"01a09ebf-67cb-7212-9cd4-a31aab2e4243",'
    '"objective":"do all tasks","status":"active","tokensUsed":0,"timeUsedSeconds":0,'
    '"createdAt":1789375962,"updatedAt":1789375962}}}'
)


def test_goal_command_confirms_from_goal_event(tmp_path: Path) -> None:
    """Confirm a /goal command from the goal event Codex writes for it."""
    source = tmp_path / "session.jsonl"
    source.write_text(f"{GOAL_EVENT}\n")
    assert controller_rollout_modes.goal_set_after(str(source), 0, OBJECTIVE)
    assert not controller_rollout_modes.goal_set_after(str(source), 0, "another objective")
    assert not controller_rollout_modes.goal_set_after(str(source), source.stat().st_size, OBJECTIVE)


def test_command_argument_reads_one_command() -> None:
    """Read the argument of one slash command and nothing else."""
    prefix = controller_values.GOAL_COMMAND_PREFIX
    assert controller_rollout.command_argument(f"/goal {OBJECTIVE}", prefix) == OBJECTIVE
    assert controller_rollout.command_argument("/goal   ", prefix) is None
    assert controller_rollout.command_argument(f"/rename {OBJECTIVE}", prefix) is None
    assert controller_rollout.command_argument(OBJECTIVE, prefix) is None
