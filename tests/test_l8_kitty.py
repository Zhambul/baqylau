# L8 — real-kitty smoke (opt-in).
#
# The ONE test of the actual socket protocol: everything else fakes `kitten`,
# which silently assumes the `@ set-tab-color` / `@ ls` grammar is frozen.
# Run this manually before AND after touching the terminal layer:
#
#     CLAUDE_E2E_KITTY=1 make test-all      (or: pytest -m kitty)
#
# Needs kitty installed; opens a real (briefly visible) kitty OS window.
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import oracle
import payloads as P
from conftest import REPO, wait_until

KITTY = shutil.which("kitty") or "/Applications/kitty.app/Contents/MacOS/kitty"

pytestmark = [
    pytest.mark.kitty,
    pytest.mark.skipif(os.environ.get("CLAUDE_E2E_KITTY") != "1",
                       reason="opt-in: set CLAUDE_E2E_KITTY=1"),
    pytest.mark.skipif(not os.path.exists(KITTY), reason="kitty not installed"),
]

STATES = ["idle", "thinking", "working", "executing", "awaiting-bg",
          "awaiting-command", "awaiting-response"]


@pytest.fixture
def real_kitty(test_env, tmp_path, reaper):
    """A real kitty instance with remote control on a private socket."""
    # /tmp, not tmp_path: unix socket paths cap at ~104 bytes and pytest's
    # tmp dirs blow straight past that (the bind fails silently).
    sock = "/tmp/kitty-e2e-%d.sock" % os.getpid()
    proc = subprocess.Popen(
        [KITTY, "-o", "allow_remote_control=yes",
         "--listen-on", "unix:" + sock, "sh", "-c", "sleep 300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    reaper.append(proc)
    kitten = shutil.which("kitten") or \
        "/Applications/kitty.app/Contents/MacOS/kitten"

    def ls():
        p = subprocess.run([kitten, "@", "--to", "unix:" + sock, "ls"],
                           capture_output=True, text=True, timeout=10)
        return json.loads(p.stdout) if p.returncode == 0 else None

    wait_until(lambda: ls() is not None, timeout=20,
               desc="kitty remote control socket up")
    win_id = str(ls()[0]["tabs"][0]["windows"][0]["id"])
    test_env["KITTY_LISTEN_ON"] = "unix:" + sock
    test_env["KITTY_WINDOW_ID"] = win_id
    test_env.pop("KITTY_KITTEN_BIN", None)          # the REAL kitten this time
    try:
        yield {"sock": sock, "win": win_id, "ls": ls}
    finally:
        proc.terminate()
        try:
            os.remove(sock)
        except OSError:
            pass


def test_color_cycle_against_real_kitty(run_hook, test_env, session, real_kitty):
    """Every state's set-tab-color argv must be ACCEPTED by real kitty
    (rc==0 is the only path that persists the tab row), and clear must too."""
    s = session.make()
    for state in STATES:
        run_hook("claude-tab-status.py", P.base(s, ""), argv=(state,))
        assert oracle.tab_state(test_env, real_kitty["win"]) == state, \
            "real kitten rejected the %s paint (argv drift?)" % state
    run_hook("claude-tab-status.py", P.session_end(s), argv=("clear",))
    assert oracle.tab_state(test_env, real_kitty["win"]) is None


def test_ls_shape_matches_fake(real_kitty):
    """The `@ ls` tree fields the product (and our fake) rely on:
    os_windows -> tabs -> windows with id + user_vars."""
    tree = real_kitty["ls"]()
    w = tree[0]["tabs"][0]["windows"][0]
    assert "id" in w and "user_vars" in w, \
        "kitty @ ls window shape changed: %s" % sorted(w)
