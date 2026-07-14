# L1c — the substream Renderer's tool dispatch tables (unit-level).
#
# substream_render.py replaced the old on_tool_use / on_tool_result if/elif
# ladders with two dispatch tables (_USE keyed by tool NAME, _RESULT keyed by
# the pend KIND). These tests pin the registrations — a known tool must map to
# its dedicated handler, an unknown one must fall to the generic branch — so
# adding a tool kind stays a one-registration change and a typo'd key is caught
# here, not by a silently-generic mirror block. substream_render is import-safe
# by design (no argv parsing / META resolution), which is what makes this the
# one unit-importable half of the streamer.
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plugins.claude_code import substream_render as SR


def make_renderer():
    return SR.Renderer(
        log="/tmp/claude-mirror-test.log", agent="a1", label="tester",
        rgb=(1, 2, 3), sub_fg=False,
        op_tag=lambda: "", ctx_tag=lambda: "",
        take_subfg=lambda tid: None,
        spawn_fg_tailer=lambda tid, rec, cmd="": None,
        spawn_tailer=lambda kind, taskid, cmd="", group=None: None)


def test_use_table_registrations():
    U = SR.Renderer._USE
    assert U["Bash"] is SR.Renderer._use_bash
    assert U["Monitor"] is SR.Renderer._use_monitor
    assert U["SendMessage"] is SR.Renderer._use_sendmsg
    assert U["Task"] is SR.Renderer._use_agent
    assert U["Agent"] is SR.Renderer._use_agent
    # Every file tool defers to the result via the shared _use_file handler.
    assert SR.FILE_LABEL, "FILE_LABEL must not be empty"
    for t in SR.FILE_LABEL:
        assert U[t] is SR.Renderer._use_file, t
    # An unknown tool has no entry — on_tool_use falls to the generic branch.
    assert "WebSearch" not in U
    assert "NoSuchTool" not in U


def test_result_table_registrations():
    RT = SR.Renderer._RESULT
    assert RT["file"] is SR.Renderer._res_file
    assert RT["agent"] is SR.Renderer._res_silent
    assert RT["sendmsg"] is SR.Renderer._res_silent
    assert RT["fg-live"] is SR.Renderer._res_fg_live
    assert RT["bg"] is SR.Renderer._res_job
    assert RT["monitor"] is SR.Renderer._res_job
    # fg and other (incl. any unknown kind) fall to the generic body render.
    assert "fg" not in RT and "other" not in RT


def test_unknown_tool_falls_to_generic(monkeypatch):
    emitted, bumps = [], []
    monkeypatch.setattr(SR.O, "emit", lambda log, *ops: emitted.extend(ops))
    monkeypatch.setattr(SR.O, "bump", lambda log, **kw: bumps.append(kw))
    monkeypatch.setattr(SR.O, "new_group", lambda log: "g1")
    r = make_renderer()
    r.on_tool_use({"name": "NoSuchTool", "input": {"query": "hi"}, "id": "t9"})
    assert r.pend["t9"] == ("other", "")            # generic branch took it
    assert r.tool_n == 1
    assert any("NoSuchTool" in str(op) for op in emitted)
    # Its result renders the generic body (no crash, no special handler).
    r.on_tool_result({"tool_use_id": "t9", "content": "hello"})
    assert "t9" not in r.pend
    assert any("hello" in str(op) for op in emitted)
    assert not bumps                                # only fg commands bump here


def test_known_tools_route_to_their_handlers(monkeypatch):
    called = []
    monkeypatch.setattr(SR.O, "emit", lambda log, *ops: None)
    monkeypatch.setattr(SR.O, "new_group", lambda log: "g1")
    r = make_renderer()
    # Dispatch goes through the _USE table (not attribute lookup), so patch the
    # table entries themselves and confirm on_tool_use routes by name.
    for name, handler in (("Bash", "_use_bash"), ("Read", "_use_file"),
                          ("Monitor", "_use_monitor"), ("Task", "_use_agent")):
        monkeypatch.setitem(SR.Renderer._USE, name,
                            lambda self, n, i, t, c, _h=handler: called.append(_h))
        r.on_tool_use({"name": name, "input": {}, "id": "t-" + name})
    assert called == ["_use_bash", "_use_file", "_use_monitor", "_use_agent"]
