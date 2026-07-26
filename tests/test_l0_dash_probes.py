# tests/test_l0_dash_probes.py — L0 dashboard: dictation, the ghost-suggestion probe, single-owner grep tests.
#
# One subject out of the former 8468-line L0 dashboard monolith; the
# shared HTTP/audit helpers live in tests/dashkit.py and the in-process
# server fixture (`dash`) in tests/conftest.py.
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import REPO

if REPO not in sys.path:
    sys.path.insert(0, REPO)

from dashboard import server as DS
from dashboard import suggestion as SUG


# ------------------------------------------------------------------ opshtml
from dashkit import (_get, _get_json, _post)


def test_clipboard_files_filters_to_existing_absolute_paths(tmp_path, monkeypatch):
    # the CLAUDE_DASH_CLIPBOARD_FILES knob replaces the real (macOS-only)
    # pasteboard read, so the whole feature is testable off macOS
    from dashboard import clipboard
    real = tmp_path / "kept.py"
    real.write_text("x")
    monkeypatch.setenv(clipboard.ENV_FILES,
                       ":".join([str(real), str(tmp_path / "gone.py"), "rel.py"]))
    assert clipboard.files() == [str(real)]
    monkeypatch.setenv(clipboard.ENV_FILES, "")
    assert clipboard.files() == []


def test_clipboard_match_requires_the_same_basenames(tmp_path, monkeypatch):
    # the correlation guard: a device whose clipboard is NOT the server's must
    # never be handed a host path it didn't name
    from dashboard import clipboard
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("x")
    b.write_text("x")
    monkeypatch.setenv(clipboard.ENV_FILES, "%s:%s" % (a, b))
    assert clipboard.match(["b.py", "a.py"]) == [str(a), str(b)]   # order-free
    assert clipboard.match(["a.py"]) == []                 # count must agree
    assert clipboard.match(["a.py", "other.py"]) == []     # name must agree
    assert clipboard.match([]) == []


def test_post_clipboard_files_resolves_the_pasted_basename(dash, tmp_path,
                                                           monkeypatch):
    # the whole point of the endpoint: the browser can only report a BASENAME
    # (a pasted zero-byte File carries nothing else), the server answers with
    # the full path off the same pasteboard kitty reads
    from dashboard import clipboard
    f = tmp_path / "__init__.py"
    f.write_text("x")
    monkeypatch.setenv(clipboard.ENV_FILES, str(f))
    code, body = _post(dash + "/api/clipboard/files", {"names": ["__init__.py"]})
    assert code == 200 and json.loads(body)["paths"] == [str(f)]
    # a name the clipboard doesn't hold is a 200 with nothing — an ordinary
    # "the clipboard moved on" outcome, never an error
    code, body = _post(dash + "/api/clipboard/files", {"names": ["other.py"]})
    assert code == 200 and json.loads(body)["paths"] == []
    # a path component in the reported name can't widen the match
    code, body = _post(dash + "/api/clipboard/files",
                       {"names": ["../../etc/__init__.py"]})
    assert code == 200 and json.loads(body)["paths"] == [str(f)]


def test_post_clipboard_files_rejects_bad_names(dash):
    for names in (None, [], "x.py", [1], ["ok.py", 2]):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/clipboard/files", {"names": names})
        assert e.value.code == 400, names


def test_http_dictate_probe_tracks_key_file(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
    assert _get_json(dash + "/api/dictate") == {"available": False}
    (tmp_path / "dg-key").write_text("sekret\n")
    assert _get_json(dash + "/api/dictate") == {"available": True}


def test_post_dictate_token_no_key_is_501(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "absent"))
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dictate/token", {"sample_rate": 48000})
    assert e.value.code == 501


