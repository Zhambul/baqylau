# L2-adopt — sid-fork session adoption (plugins/claude_code/adopt.py) and
# the daemon-env tab-paint fallback (tabstatus._ensure_win).
#
# Claude Code can FORK the sid mid-flight — on --resume (SessionStart fires
# under the OLD sid while every later event carries a NEW sid with no
# SessionStart of its own) and on BACKGROUNDING a session (it continues under
# the background-job id, no SessionStart at all). The fork's first event must
# ADOPT the predecessor — rename its state DB to the new sid's path (symlinks
# at the old paths), retag the panes, and write the sessions row — or the
# mirror/scorebar/tab all freeze on the old sid while the real session's data
# lands in a DB nothing renders (observed live 2026-07-11, 19a42746→ebcecfcc
# on resume and 12e32815→0ed3231c on backgrounding).
import os

import oracle
import payloads as P

HOOK = "claude-hook.py"

ADOPT_SECTION = "hook traffic under a sid with no sessions row"


def seed_counter(seed, log, key, val):
    seed.py(
        "from core import state as S\n"
        "c = S.connect(%r)\n"
        "c.execute(\"INSERT OR REPLACE INTO counters(key,val) VALUES(?,?)\","
        " (%r, %r))\n"
        "c.commit()" % (log, key, val))


def merged_user_vars(fk):
    uv = {}
    for w in fk.windows():
        uv.update(w.get("user_vars", {}))
    return uv


def test_resume_fork_adopts_predecessor(run_hook, test_env, fake_kitten,
                                        session, seed):
    a = session.make()
    run_hook(HOOK, P.session_start(a, source="resume"))
    assert os.path.isfile(a.state_db)
    seed_counter(seed, a.log, "cost", 1.5)   # pre-fork history to carry over

    # The fork: a new sid in the same cwd, no SessionStart of its own.
    b = session.make()
    run_hook(HOOK, P.post_file(b, tool="Edit"))

    # The predecessor's DB now lives at the NEW sid's path; the old path is a
    # symlink so old-key pollers (scorebar liveness, the renderer's reopen)
    # keep resolving to the adopted DB.
    assert os.path.isfile(b.state_db) and not os.path.islink(b.state_db)
    assert os.path.islink(a.state_db)
    assert b.counters().get("cost") == 1.5           # history carried over
    assert b.counters().get("tool:Edit") == 1        # the event itself landed

    # Panes retagged to the sid every future event carries.
    uv = merged_user_vars(fake_kitten)
    assert uv.get("claude_session") == b.sid
    assert uv.get("claude_mirror") == b.sid
    assert uv.get("claude_scorebar") == b.sid

    # Audit trail: the adopt state row, the dispatcher's decision row, and the
    # sessions row the fork's missing SessionStart never wrote.
    assert any(r[1] == "adopt" for r in oracle.state_files(test_env, b.sid))
    assert any(d.startswith("adopt:") for d in
               oracle.decisions(test_env, b.sid, "claude-hook.py"))
    assert oracle.q(test_env, "SELECT 1 FROM sessions WHERE session_id=?",
                    (b.sid,))
    counts = oracle.anomaly_counts(test_env, b.sid)
    hits = [n for t, n in counts.items() if ADOPT_SECTION in t]
    assert hits == [0]


def test_adoption_is_take_once(run_hook, test_env, fake_kitten, session):
    a = session.make()
    run_hook(HOOK, P.session_start(a, source="resume"))
    b = session.make()
    run_hook(HOOK, P.post_file(b, tool="Edit"))
    run_hook(HOOK, P.post_file(b, tool="Edit", path=os.path.join(b.cwd, "x.py")))
    # One adopt row, not one per event — the note is consumed by the winner.
    adopts = [r for r in oracle.state_files(test_env, b.sid) if r[1] == "adopt"]
    assert len(adopts) == 1


