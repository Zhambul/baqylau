"""Executable parity checks for the unchanged dashboard interactions."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPOSITORY_ROOT

NODE = shutil.which("node")


def run(script: str, *application_files: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [NODE, f"tests/jsdom/{script}", *application_files],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_account_rows_keep_the_existing_alignment():
    document = run("accounts.js", "dashboard/static/app.01-attention.js")
    assert document["ok"] is True
    assert document["errors"] == []


def test_question_submission_uses_the_canonical_control_without_changing_answers():
    document = run("asksubmit.js", "dashboard/static/app.07-dialogs.js")
    assert document["typed_on_plain_question"]["control_name"] == "answer_question"
    assert document["typed_on_plain_question"]["answers"][2]["other"] == "testing"
    assert document["explicit_chat"]["decision"] == "discuss"


def test_context_bar_and_dictation_interactions_are_unchanged():
    context = run("ctxbar.js", "dashboard/static/app.04-list.js")
    audio = run("dictpcm.js", "dashboard/static/app.07-dialogs.js")
    startup = run(
        "dictstart.js",
        "dashboard/static/app.07-dialogs.js",
        "dashboard/static/app.08-composer.js",
    )
    assert context["ok"] is True
    assert audio["rate"] == 16000
    assert startup["ok"] is True
    assert startup["errors"] == []
    assert startup["a_mint_cwd"] == "/tmp/proj"


def test_composer_keeps_the_session_identity_reader_callable():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.08-composer.js").read_text(
        encoding="utf-8"
    )
    composer = source.split("function buildComposer()", 1)[1].split(
        "/* ---------- jump to a freshly launched session", 1
    )[0]

    assert "const sessionId =" not in composer
    assert "row => sessionId(row)" in composer


def test_dashboard_reload_is_mandatory_when_the_server_build_changes():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.02-router.js").read_text(
        encoding="utf-8"
    )
    stale_branch = source.split("if (boot !== S.boot)", 1)[1].split("\n    }", 1)[0]

    assert "location.reload();" in stale_branch
    assert "toast(" not in stale_branch


def test_composer_uses_the_canonical_send_control_capability():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.05-session.js").read_text(
        encoding="utf-8"
    )

    assert 'send: controls.has("send_text")' in source


def test_file_diff_view_requests_dashboard_html_without_changing_raw_copy():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.05-session.js").read_text(
        encoding="utf-8"
    )

    assert 'node.dataset.summaryKind === "file_edit" ? "diff" : "source"' in source
    assert 'new URLSearchParams({ view, path: node.dataset.filePath })' in source
    assert '? canonicalViewUrl(itemNode)' in source
    assert ': canonicalContentUrl(itemNode.dataset.contentReference)' in source


def test_operation_content_links_reach_the_shared_copy_handler():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.05-session.js").read_text(
        encoding="utf-8"
    )

    assert 'const interactive = event.target.closest("a,button")' in source
    assert (
        'if (actionNode === itemNode && interactive && interactive !== actionNode) return;'
        in source
    )


def test_second_composer_message_reconciles_its_optimistic_bubble():
    document = run("pendingprompt.js", "dashboard/static/app.08-composer.js")

    assert document["afterUnrelated"] == ["say hi", "run ls and say hi again"]
    assert document["remaining"] == ["say hi"]
    assert document["removed"] == ["run ls and say hi again"]


def test_session_navigation_has_no_retired_section_poll_cleanup():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.03-stats.js").read_text(
        encoding="utf-8"
    )

    assert "clearSectionPoll" not in source


def test_session_metadata_failure_does_not_loop_and_close_reconciliation_is_live():
    document = run(
        "sessionlifecycle.js",
        "dashboard/static/app.04-list.js",
        "dashboard/static/app.05-session.js",
    )

    assert document["closeReconciled"] is True
    assert document["metadataFailure"]["sessionId"] == "new-session"
    assert document["retryScheduled"] is False


def test_manifest_request_carries_cloudflare_access_credentials():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/index.html").read_text(
        encoding="utf-8"
    )

    assert 'rel="manifest" href="/static/manifest.webmanifest" ' \
        'crossorigin="use-credentials"' in source


def test_header_interactions_are_unchanged():
    header = run(
        "headeract.js",
        "dashboard/static/app.10-control.js",
        "dashboard/static/app.11-chrome.js",
    )
    assert header["idle"]["⇆ migrate"]["disabled"] is False
    # the ✦ button follows the session's model.changed, not the ctx probe's
    # measurement model, so a terminal `/model opus` shows at once instead of
    # waiting for the next assistant record
    assert header["model"]["switched"]["label"] == "✦ opus ▾"
    assert header["model"]["switched"]["cur"] == "opus"
    assert header["model"]["switched_clears_pending"]["pending"] == ""
    # with no session model yet, the probe is still the fallback
    assert header["model"]["claude"]["label"] == "✦ opus-4.8 ▾"


def test_new_session_sections_task_order_and_density_are_unchanged():
    new_session = run(
        "newsession.js",
        "dashboard/static/app.10-control.js",
        "dashboard/static/app.09-newsession.js",
    )
    sections = run(
        "sections.js",
        "dashboard/static/app.11-chrome.js",
    )
    task_order = run("taskorder.js", "dashboard/static/app.05-session.js")
    view_mode = run("viewmode.js", "dashboard/static/app.05-session.js")
    assert new_session["ok"] is True
    assert new_session["ended_launch_hash"] == "#/s/ended-session"
    assert new_session["ended_launch_event"] == {
        "sessionId": "ended-session",
        "name": "launch.ended",
    }
    assert new_session["ended_launch_toast"] == {
        "kind": "bad",
        "title": "session exited during startup",
    }
    assert sections["ok"] is True
    assert sections["timers"] == {"set": 0, "cleared": 0}
    assert task_order["late"] == ["answer", "task:end"]
    assert task_order["agentIds"] == ["child", "grandchild"]
    assert task_order["copyLinks"] == [
        {"label": "⧉cmd", "reference": "start:operation_command"},
        {"label": "⧉out", "reference": "finish:operation_output"},
    ]
    assert view_mode["default"]["summaries"][0]["text"] == (
        "Read 1 file, ran 1 shell command"
    )
    assert view_mode["focus"]["summary"]["text"] == (
        "Edited 1 file +12 -3, ran 1 agent, ran 1 shell command"
    )
    assert view_mode["focus"]["summary"]["failed"] is True
    assert view_mode["focus"]["open"] == "1"
    assert view_mode["finishedAgent"]["text"] == "Ran 1 agent"
    assert view_mode["blockExpansion"] == {
        "verbose": "1",
        "focus": "0",
        "userStateAfterSwitch": None,
    }