def test_post_dictate_token_rejects_bogus_rates(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
    (tmp_path / "dg-key").write_text("sekret")
    # missing, wrong-typed (incl. bool — a Python int subclass), out-of-range
    for rate in (None, "48000", True, 7999, 500000):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(dash + "/api/dictate/token", {"sample_rate": rate})
        assert e.value.code == 400, rate


def test_post_dictate_token_mints_and_builds_url(dash, tmp_path, monkeypatch):
    # the grant call goes to a fake server (CLAUDE_DICTATE_GRANT_URL — the
    # env-knob convention): assert the on-disk key arrives as Token auth and
    # the response carries the JWT + a fully-assembled listen URL, key-free
    seen = {}

    class Grant(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            body = json.dumps({"access_token": "jwt-abc",
                               "expires_in": 30}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):          # keep pytest output clean
            pass

    gsrv = ThreadingHTTPServer(("127.0.0.1", 0), Grant)
    threading.Thread(target=gsrv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
        monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE",
                           str(tmp_path / "terms"))
        monkeypatch.setenv("CLAUDE_DICTATE_GRANT_URL",
                           "http://127.0.0.1:%d/grant"
                           % gsrv.server_address[1])
        (tmp_path / "dg-key").write_text("sekret\n")
        (tmp_path / "terms").write_text("scorebar\n# a comment\n\ntailer\n")
        code, body = _post(dash + "/api/dictate/token", {"sample_rate": 48000})
        assert code == 200
        out = json.loads(body)
        assert out["token"] == "jwt-abc" and out["expires_in"] == 30
        assert seen["auth"] == "Token sekret"
        url = out["ws_url"]
        assert url.startswith("wss://api.deepgram.com/v1/listen?")
        assert "model=nova-3" in url and "interim_results=true" in url
        assert "encoding=linear16" in url and "sample_rate=48000" in url
        assert "smart_format=true" in url and "channels=1" in url
        assert url.count("keyterm=") == 2
        assert "keyterm=scorebar" in url and "keyterm=tailer" in url
        assert "sekret" not in body        # the key never reaches the page
    finally:
        gsrv.shutdown()
        gsrv.server_close()


def test_dictate_keyterms_project_layering(tmp_path, monkeypatch):
    # The merge structure (no vocabulary policy here): nearest project file →
    # outer project file → the user-global file; every file parses the same
    # (#-comments, blanks); first occurrence wins the dedup; empty cwd (and
    # the endpoint's degraded bad-cwd) = global only.
    from dashboard import dictate
    outer = tmp_path / "proj"
    inner = outer / "sub"
    (outer / ".claude").mkdir(parents=True)
    (inner / ".claude").mkdir(parents=True)
    (outer / ".claude" / "deepgram-keyterms").write_text("alpha\nshared\n")
    (inner / ".claude" / "deepgram-keyterms").write_text(
        "# comment\n\nnearest\nshared\n")
    g = tmp_path / "global-terms"
    g.write_text("shared\nglobaly\n")
    monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE", str(g))
    # pin the user config dir (the walk's tail) into the tmp tree — a real
    # ~/.claude/deepgram-keyterms on the dev machine must not leak in
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "usercfg"))
    terms = dictate.keyterms(str(inner))
    assert terms == ["nearest", "shared", "alpha", "globaly"]
    assert dictate.keyterms("") == ["shared", "globaly"]


def test_dictate_keyterms_cap_prefers_nearest(tmp_path, monkeypatch):
    from dashboard import dictate
    proj = tmp_path / "p"
    (proj / ".claude").mkdir(parents=True)
    (proj / ".claude" / "deepgram-keyterms").write_text(
        "\n".join("near%d" % i for i in range(dictate.KEYTERMS_MAX)))
    g = tmp_path / "g"
    g.write_text("evicted-global")
    monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE", str(g))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "usercfg"))
    terms = dictate.keyterms(str(proj))
    assert len(terms) == dictate.KEYTERMS_MAX
    assert "evicted-global" not in terms      # the FARTHEST layer falls off


def test_dictate_available_degrades_on_bad_key_file(tmp_path, monkeypatch):
    from dashboard import dictate
    # a non-UTF-8 key file raises UnicodeDecodeError (a ValueError) out of
    # _read — available() must degrade to False (feature invisible), never let
    # it escape the probe. A missing file is False; a good file is True.
    keyf = tmp_path / "key"
    keyf.write_bytes(b"\xff\xfe\x00bad")
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(keyf))
    assert dictate.available() is False
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "nope"))
    assert dictate.available() is False
    keyf.write_text("dg-key-123")
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(keyf))
    assert dictate.available() is True