def test_background_fork_after_plain_startup_adopts(run_hook, test_env,
                                                    fake_kitten, session):
    # BACKGROUNDING a session forks the sid exactly like --resume does (the
    # conversation continues under the background-job id, no SessionStart), so
    # the note is written for EVERY hosted start, not just resumes.
    a = session.make()
    run_hook(HOOK, P.session_start(a, source="startup"))
    b = session.make()
    run_hook(HOOK, P.post_file(b, tool="Edit"))
    assert os.path.islink(a.state_db)
    assert any(r[1] == "adopt" for r in oracle.state_files(test_env, b.sid))


def test_no_adoption_without_any_hosted_start(run_hook, test_env, fake_kitten,
                                              session):
    # No hosted SessionStart in this cwd → no note → an unknown sid is just a
    # session we don't manage; it accrues its own fresh DB, nothing is captured.
    b = session.make()
    run_hook(HOOK, P.post_file(b, tool="Edit"))
    assert not any(r[1] == "adopt" for r in oracle.state_files(test_env, b.sid))


def test_own_sessionstart_blocks_adoption(run_hook, test_env, fake_kitten,
                                          session):
    a = session.make()
    run_hook(HOOK, P.session_start(a, source="resume"))
    # A headless/daemon session: SessionStart fires (registering the sid) but
    # with no window anywhere the pane lifecycle is skipped — no state DB.
    b = session.make()
    envb = dict(test_env)
    envb.pop("KITTY_WINDOW_ID", None)
    run_hook(HOOK, P.session_start(b, source="startup"), env=envb)
    assert not os.path.exists(b.state_db)
    # Its events must NOT capture the resumed session: the sid had its own
    # SessionStart, so it is a genuinely new session, not a fork.
    run_hook(HOOK, P.post_file(b, tool="Edit"), env=envb)
    assert not os.path.islink(a.state_db)
    assert not any(r[1] == "adopt" for r in oracle.state_files(test_env, b.sid))


def test_instructionsloaded_before_own_sessionstart_blocks_adoption(
        run_hook, test_env, fake_kitten, session):
    # A real NEW session's InstructionsLoaded fires ~100ms BEFORE its own
    # SessionStart. If a CONCURRENT independent session shares the cwd (its
    # adopt_pending note is live), that pre-SessionStart event must NOT consume
    # the note and steal the other session's panes (live bug 2026-07-13:
    # 507fc4c8's InstructionsLoaded adopted the unrelated live db081e65 —
    # toggling 507's mirror then toggled db081e65's). InstructionsLoaded, which a
    # fork never emits, marks the sid so sid_seen blocks the adoption.
    a = session.make()
    run_hook(HOOK, P.session_start(a, source="startup"))   # live, leaves a note
    b = session.make()
    run_hook(HOOK, P.base(b, "InstructionsLoaded"))         # b's true first event
    assert not os.path.islink(a.state_db)                   # a's DB untouched
    assert not any(r[1] == "adopt" for r in oracle.state_files(test_env, b.sid))
    # b then starts normally; still no adoption of the concurrent session.
    run_hook(HOOK, P.session_start(b, source="startup"))
    run_hook(HOOK, P.post_file(b, tool="Edit"))
    assert not os.path.islink(a.state_db)
    assert not any(r[1] == "adopt" for r in oracle.state_files(test_env, b.sid))


def test_adoption_swap_old_path_never_absent(tmp_path, monkeypatch):
    # The move must never leave the OLD path absent, even for an instant: an
    # old-key poller samples parked() (a bare os.path.exists) at any time, and
    # a single False sample makes it conclude SessionEnd and exit permanently
    # (frozen scoreboard); an old-key writer connecting in the gap creates a
    # fresh orphan DB. The old replace-then-symlink pair had exactly that
    # window. Step through the new hardlink + rename-over-tmp-symlink sequence
    # in-process, asserting old_db resolves at EVERY syscall boundary.
    monkeypatch.setenv("CLAUDE_AUDIT", "0")
    from plugins.claude_code import adopt
    src = str(tmp_path / "old.db")
    dst = str(tmp_path / "new.db")
    with open(src, "w") as f:
        f.write("history")
    samples = []
    real = {n: getattr(os, n) for n in ("link", "symlink", "rename", "remove")}

    def wrap(name):
        def stepped(*a, **k):
            samples.append((name, "pre", os.path.exists(src)))
            r = real[name](*a, **k)
            samples.append((name, "post", os.path.exists(src)))
            return r
        return stepped
    for n in real:
        monkeypatch.setattr(os, n, wrap(n))

    os.link(src, dst)                        # step 1, as _maybe_adopt performs it
    adopt._swap_in_symlink("sid", src, dst)  # step 2, the atomic symlink swap

    absent = [s for s in samples if not s[2]]
    assert not absent, "old path vanished at: %r" % absent
    # End state identical to the pre-fix sequence: real file at the new path,
    # symlink at the old, same inode, no tmp leftover.
    assert os.path.isfile(dst) and not os.path.islink(dst)
    assert os.path.islink(src) and os.path.samefile(src, dst)
    assert open(src).read() == "history"
    assert not os.path.lexists(src + adopt._TMP_SYMLINK_SUF)


