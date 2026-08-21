"""One real harness CLI, started the way the product starts it — through the API.

Nothing here is invented and nothing here is private. The launch is
POST /api/sessions, the same request the dashboard's new-session form makes, so
the launcher, the launch plan, the terminal adapter and the window binding are
all the product's own. What differs from a person's run is WHICH terminal: the
daemon is pinned to `terminal/impl/pty`, whose windows are pseudo-terminals
rather than kitty tabs, which is what lets the suite run headless. The CLI still
sees a tty, still runs its full TUI, and still fires its own hooks — into the
test daemon, because the daemon passes its own port down to what it launches.

This file has no screen reads and no keystrokes of its own. It used to have both,
for the workspace-trust gate and the backgrounding chord; the gate is gone (see
the launch step in conftest.py) and the chord is a control gesture now, so
everything this suite does goes through a route the product publishes. What is
left here is the launch and the close.
"""

from __future__ import annotations

import json

from support.daemon import Daemon


class Launched:
    """A harness the daemon started, addressed by the window it runs in.

    The window, not the session: this object exists from the moment of the launch
    and a session does not — the harness chooses its own id and announces it in
    its first evidence.
    """

    def __init__(self, daemon: Daemon, harness: str, window_id: str) -> None:
        self.daemon = daemon
        self.harness = harness
        self.window_id = window_id

    def stop(self, session_id: str | None) -> None:
        """End the session the way a person does: the close-session gesture.

        Through the control plane rather than by killing a window, because the
        harness has work to flush on the way out — its own exit evidence, which
        the next scenario's uninterpreted check is entitled to see. A session that
        never announced itself has nothing to address, and its terminal goes when
        the daemon that owns it does.
        """
        if session_id is None:
            return
        self.daemon.post(
            f"/api/sessions/{session_id}/controls/close-session",
            {"request_id": "e2e-close"},
        )


def launch(
    daemon: Daemon,
    harness: str,
    *,
    workspace: str,
    prompt: str,
    model: str | None,
    effort: str | None,
) -> Launched:
    """Start `harness` in `workspace` with `prompt` as its first turn.

    The launch route answers 202 with the window it opened. It does NOT answer
    with a session id, and that is correct rather than a gap: the harness picks
    its own id and the daemon learns it from the harness's first evidence, which
    is why every scenario discovers its session by watching the list.
    """
    status, body = daemon.post("/api/sessions", {
        "harness": harness,
        "working_directory": workspace,
        "initial_text": prompt,
        "model_id": model,
        "effort": effort,
    })
    assert status == 202, f"the launch was refused: {status} {body[:400]}"
    answer = json.loads(body)
    window_id = answer.get("window_id")
    assert window_id, f"the launch opened no window: {body[:400]}"
    return Launched(daemon, harness, str(window_id))