def test_post_dictate_token_cwd_keys_project_vocab(dash, tmp_path, monkeypatch):
    # the endpoint contract: a valid cwd layers project terms ahead of global
    # in the minted ws_url; a bogus/missing cwd degrades to global-only, 200
    class Grant(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.dumps({"access_token": "jwt-x",
                               "expires_in": 30}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    gsrv = ThreadingHTTPServer(("127.0.0.1", 0), Grant)
    threading.Thread(target=gsrv.serve_forever, daemon=True).start()
    try:
        proj = tmp_path / "proj"
        (proj / ".claude").mkdir(parents=True)
        (proj / ".claude" / "deepgram-keyterms").write_text("projterm\n")
        g = tmp_path / "g"
        g.write_text("globalterm\n")
        monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "k"))
        (tmp_path / "k").write_text("sekret")
        monkeypatch.setenv("CLAUDE_DICTATE_KEYTERMS_FILE", str(g))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "usercfg"))
        monkeypatch.setenv("CLAUDE_DICTATE_GRANT_URL",
                           "http://127.0.0.1:%d/" % gsrv.server_address[1])
        code, body = _post(dash + "/api/dictate/token",
                           {"sample_rate": 48000, "cwd": str(proj)})
        url = json.loads(body)["ws_url"]
        assert code == 200
        assert url.index("keyterm=projterm") < url.index("keyterm=globalterm")
        for bad in (str(tmp_path / "nope"), 123, None):
            code, body = _post(dash + "/api/dictate/token",
                               {"sample_rate": 48000, "cwd": bad})
            url = json.loads(body)["ws_url"]
            assert code == 200 and "projterm" not in url \
                and "keyterm=globalterm" in url, bad
    finally:
        gsrv.shutdown()
        gsrv.server_close()