def test_adoption_swap_cleans_tmp_on_failure(tmp_path, monkeypatch):
    # A failed rename must not strand the .adopt-tmp scratch symlink (or the
    # original file): the swap removes it and re-raises for the caller's audit.
    monkeypatch.setenv("CLAUDE_AUDIT", "0")
    from plugins.claude_code import adopt
    src = str(tmp_path / "old.db")
    dst = str(tmp_path / "new.db")
    with open(src, "w") as f:
        f.write("history")
    os.link(src, dst)

    def boom(*a, **k):
        raise OSError("rename failed")
    monkeypatch.setattr(os, "rename", boom)
    raised = False
    try:
        adopt._swap_in_symlink("sid", src, dst)
    except OSError:
        raised = True
    assert raised, "expected OSError to propagate"
    assert not os.path.lexists(src + adopt._TMP_SYMLINK_SUF)
    assert os.path.isfile(src) and not os.path.islink(src)  # original intact


def test_tab_paint_without_window_env(run_hook, test_env, fake_kitten, session):
    # A daemon-origin hook process has no KITTY_WINDOW_ID: tabstatus must fall
    # back to the claude_session-tagged window instead of bailing "not inside
    # kitty" (the frozen-tab half of the resume-fork bug).
    a = session.make()
    run_hook(HOOK, P.session_start(a))       # tags claude_session on the pane
    envn = dict(test_env)
    envn.pop("KITTY_WINDOW_ID", None)
    fake_kitten.clear()
    run_hook(HOOK, P.base(a, "Stop"), env=envn)
    assert fake_kitten.calls("set-tab-color")
    assert oracle.tab_state(test_env, fake_kitten.window_id) == "awaiting-response"
    assert any(t[2] == "awaiting-response" and t[3] == 1
               for t in oracle.transitions(test_env, a.sid))


def test_partial_adoption_leaves_error_rows(run_hook, test_env, fake_kitten,
                                            session):
    # A botched half-adoption (some sidecars moved, some didn't) must leave
    # errors rows — a silent partial adoption was undebuggable after the fact.
    # Force it: the predecessor has a -wal sidecar, and a non-empty DIRECTORY
    # squats at the new sid's -wal path, so os.replace fails; the old -wal then
    # still exists, so the old-path symlink fails too.
    a = session.make()
    run_hook(HOOK, P.session_start(a, source="resume"))
    with open(a.state_db + "-wal", "w") as f:
        f.write("x")
    b = session.make()
    os.makedirs(os.path.join(b.state_db + "-wal", "occupied"))
    run_hook(HOOK, P.post_file(b, tool="Edit"))

    # The main DB still adopted; only the -wal half failed.
    assert os.path.isfile(b.state_db) and not os.path.islink(b.state_db)
    adopts = [r for r in oracle.state_files(test_env, b.sid) if r[1] == "adopt"]
    assert adopts and '"db"' in adopts[0][2] and '"-wal"' not in adopts[0][2]

    errs = oracle.errors(test_env, b.sid)
    funcs = [e[2] for e in errs]
    assert "adopt: move state db" in funcs
    assert "adopt: symlink old path" in funcs
    move = next(e for e in errs if e[2] == "adopt: move state db")
    assert a.sid in (move[3] or "") and "-wal" in (move[3] or "")
