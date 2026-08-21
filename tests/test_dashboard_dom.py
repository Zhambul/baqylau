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


def test_a_live_session_can_be_typed_to_without_a_terminal_window_id():
    """The composer's gate, over the wire's actual shape.

    `live` MEANS a terminal window is attached — the server resolves it against
    the terminal at read time and never serves the window's own id. A gate that
    also demanded the id left every live session's composer dead ("no terminal
    window — can't message a headless session"), so these fixtures carry no
    window id at all, which is what the browser really receives.
    """
    gate = run(
        "composergate.js",
        "dashboard/static/app.07-dialogs.js",
        "dashboard/static/app.08-composer.js",
    )
    assert gate["errors"] == []
    assert gate["live"]["typable"] is True
    assert gate["live"]["sendable"] is True
    assert gate["live"]["label"] == "send"
    # parked keeps the one door from parked to live...
    assert gate["parked"]["label"] == "resume & send"
    # ...and a host that takes no message says so instead of POSTing a 409
    assert gate["send_capability_off"]["typable"] is False
    # UNRESOLVED liveness is not parked: the door that relaunches a session
    # must not open on uncertainty.
    assert gate["liveness_unknown"]["typable"] is False
    assert gate["liveness_unknown"]["label"] == "send"


def test_liveness_has_one_owner_and_a_refresh_cannot_park_a_running_session():
    """Who decides `live`, and who may not.

    The canonical payload decides it. A payload that does not carry it may not
    write it — `applyCanonicalSnapshotRefresh` rebuilds meta from a synthetic
    {session, actors} with no liveness in it, and a merge that wrote `false`
    there would disable a running session's composer a second after it came up.
    """
    liveness = run("liveness.js", "dashboard/static/app.05-session.js")
    assert liveness["errors"] == []
    assert liveness["live_from_payload"] is True
    assert liveness["parked_from_payload"] is False
    # absent, not false — the merge downstream leaves what it finds
    assert liveness["synthetic_omits_live"] is False
    assert liveness["after_refresh"] is True
    assert liveness["after_refresh_parked"] is False


def test_session_catalog_waits_for_the_harness_capabilities_on_cold_load():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.05-session.js").read_text(
        encoding="utf-8"
    )
    catalog_load = source.split("function loadSessionCatalog", 1)[1]

    host_wait = "loadCanonicalHosts().then(() => catalog)"
    assert host_wait in catalog_load
    assert catalog_load.index(host_wait) < catalog_load.index(
        "applyCanonicalCatalog(data, catalog);"
    )


def test_webpush_subscription_rotates_with_the_server_signing_key():
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.01-attention.js").read_text(
        encoding="utf-8"
    )

    assert "subscribedKey !== cfg.key" in source
    assert "await sub.unsubscribe()" in source
    assert "localStorage.setItem(PUSH_SERVER_KEY_STORAGE, cfg.key)" in source


def test_copying_a_block_reads_the_text_it_already_holds():
    """Content is EMBEDDED in an entry now, so a copy is a local read. The two
    tests this replaced asserted the content ROUTE and its server-rendered diff
    view; both went with the fold that produced them."""
    source = (Path(REPOSITORY_ROOT) / "dashboard/static/app.05-session.js").read_text(
        encoding="utf-8"
    )

    assert 'event.target.closest("[data-copy-block]")' in source
    assert "body.innerText || body.textContent" in source
    assert "/api/content/" not in source


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
    task_order = run(
        "taskorder.js",
        "dashboard/static/app.00a-markup.js",
        "dashboard/static/app.00b-entries.js",
        "dashboard/static/app.05-session.js",
    )
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
    # One copy target, not two: the command and its output are one block's text
    # now that content rides the entry instead of being fetched per reference.
    assert task_order["copyLink"] is True
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


def test_a_finished_blocks_header_click_opens_and_closes_its_body():
    result = run(
        "expand.js",
        "dashboard/static/app.00a-markup.js",
        "dashboard/static/app.00b-entries.js",
        "dashboard/static/app.05-session.js",
    )
    assert result["hasHeader"] is True
    assert result["hasHandler"] is True
    assert result["before"] == "0"
    assert result["afterFirstClick"] == "1"
    assert result["afterSecondClick"] == "0"
    # noise budget: a normal open/close reports nothing to the audit
    assert result["normalClickLoudCalls"] == []


def test_a_throwing_click_reports_the_failure_instead_of_going_dead():
    result = run(
        "expand.js",
        "dashboard/static/app.00a-markup.js",
        "dashboard/static/app.00b-entries.js",
        "dashboard/static/app.05-session.js",
    )
    failure = result["toggleFailure"]
    assert failure is not None
    assert failure["code"] == "feed.block.toggle.fail"
    assert failure["detail"]["entry_id"] == "start"
    assert failure["detail"]["entry_type"] == "shell_started"
    assert "boom" in failure["detail"]["error"]


def test_a_block_missing_its_body_reports_unbound_once_per_entry():
    result = run(
        "expand.js",
        "dashboard/static/app.00a-markup.js",
        "dashboard/static/app.00b-entries.js",
        "dashboard/static/app.05-session.js",
    )
    first = result["unboundFirstPass"]
    assert len(first) == 1
    assert first[0]["code"] == "feed.block.unbound"
    assert first[0]["detail"] == {"entry_id": "finish", "entry_type": "shell_finished"}
    # the same entry checked again, still broken, does not report a second time
    assert result["unboundSecondPass"] == []


def test_the_feed_defaults_to_the_lead_actor_and_a_chosen_scope_overrides_it():
    scope = run("feedscope.js", "dashboard/static/app.05-session.js")
    assert scope["defaultScope"] == "lead"
    assert scope["chosenScope"] == "child"
    assert scope["unknownScope"] == ""
    assert scope["leadInDefaultScope"] is True
    assert scope["childInDefaultScope"] is False
    assert scope["childInChosenScope"] is True
    assert scope["leadInChosenScope"] is False
    assert scope["everythingInUnknownScope"] is True
    assert scope["unknownScopeReportedLoudly"] is True


def test_the_global_stream_opens_only_after_the_list_answers_and_from_its_cursor():
    """The page-load fetch-burst bug: the stream used to open at cursor 0
    alongside the list fetch, and if its first (backlog) frame beat the list's
    reply, every session in it looked unknown to the client and
    `adoptStreamedSession` fired once per session.

    The fix is one shared high-water mark: `connectGlobal` (app.02-router.js)
    now opens the stream only once `/sessionData` has answered, and from the
    cursor that answer reports.
    """
    sequence = run("globalstreamsequence.js", "dashboard/static/app.02-router.js")
    assert sequence["errors"] == []
    assert sequence["fetched_list_first"] is True
    # No stream at all while the list is still in flight — not even at cursor 0.
    assert sequence["stream_before_list_answers"] == 0
    assert sequence["stream_opened_after_list"] == 1
    assert sequence["stream_opened_from_lists_cursor"] is True
    assert sequence["list_rows_applied"] == ["s1"]