def test_post_dictate_token_grant_failure_is_502(dash, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_DICTATE_KEY_FILE", str(tmp_path / "dg-key"))
    (tmp_path / "dg-key").write_text("sekret")
    # nothing listens here — the grant call fails fast, the page gets a 502
    monkeypatch.setenv("CLAUDE_DICTATE_GRANT_URL", "http://127.0.0.1:9/grant")
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(dash + "/api/dictate/token", {"sample_rate": 48000})
    assert e.value.code == 502


def test_canon_cwd_collapses_symlinked_repo(tmp_path):
    """canon_cwd resolves a symlinked repo path so the list groups one project
    under one entry (the baqylau rename left ~/code/personal/kitty as a symlink
    to .../baqylau; pre-move sessions record the /kitty spelling). Empty stays
    empty — realpath('') would be the dashboard's OWN cwd."""
    real = tmp_path / "baqylau"
    real.mkdir()
    link = tmp_path / "kitty"
    link.symlink_to(real, target_is_directory=True)
    assert DS.canon_cwd(str(link)) == str(real)
    assert DS.canon_cwd(str(link / ".claude" / "worktrees" / "x")) \
        == str(real / ".claude" / "worktrees" / "x")   # nested under the symlink
    assert DS.canon_cwd(str(real)) == str(real)         # already-canonical unchanged
    assert DS.canon_cwd("") == ""                       # never the process cwd


def test_group_dir_resolves_worktree_owner(tmp_path):
    """group_dir — the list's grouping-key resolver — maps a session's cwd to
    the directory it files under: a linked-worktree cwd resolves to its OWNING
    main checkout (so N worktrees of one repo aggregate as one project), a main
    checkout / non-checkout resolves to itself, '' stays ''. File-reads only, no
    dirty subprocess. Fed start_cwd (the frozen original), so a later cd can't
    change it."""
    repo = tmp_path / "repo"
    (repo / ".git" / "worktrees" / "wt1").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    wt = tmp_path / "wt1"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: %s\n" % (repo / ".git" / "worktrees" / "wt1"))
    assert DS.group_dir(str(wt)) == str(repo)           # worktree -> owning checkout
    assert DS.group_dir(str(repo)) == str(repo)         # main checkout -> itself
    plain = tmp_path / "plain"
    assert DS.group_dir(str(plain)) == str(plain)       # non-checkout -> itself
    assert DS.group_dir("") == ""


def test_app_js_groups_and_suggests_through_the_shared_group_key(dash):
    """The list's grouping AND the new-session directory picker must both name a
    session's project directory through the ONE `groupKey(row)` helper. Two
    inline copies of `group_dir || cwd` is how the picker came to suggest raw
    `.claude/worktrees/<name>/` cwds that the list had already folded into their
    main checkout — a static check on the served bundles keeps them in step."""
    code, core = _get(dash + "/static/app.00-core.js")
    assert code == 200 and "function groupKey(" in core
    for part in ("app.04-list.js", "app.09-newsession.js"):
        code, body = _get(dash + "/static/" + part)
        assert code == 200
        assert "groupKey(" in body, part
        # the hand-rolled fallback expression must be gone from both readers
        assert "row.group_dir ||" not in body, part
        assert "map(r => r.cwd)" not in body, part   # the picker's old raw-cwd map
    # and the picker builds its list through the one filter that also drops
    # scratch (`/tmp`) paths — dead test/mktemp dirs nobody can launch into
    code, ns = _get(dash + "/static/app.09-newsession.js")
    assert code == 200
    assert "const NS_SCRATCH = /\\/tmp/;" in ns
    assert "suggest(dir, nsSuggestDirs(S.sessions))" in ns


_RULE = "\x1b[m\x1b[38:2:136:136:136m" + "─" * 100


def _screen(input_line):
    return ("\x1b[m  some prior turn output\n"
            + _RULE + "\n" + input_line + "\n" + _RULE + "\n"
            + "\x1b[m  \x1b[36m[Opus 4.8]\x1b[38:2:153:153:153m │ status line\n")


def test_suggestion_parse_faint_ghost():
    s = _screen("\x1b[m❯\xa0\x1b[22;2mapply the MODULES filesystem-scan fix")
    assert SUG.parse(s) == "apply the MODULES filesystem-scan fix"


def test_suggestion_parse_real_input_is_none():
    # normal-weight text on the input line is the user's own line, not a ghost
    assert SUG.parse(_screen("\x1b[m❯\xa0hello there this is typed")) is None


def test_suggestion_parse_empty_box_is_none():
    assert SUG.parse(_screen("\x1b[m❯\xa0")) is None


def test_suggestion_parse_no_box_is_none():
    assert SUG.parse("just\noutput\nlines") is None
    assert SUG.parse("") is None
    assert SUG.parse(None) is None


def test_suggestion_parse_wrapped_ghost_joins_lines():
    # a long suggestion wraps onto a continuation line inside the box; both
    # faint lines join into one whitespace-normalized string
    s = (_RULE + "\n"
         + "\x1b[m❯\xa0\x1b[22;2mapply the MODULES filesystem-scan fix and\n"
         + "\x1b[m  \x1b[22;2mthen re-run the suite\n"
         + _RULE + "\n")
    assert SUG.parse(s) == "apply the MODULES filesystem-scan fix and then re-run the suite"


# suggestion.typed is the COMPLEMENT of parse: the REAL (non-faint) input-box
# text — the tell that the user is composing a reply AT THE TERMINAL, the signal
# the deferred Telegram alert's 'done' arm suppresses on.
def test_suggestion_typed_real_input():
    assert SUG.typed(_screen("\x1b[m❯\xa0hello there this is typed")) \
        == "hello there this is typed"


def test_suggestion_typed_ghost_only_is_none():
    # a faint ghost suggestion is NOT the user typing — typed() ignores it
    assert SUG.typed(_screen("\x1b[m❯\xa0\x1b[22;2mapply the fix")) is None


def test_suggestion_typed_empty_and_no_box_is_none():
    assert SUG.typed(_screen("\x1b[m❯\xa0")) is None
    assert SUG.typed("just\noutput\nlines") is None
    assert SUG.typed("") is None
    assert SUG.typed(None) is None


def _dash_py(*roots):
    """(relpath, source) for every .py under the given repo-relative roots."""
    for root in roots:
        for dirpath, _dirs, files in os.walk(os.path.join(REPO, root)):
            for f in sorted(files):
                if f.endswith(".py"):
                    p = os.path.join(dirpath, f)
                    with open(p, encoding="utf-8", errors="replace") as fh:
                        yield os.path.relpath(p, REPO), fh.read()


def test_audit_target_triple_has_one_owner():
    """A control-plane handler resolving its own audit `log` — the
    `<row>.get("log") or P.mirror_log(<sid>)` spelling — is drift:
    dashboard/http/base._audit_target owns it. Two sanctioned exceptions, both
    documented in that docstring: get.py's copy/view fetchers need the STRICT
    state_db_for (they branch on its absence), and sse.py resolves the mirror
    KEY for op rendering, not an audit target."""
    hits = [f for f, src in _dash_py("dashboard")
            if 'or P.mirror_log(sid)' in src or 'or P.mirror_log(esid)' in src]
    assert hits == ["dashboard/http/base.py", "dashboard/http/get.py",
                    "dashboard/http/sse.py"], hits


def test_post_registries_hold_functions_from_every_mixin():
    """Both POST tables map to the HANDLER FUNCTIONS, not to method-name
    strings resolved by getattr.

    The strings were what forced all 45 handlers into one class — a table that
    can only name methods of `self` cannot span modules — and that is how the
    control plane reached 2000 lines of twelve unrelated subjects. They also
    turned a typo into a 500 on the one request that happened to hit that row,
    whereas an unresolvable function name is now an ImportError at start-up.

    Every row must also still be reachable on the composed Handler: registering
    `_TypingMixin.post_message` is only correct because Handler inherits it."""
    import types
    tables = {"session": DS.Handler._SESSION_POST, "fixed": DS.Handler._FIXED_POST}
    seen_modules = set()
    for which, table in tables.items():
        for key, fn in table.items():
            assert isinstance(fn, types.FunctionType), (which, key, fn)
            assert getattr(DS.Handler, fn.__name__, None) is fn, (which, key)
            seen_modules.add(fn.__module__)
    # the split is real: the rows come from the per-concern modules, not one file
    assert len(seen_modules) >= 6, sorted(seen_modules)
    assert all(m.startswith("dashboard.http.post.") for m in seen_modules), \
        sorted(seen_modules)
    # arity: session verbs take (self, sid), fixed paths take (self)
    for key, fn in DS.Handler._SESSION_POST.items():
        assert fn.__code__.co_argcount == 2, (key, fn.__code__.co_varnames)
    for key, fn in DS.Handler._FIXED_POST.items():
        assert fn.__code__.co_argcount == 1, (key, fn.__code__.co_varnames)


def test_session_scoped_rejects_resolve_their_target_by_sid():
    """A session-scoped input reject files its row through `_reject_input(...,
    sid=sid)` — never a hand-passed `log=`. Handing it a re-derived key made a
    handler's reject row and its SUCCESS row disagree for a forked sid (see the
    _reject_input docstring) and dropped `path` besides. post_upload is the one
    sanctioned `log=`/`path=` caller: its sid is OPTIONAL, so it resolves the
    target once (global '' when absent) and passes it down.

    Scans the whole control plane: post/ is a PACKAGE (router + one module per
    concern), so a per-file assertion would go blind the moment a handler moves
    between them."""
    src = "\n".join(t for f, t in _dash_py("dashboard/http/post"))
    assert "P.mirror_log(sid)" not in src, "a reject re-deriving the audit key"
    calls = []
    for m in re.finditer(r"_reject_input\(", src):     # balanced-paren scan: the
        i, depth = m.end() - 1, 0                      # args nest 2+ deep
        while i < len(src):
            depth += (src[i] == "(") - (src[i] == ")")
            i += 1
            if not depth:
                break
        calls.append(src[m.start():i])
    by_sid = [c for c in calls if "sid=sid" in c]
    assert len(by_sid) >= 11, "the session-scoped reject sites: %d" % len(by_sid)
    for call in calls:                      # one target channel per call, never both
        assert not ("sid=sid" in call and "log=" in call), call


def test_modal_stash_match_has_one_owner_per_dialog():
    """Refusing a decision meant for a REPLACED modal (the `tool_use_id`
    mismatch) is what keeps an answer from being typed into the dialog that
    took its place — so each dialog kind matches its stash in exactly ONE
    place: `_ask_stash` for the two ask endpoints (answer / ask-draft),
    `_plan_guard` for the two plan ones. The ask side used to hand-roll it at
    both call sites, which is how the two ended up answering a stale card with
    different HTTP bodies. Both guards and all four callers live in
    dashboard/http/post/dialogs.py; the scan covers the whole package so a
    handler moving out of it can't take a third copy along."""
    src = "\n".join(t for f, t in _dash_py("dashboard/http/post"))
    # the mismatch test itself: exactly twice, once per guard (they spell it
    # identically). post_message's `ask_pending(sid) or plan_pending(sid)`
    # asks a DIFFERENT question — "is ANY modal up" (a paste would go into the
    # dialog) — and matches no id, so the stash READS are not what's counted.
    assert src.count('!= (pending.get("tool_use_id")') == 2
    for guard in ("_ask_stash", "_plan_guard"):
        assert src.count("def %s(" % guard) == 1
        assert src.count("self.%s(" % guard) == 2, "the two %s callers" % guard


def test_live_or_parked_state_db_has_one_owner():
    """Choosing between the live state DB and its park is core.sessionapi's
    (state_db_for / session_db) — no dashboard module may re-derive it.
    post/session.py is the one hit: post_migrate's unknown-sid 404 probes BOTH
    files without choosing between them, which is a different question ("did
    this sid ever exist")."""
    hits = [f for f, src in _dash_py("dashboard") if "parked_db" in src]
    assert hits == ["dashboard/http/post/session.py"], hits


def test_session_kv_read_has_one_owner():
    """In the READ model, a per-session kv read goes through
    read/meta.session_kv — a hand-rolled state_db_for + kv_at pair can drift on
    the no-state-DB guard that keeps the probe from CREATING the DB whose
    existence is a liveness signal. (post.py's write handlers legitimately call
    kv_at on an sdb _audit_target already resolved for them.)"""
    hits = [f for f, src in _dash_py("dashboard/read") if "kv_at(" in src]
    assert hits == ["dashboard/read/meta.py"], hits
