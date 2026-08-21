"""The rules that make `client/` a boundary rather than a directory.

Nine programs run outside the daemon: two panes, three terminal handlers, two
hooks, the OTLP receiver and the status-line shim. Every one of them used to live
inside the application it POSTs to — importing 61 to 76 project modules and
counting directory levels to find the repository first — and the whole class of
failure that produced is what these tests exist to make impossible:

  * measured 2026-08-17: a refactor moved the pane processes one level deeper and
    left their depth count alone. It resolved to `terminal/` instead of the root,
    so every pane died on its first import. kitty had already made the window, so
    the terminal reported success; nothing in the suite noticed, because every
    test read those files instead of running them.
  * measured the same day: a hook process cost 134 ms against a 23 ms
    interpreter floor, and its failure path cost another 122 ms, because
    recording an audit row pulled in `repository/impl/sqlite`.

So: a client is one file in one directory, it imports the standard library and
its siblings, it never walks up, it records nothing, and the suite RUNS it.
"""

from __future__ import annotations

import ast
import contextlib
import gzip
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest import REPOSITORY_ROOT
from api.hooks import routes as hook_routes
from api.telemetry import harness as telemetry_routes
from api.sessiondata import routes as session_data_routes, streams as session_data_streams
from api.controls import routes as control_routes
from api.terminal import panes as pane_routes
from core import clients
from core.daemon import contract
from harness.hooks import headers
from harness.impl.claude_code import account, launcher as claude_launcher
from harness.impl.claude_code.otel import launch as otel_launch
from harness.impl.claude_code.usage import live as claude_live_usage
from harness.models import TELEMETRY_KIND_HEADER
from terminal import adapter as terminal_adapter
from terminal.impl.kitty import remote as kitty_remote
from terminal.impl.pty import plugin as pty_plugin

ROOT = Path(REPOSITORY_ROOT)
CLIENT = ROOT / "client"
SHARED = ("_http.py", "_daemon.py", "_model.py", "_render.py", "_handoff.py")

# The inventory, held HERE rather than in the product: six of these files are
# named only by configuration we do not own, and the two we launch are named by
# the launcher that runs them (a shared package may not name a concrete harness
# or terminal, which is the rule that keeps `core/` free of these words). The
# suite is the one place that has to know all eight — which is what makes this
# list the thing that fails when a ninth is added and forgotten.
CLAUDE_HOOK = "claude_hook.py"
CLAUDE_STATUSLINE = "claude_statusline.py"
CLAUDE_OTEL = "claude_otel.py"
CODEX_HOOK = "codex_hook.py"
TERMINAL_PANE = "terminal_pane.py"
TERMINAL_KEYS = "terminal_keys.py"
TERMINAL_VIEW = "terminal_view.py"
TERMINAL_CONTENT = "terminal_content.py"
PUBLISHED = (CLAUDE_HOOK, CLAUDE_STATUSLINE, CODEX_HOOK,
             TERMINAL_KEYS, TERMINAL_VIEW, TERMINAL_CONTENT)
LAUNCHED = (CLAUDE_OTEL, TERMINAL_PANE)

# The one path expression a client may contain (R3): its own directory, which no
# move can invalidate. `realpath`, not `abspath`, so it is still its own directory
# when the file is reached through a symlink — which is how the six paths that
# were published before this landed kept delivering during the migration.
OWN_DIRECTORY = "os.path.dirname(os.path.realpath(__file__))"
# The two entries a human types in a shell, inside the repository, which have to
# put the root on sys.path to build the application at all. They fail loudly, at
# your own terminal, on the first line.
ROOT_ANCHORS = {"bin/baqylau-dashboard.py", "bin/baqylau-raw-events-audit.py"}

OUR_PACKAGES = (
    "api", "app", "core", "dashboard", "audit", "domain",
    "engine", "harness", "notify", "repository", "terminal",
)


def imported_names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


# --- the shape of the directory --------------------------------------------


def test_a_client_is_one_file_in_the_client_directory():
    """One program, one file, and no package (R1).

    A subdirectory per client would need a path expression naming its PARENT to
    reach a shared module — the walk this layout exists to delete — and a
    wrapper/implementation pair (which is what the published paths used to be)
    puts an import failure in the one place nothing can report it: before the
    handler that would have reported it was loaded.
    """
    assert not (CLIENT / "__init__.py").exists(), "client/ is not importable BY us"
    assert [entry.name for entry in CLIENT.iterdir()
            if entry.is_dir() and entry.name != "__pycache__"] == []
    files = {path.name for path in CLIENT.glob("*.py")}
    assert {name for name in files if name.startswith("_")} == set(SHARED)
    # Exhaustive both ways: a client nothing declares, or a declaration with no
    # file, is a client nobody launches.
    assert files - set(SHARED) == set(PUBLISHED) | set(LAUNCHED)


def test_clients_import_only_the_standard_library_and_their_siblings():
    """R2 — the rule the other rules are for.

    What crosses this boundary is a URL, seven header names, a port and the
    process's own stdin. Importing the application to obtain them cost 111 ms per
    hook process on top of the interpreter floor (`bin/retarget-python.py` exists
    because ~140 ms of per-hook overhead was already intolerable), and coupled
    every hook delivery to every import under `harness/impl/`.
    """
    siblings = {path.stem for path in CLIENT.glob("*.py")}
    violations = []
    for path in sorted(CLIENT.glob("*.py")):
        for imported in imported_names(path):
            root = imported.split(".")[0]
            if root in sys.stdlib_module_names or root in siblings:
                continue
            violations.append(f"{path.name} imports {imported}")
    assert violations == []


def test_nothing_but_two_dev_entries_walks_up_to_the_repository_root():
    """R3 — replaces the old rule, which counted the levels and required the
    anchor to name that many.

    That rule was the best available check on a design where 16 files each held a
    depth count, and it still could not have stopped the outage it was written
    for: a count is only wrong AFTER the move, the wrong count still resolves,
    and the failure lands in a process nobody is watching. A file may now name
    nothing but its own directory.
    """
    violations = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in ROOT_ANCHORS or relative.startswith("tests/"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call) or ast.unparse(node.func) != "sys.path.insert":
                continue
            # Read STRUCTURALLY, on the AST: a text match is satisfied by the
            # comment explaining the rule, which is what happened to the first
            # version of the rule this replaces.
            anchor = ast.unparse(node.args[1])
            if anchor != OWN_DIRECTORY:
                violations.append(f"{relative} anchors on {anchor}")
    assert violations == []
    assert all((ROOT / name).is_file() for name in ROOT_ANCHORS)


def test_no_client_touches_the_store_the_audit_trail_or_the_application():
    """R5, belt to the braces of the import rule: a name that never appears
    cannot come back through a deferred import.

    Every marker below was in a client at some point. The OTLP receiver opened
    the event store; the status-line shim opened usage.db; all nine wrote an
    audit row from their `except` blocks, which is what made
    `audit/record.py` — and through it the whole sqlite layer — part of
    nine foreign processes and gave audit.db ten writers.

    `_handoff.py` is the ONE file allowed to touch a file, and only the
    file-access markers are lifted for it — it may still not name a store, a
    repository, the audit trail or the application. It exists because both pane
    click gestures are frontend-only: the terminal launches a separate program
    for a click, that program has no model and no daemon to ask, and the text it
    needs is already on the pane's screen. So the two processes meet in the temp
    directory. That is a local channel between two halves of one frontend, which
    is what this rule was never about; the rule is that a client does not reach
    OUR state, and a file of its own making is not our state.
    """
    forbidden = ("sqlite3", "repository", "audit", "app.providers",
                 "build_application", "RawEvent", "main.db", "audit.db")
    touches_files = ("open(", ".write_text(", ".write_bytes(", "os.makedirs")
    violations = []
    for path in sorted(CLIENT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        markers = forbidden if path.name == "_handoff.py" else forbidden + touches_files
        violations.extend(
            f"{path.name} contains {marker}" for marker in markers if marker in source
        )
    assert violations == []


def test_no_module_of_ours_imports_a_client():
    """The coupling must not grow back in the other direction either.

    `client/` is not a package and nothing above it may reach in: the daemon
    names those files (`core/clients.py`) and runs them, and that is the entire
    relationship.
    """
    importers = []
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            for imported in imported_names(path):
                if imported.split(".")[0] in {"client", "_http", "_daemon"}:
                    importers.append(path.relative_to(ROOT).as_posix())
    assert importers == []


def code_strings(path: Path):
    """Every string LITERAL in a module that is not a docstring.

    Prose may name a client file — half the value of these programs is that the
    daemon-side code says which one it talks to. Building a path out of the name
    is the thing that must live in one place, and that is a string in code.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    documentation = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                documentation.add(id(first.value))
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in documentation
    ]


def test_only_a_launcher_names_its_client_and_only_one_module_builds_the_path():
    """R7, in two halves.

    A client's FILENAME belongs to the code that starts it — the pane to the
    terminal adapter, the receiver to the OTLP launcher — and nothing else in the
    tree may name one in code, because a second namer is a second thing to keep
    true. The PATH is arithmetic, and it lives in exactly one module:
    `TerminalAdapter` used to join "panes" onto its own directory and
    `otel/launch.py` its own, so both processes that get launched carried a path
    assumption nobody could see.
    """
    for name in PUBLISHED + LAUNCHED:
        assert Path(clients.path(name)).is_file(), name
    # The two launchers say which file they run, and those are the two we launch.
    assert terminal_adapter.PANE_CLIENT == TERMINAL_PANE
    assert otel_launch.RECEIVER_CLIENT == CLAUDE_OTEL

    namers = {"terminal/adapter.py", "harness/impl/claude_code/otel/launch.py"}
    violations = []
    for package in OUR_PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            for literal in code_strings(path):
                if literal == "client" and relative != "core/clients.py":
                    violations.append(f"{relative} builds a path into client/")
                for name in PUBLISHED + LAUNCHED:
                    if name in literal and relative not in namers:
                        violations.append(f"{relative} names {name}")
    assert violations == []


# --- the HTTP boundary -------------------------------------------------------


def load_shared(name):
    """One of `client/`'s shared modules, the way a client loads it: by file,
    with the client directory on sys.path and nothing else about it known.

    Loaded ONCE per session and cached, which is not an optimisation: a second
    execution makes a second `_model` module, and then a `ShellFold` built by one
    is not an instance of the other's class. A pane process has exactly one of
    each, and a test that has two is testing something that cannot happen.
    """
    import importlib.util

    if name in sys.modules:
        return sys.modules[name]
    specification = importlib.util.spec_from_file_location(name, CLIENT / ("%s.py" % name))
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module                    # `_render` imports `_model`
    specification.loader.exec_module(module)
    return module


def load_http():
    return load_shared("_http")


def test_the_http_module_matches_the_daemon():
    """The one duplication this design accepts, pinned.

    `client/_http.py` is our side of the HTTP contract and it is a COPY: a client
    that imported the daemon's constants would import the daemon. So the copy is
    checked against the modules that read it — and every path against the routes
    the server actually serves, which is the half a hand-written constant cannot
    keep true on its own.
    """
    http_module = load_http()
    assert (http_module.HOST, http_module.PORT) == (contract.HOST_ADDRESS, contract.PORT_NUMBER)
    assert http_module.TERMINAL_WINDOW_HEADER == headers.TERMINAL_WINDOW_HEADER
    assert http_module.CLIENT_PROCESS_HEADER == headers.CLIENT_PROCESS_HEADER
    assert http_module.ACCOUNT_ID_HEADER == headers.ACCOUNT_ID_HEADER
    assert http_module.ACCOUNT_NAME_HEADER == headers.ACCOUNT_NAME_HEADER
    assert http_module.LAUNCH_MODEL_HEADER == headers.LAUNCH_MODEL_HEADER
    assert http_module.LAUNCH_EFFORT_HEADER == headers.LAUNCH_EFFORT_HEADER
    assert http_module.TELEMETRY_KIND_HEADER == TELEMETRY_KIND_HEADER
    assert http_module.LAUNCH_MODEL_VARIABLE == claude_launcher.LAUNCH_MODEL_VARIABLE
    assert http_module.LAUNCH_EFFORT_VARIABLE == claude_launcher.LAUNCH_EFFORT_VARIABLE
    assert http_module.ACCOUNT_SLUG_VARIABLE == account.SLUG_VARIABLE
    assert http_module.ACCOUNT_LABEL_VARIABLE == account.LABEL_VARIABLE
    assert http_module.PROBE_VARIABLE == claude_live_usage.PROBE_VARIABLE
    # One line per terminal we can drive: a client cannot import a plugin to ask
    # which variable names the current window, so it carries the union. BOTH are
    # pinned, because a terminal missing from this union is a terminal whose
    # sessions have no window — and therefore no gesture that needs one.
    assert set(http_module.WINDOW_ID_VARIABLES) == {
        kitty_remote.WINDOW_ID_VARIABLE, pty_plugin.WINDOW_ID_VARIABLE
    }

    # Routers, not modules: one module now declares two of them, because typing
    # into a terminal is guarded and looking at one is not.
    served = {
        route.path
        for router in (hook_routes.router, telemetry_routes.router,
                       pane_routes.router, control_routes.router,
                       session_data_routes.router, session_data_streams.router)
        for route in router.routes
    }
    assert http_module.HOOK_PATH % "{harness}" in served
    assert http_module.TELEMETRY_PATH % "{harness}" in served
    # The pane's three reads. Split on "?" because the constants carry their
    # query template and a route does not.
    for template, arguments in (
        (http_module.SESSION_DATA_PATH, ("{session_id}",)),
        (http_module.SESSION_ENTRIES_PATH, ("{session_id}", 0)),
        (http_module.SESSION_STREAM_PATH, ("{session_id}", 0)),
    ):
        assert (template % arguments).split("?")[0] in served
    assert set(http_module.PANE_COMMAND_PATHS.values()) <= served


def test_the_published_client_paths_are_an_api():
    """Three files we do not own hold 48 references to six of these — 28 hook
    commands and a status line in ~/.claude/settings.json, 10 in
    ~/.codex/hooks.json, 8 keymaps and 2 click protocols in kitty's config.

    Each is captured and cached by whatever launched the process, so a rename
    does not fail at the next launch: it blocks evidence MID-SESSION, for every
    session that already named the old path. Nothing checked that before.
    """
    for name in PUBLISHED + LAUNCHED:
        path = Path(clients.path(name))
        source = path.read_text(encoding="utf-8")
        assert source.startswith("#!/usr/bin/env python3\n"), name
        assert os.access(path, os.X_OK), f"{name} is not executable"
        compile(source, str(path), "exec")


# --- running them -----------------------------------------------------------


@dataclass
class Delivery:
    method: str
    path: str
    headers: dict
    body: bytes


class _Capture:
    def __init__(self) -> None:
        self.deliveries: list[Delivery] = []
        self.reply = b"{}"
        # A reply per path fragment, for the clients that read more than one
        # resource: the pane asks for an aggregate and then a page of entries,
        # and one `reply` cannot answer both.
        self.replies: dict[str, bytes] = {}
        self.stream = ""
        self.port = 0

    def delivery(self, path_fragment=""):
        return next(found for found in self.deliveries if path_fragment in found.path)


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *arguments):
        del format, arguments

    def _record(self, method, body=b""):
        self.server.capture.deliveries.append(
            Delivery(method, self.path, dict(self.headers), body)
        )

    def do_POST(self):
        self._record("POST", self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        self._answer(self.server.capture.reply)

    def do_GET(self):
        self._record("GET")
        # A stream first: every other path is answered by fragment, and a
        # stream's path is a resource's path with `/stream` on the end.
        if "/stream" not in self.path:
            for fragment, payload in self.server.capture.replies.items():
                if fragment in self.path:
                    self._answer(payload)
                    return
        if "/stream" in self.path or "/panes/" in self.path:
            frames = self.server.capture.stream.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frames)))
            self.end_headers()
            self.wfile.write(frames)
            return
        self._answer(self.server.capture.reply)

    def _answer(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def daemon():
    """A stub daemon on a free port: what every client here talks to."""
    capture = _Capture()
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    server.capture = capture
    capture.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield capture
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def run_client(name, arguments=(), stdin=b"", port=None, environment=None, timeout=15):
    """One published client, as a process, told where the daemon is the way its
    external configuration tells it: through the environment."""
    process_environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("KITTY_", "CLAUDE_", "BAQYLAU_"))
    }
    process_environment["BAQYLAU_DASHBOARD_PORT"] = str(port if port is not None else free_port())
    process_environment.update(environment or {})
    return subprocess.run(
        [sys.executable, clients.path(name), *arguments],
        input=stdin,
        capture_output=True,
        env=process_environment,
        timeout=timeout,
        check=False,
    )


def test_the_claude_hook_ships_its_stdin_and_what_it_observed(daemon):
    """The check that runs the process. Every other hook test in the suite reads
    a file, which is exactly how a pane process that died on line 1 passed the
    whole suite."""
    daemon.reply = b'{"reply":"yes"}'
    payload = b'{"session_id":"session-one","hook_event_name":"SessionStart"}'

    completed = run_client(
        CLAUDE_HOOK,
        stdin=payload,
        port=daemon.port,
        environment={
            "KITTY_WINDOW_ID": "77",
            account.SLUG_VARIABLE: "c2",
            account.LABEL_VARIABLE: "Account Two",
            claude_launcher.LAUNCH_MODEL_VARIABLE: "fable",
            claude_launcher.LAUNCH_EFFORT_VARIABLE: "high",
        },
    )

    assert completed.returncode == 0
    assert completed.stdout == b'{"reply":"yes"}'      # the reply reaches the harness
    delivery = daemon.delivery("/hooks")
    assert delivery.path == "/api/harnesses/claude_code/hooks"
    assert delivery.body == payload                    # the EXACT bytes, unparsed
    assert delivery.headers[headers.TERMINAL_WINDOW_HEADER] == "77"
    # Its own pid, not the CLI's: the daemon walks the ancestry, while the CLI is
    # still blocked on this response and therefore provably alive.
    assert delivery.headers[headers.CLIENT_PROCESS_HEADER].isdigit()
    assert delivery.headers[headers.ACCOUNT_ID_HEADER] == "c2"
    assert delivery.headers[headers.ACCOUNT_NAME_HEADER] == "Account Two"
    assert delivery.headers[headers.LAUNCH_MODEL_HEADER] == "fable"
    assert delivery.headers[headers.LAUNCH_EFFORT_HEADER] == "high"


def test_a_hook_of_the_daemons_own_probe_ships_nothing(daemon):
    """The daemon spawns the harness to READ its plan windows, and that process
    fires hooks like any other. Shipping them would put a session in the store
    that nobody started — a reader manufacturing the thing it reads."""
    completed = run_client(
        CLAUDE_HOOK,
        stdin=b'{"session_id":"probe-session","hook_event_name":"SessionStart"}',
        port=daemon.port,
        environment={claude_live_usage.PROBE_VARIABLE: "1"},
    )

    assert completed.returncode == 0
    assert completed.stdout == b""
    assert daemon.deliveries == []


def test_the_codex_hook_ships_only_what_codex_has(daemon):
    completed = run_client(
        CODEX_HOOK,
        stdin=b'{"session_id":"session-two"}',
        port=daemon.port,
        environment={"KITTY_WINDOW_ID": "12", account.SLUG_VARIABLE: "c2"},
    )

    assert completed.returncode == 0
    delivery = daemon.delivery("/hooks")
    assert delivery.path == "/api/harnesses/codex/hooks"
    assert delivery.headers[headers.TERMINAL_WINDOW_HEADER] == "12"
    # No accounts, no launch selections: Codex has neither, so neither is claimed
    assert headers.ACCOUNT_ID_HEADER not in delivery.headers
    assert headers.LAUNCH_MODEL_HEADER not in delivery.headers


def test_the_statusline_ships_the_windows_and_still_runs_the_real_status_line(daemon):
    """The shim must NEVER break the status line: the delegate's stdout is what
    Claude Code renders, and it runs whatever the capture did."""
    stdin = json.dumps({
        "session_id": "session-usage",
        "rate_limits": {"five_hour": {"used_percentage": 25, "resets_at": 2_000_000_000}},
    }).encode()

    completed = run_client(
        CLAUDE_STATUSLINE,
        arguments=[sys.executable, "-c", "import sys; sys.stdin.read(); print('HUD')"],
        stdin=stdin,
        port=daemon.port,
        environment={account.SLUG_VARIABLE: "work", account.LABEL_VARIABLE: "Work"},
    )

    assert completed.returncode == 0
    assert completed.stdout == b"HUD\n"
    delivery = daemon.delivery("/telemetry")
    assert delivery.path == "/api/harnesses/claude_code/telemetry"
    assert delivery.headers[TELEMETRY_KIND_HEADER] == "statusline"
    document = json.loads(delivery.body)
    assert document["rate_limits"]["five_hour"]["used_percentage"] == 25
    # Forwarded RAW — what a valid account id looks like is decided daemon-side
    assert (document["_account_id"], document["_account_name"]) == ("work", "Work")


def test_the_keybinding_ships_only_its_environment(daemon):
    for arguments in (["toggle"], ["grow", "9"], ["setpct", "75"]):
        completed = run_client(
            TERMINAL_KEYS, arguments, port=daemon.port,
            environment={"KITTY_WINDOW_ID": "77"},
        )
        assert completed.returncode == 0

    # one endpoint per gesture — the URL is the discriminator, so no body carries
    # a command word
    assert [delivery.path for delivery in daemon.deliveries] == [
        "/api/terminal/panes/toggle",
        "/api/terminal/panes/grow",
        "/api/terminal/panes/set-percent",
    ]
    toggle, grow, setpct = (json.loads(delivery.body) for delivery in daemon.deliveries)
    assert toggle == {"window_id": "77", "working_directory": os.getcwd()}
    assert grow["columns"] == 9
    assert setpct["percent"] == 75


def test_the_copy_and_expand_handlers_reach_the_pane_and_not_the_daemon(daemon, tmp_path):
    """Both click gestures are FRONTEND-ONLY, and this is what that means.

    The pane holds every byte it draws — content is embedded in the entries it
    was served — so a copy needs no route and an expansion needs no stored state.
    The two programs the terminal launches for a click talk to the pane through a
    local file (client/_handoff.py) and the daemon is not involved in either. The
    stub daemon here is a WITNESS: it must record no delivery at all.
    """
    handoff = load_shared("_handoff")
    session_id, kind = "session-one", "mirror"
    # A fake `pbcopy` on PATH: the real one would reach the machine's clipboard,
    # and a test may not.
    clipboard = tmp_path / "pbcopy"
    clipboard.write_text("#!/bin/sh\ncat > %s/copied\n" % tmp_path, encoding="utf-8")
    clipboard.chmod(0o755)

    # From a clean slate: an expansion deliberately OUTLIVES the pane that drew
    # it (the next pane on this session opens with the reader's own expansions in
    # place), so a leftover file from an earlier run would answer for this one.
    paths = [
        handoff.pane_path(session_id, kind),
        handoff.view_path(session_id, kind),
        handoff.lock_path(session_id, kind),
    ]
    for path in paths:
        Path(path).unlink(missing_ok=True)

    # The pane's half of the channel, written the way the pane writes it.
    handoff.publish(session_id, kind, {"sh:sh-1:out": "445 passed"})
    try:
        content = run_client(
            TERMINAL_CONTENT,
            ["baqylau-content://%s/%s/sh:sh-1:out" % (session_id, kind)],
            port=daemon.port,
            environment={"PATH": "%s:%s" % (tmp_path, os.environ.get("PATH", ""))},
        )
        view = run_client(
            TERMINAL_VIEW,
            ["baqylau-view://%s/%s/entry-9" % (session_id, kind)],
            port=daemon.port,
        )

        assert (content.returncode, view.returncode) == (0, 0)
        assert (tmp_path / "copied").read_bytes() == b"445 passed"
        # The expansion is recorded where the PANE will read it…
        assert handoff.opened(session_id, kind) == frozenset({"entry-9"})
        # …and toggling the same entry again puts it back.
        run_client(TERMINAL_VIEW, ["baqylau-view://%s/%s/entry-9" % (session_id, kind)],
                   port=daemon.port)
        assert handoff.opened(session_id, kind) == frozenset()
        # Neither gesture asked the daemon anything.
        assert daemon.deliveries == []
    finally:
        for path in paths:
            Path(path).unlink(missing_ok=True)


def test_a_pane_publishes_what_a_click_can_copy_and_redraws_what_a_click_expands():
    """The pane's half of both gestures, without a process.

    `copy_targets` is built from the MODEL rather than collected while painting,
    so what a click can reach is exactly what the feed holds — and the expanded
    set is an argument to the paint, so an expansion is a repaint and nothing
    more.
    """
    model_module = load_shared("_model")
    render = load_shared("_render")

    model = model_module.SessionModel()
    model.apply_snapshot({
        "cursor": 1,
        "session": {"session_id": "s", "lead_actor_id": "lead", "account": None},
        "actors": [{"actor_id": "kid", "name": "Explore", "background": {}}],
        "live": True,
    })

    def entry(entry_id, kind, body):
        return {
            "entry_id": entry_id, "type": kind, "cursor": int(entry_id),
            "actor_id": "kid", "parent_actor_id": None, "turn_id": None,
            "occurred_at": 1.0, "summary": None, "body": body,
        }

    model.apply_page({"items": [
        entry("1", "shell_started", {
            "shell_id": "sh-1", "command": {"text": "make test"}, "execution": "foreground",
        }),
        entry("2", "shell_output", {
            "shell_id": "sh-1", "stream": "output", "mode": "append",
            "content": {"text": "445 passed\n"},
        }),
        entry("3", "file", {
            "path": "domain/entries.py", "action": "updated", "state": "succeeded",
            "lines_added": 1, "lines_removed": 1,
            "content": {"text": "-old line\n+new line\n"},
        }),
    ]})

    # Both halves of the command, under the names a link carries.
    assert render.copy_targets(model) == {
        "sh:sh-1:cmd": "make test",
        "sh:sh-1:out": "445 passed\n",
    }

    closed = render.mirror(model, 80, view=lambda entry_id: "view://" + entry_id)
    assert "new line" not in closed, "a file is collapsed until somebody expands it"
    assert "view://3" in closed, "and the line is the click target"

    opened = render.mirror(
        model, 80, view=lambda entry_id: "view://" + entry_id, opened=frozenset({"3"}),
    )
    assert "new line" in opened and "old line" in opened
    # The diff came from the ENTRY. There is no second request and no route left
    # to make one to.
    assert "make test" in opened


def test_a_pane_renders_the_read_model_and_never_sends_a_width(daemon, monkeypatch):
    """A pane is told its address, its session and its kind — everything it
    cannot observe — and draws everything else itself.

    This is the whole shape of the redesign in one test: the daemon answers with
    FACTS, and the ANSI is the client's. The width never crosses the socket,
    which is what makes a resize a repaint instead of a reconnect.
    """
    monkeypatch.setattr(clients, "PORT_NUMBER", daemon.port)
    daemon.replies = {
        "/sessionData/session-one/entries": json.dumps({
            "items": [{
                "entry_id": "e1", "type": "shell_started", "cursor": 4,
                "actor_id": "lead", "parent_actor_id": None, "turn_id": None,
                "occurred_at": 1.0, "summary": None,
                "body": {
                    "shell_id": "sh-1",
                    "command": {"text": "make test", "media_type": "text/plain"},
                    "execution": "foreground",
                },
            }],
            "oldest_cursor": 4,
            "has_more": False,
        }).encode("utf-8"),
        "/sessionData/session-one": json.dumps({
            "cursor": 4,
            "session": {
                "session_id": "session-one", "harness": "claude_code", "title": None,
                "state": "running", "working_directory": "/tmp", "started_at": 1.0,
                "finished_at": None, "account": None, "lead_actor_id": "lead",
                "goal": None, "tasks": [],
            },
            "actors": [],
            "live": True,
            "repository": None,
        }).encode("utf-8"),
    }
    # One frame, then the connection ends: the command finishes, and what the
    # pane draws has to reflect the frame rather than the page it started from.
    daemon.stream = (
        'event: sessionData\ndata: '
        + json.dumps({"entries": [{
            "entry_id": "e2", "type": "shell_finished", "cursor": 5,
            "actor_id": "lead", "parent_actor_id": None, "turn_id": None,
            "occurred_at": 3.0, "summary": None,
            "body": {"shell_id": "sh-1", "state": "failed", "exit_code": 2},
        }]})
        + "\n\n"
    )
    process = subprocess.Popen(
        clients.command(TERMINAL_PANE, "session-one", "mirror"),
        stdout=subprocess.PIPE,
        env={**os.environ, "COLUMNS": "100", "LINES": "40"},
    )
    try:
        painted = _read_until(process, "failed (exit 2)")
    finally:
        process.terminate()
        process.wait(timeout=10)

    assert "make test" in painted                      # the page, drawn here
    assert "failed (exit 2)" in painted                # and then the frame
    assert "\x1b[" in painted                           # as ANSI, from this process
    assert daemon.delivery("/sessionData/session-one/entries").path == (
        "/sessionData/session-one/entries?at=4"
    )
    assert daemon.delivery("/stream").path == (
        "/sessionData/session-one/stream?after_cursor=4"
    )
    assert not [found for found in daemon.deliveries if "width" in found.path]


def _read_until(process, marker, timeout=20.0):
    """Everything the process has written by the time `marker` appears.

    A paint is one flushed write, so `read1` hands them back whole; a fixed-size
    `read` would block for a buffer the pane has no reason to fill.
    """
    painted = ""
    deadline = time.monotonic() + timeout
    while marker not in painted and time.monotonic() < deadline:
        chunk = process.stdout.read1(65536)
        if not chunk:
            break
        painted += chunk.decode("utf-8", "replace")
    return painted


def test_the_pane_folds_a_command_and_paints_it_at_its_own_width():
    """The fold and the painter, directly.

    Both are the client's now, and both are the kind of thing that is wrong for a
    week before anyone notices by eye: an output chunk that replaces instead of
    appending doubles a command's output, and a wrap that mis-counts a prefix
    silently eats a column.
    """
    model_module = load_shared("_model")
    render = load_shared("_render")

    def entry(entry_id, kind, body, at=1.0):
        return {
            "entry_id": entry_id, "type": kind, "cursor": int(entry_id),
            "actor_id": "lead", "parent_actor_id": None, "turn_id": None,
            "occurred_at": at, "summary": None, "body": body,
        }

    model = model_module.SessionModel()
    model.apply_snapshot({
        "cursor": 1,
        "session": {"session_id": "s", "lead_actor_id": "lead", "account": None},
        "actors": [{"actor_id": "lead", "name": "Lead", "background": {}}],
        "live": True,
    })
    model.apply_page({"items": [
        entry("1", "shell_started", {
            "shell_id": "sh", "command": {"text": "make test"}, "execution": "foreground",
        }),
        entry("2", "shell_output", {
            "shell_id": "sh", "stream": "output", "mode": "append",
            "content": {"text": "first\n"},
        }),
        entry("3", "shell_output", {
            "shell_id": "sh", "stream": "output", "mode": "replace",
            "content": {"text": "the whole output, all of it, arriving at once\n"},
        }),
        entry("4", "shell_finished", {"shell_id": "sh", "state": "succeeded", "exit_code": 0},
              at=2.5),
    ]})
    # One block, not four: a command is its start, its chunks and its finish.
    folded = list(model.feed())
    assert len(folded) == 1
    # Replaced, not appended: the first chunk is gone rather than prefixed.
    assert folded[0].output == "the whole output, all of it, arriving at once\n"

    narrow = render.mirror(model, 30)
    wide = render.mirror(model, 100)
    assert "the whole output, all of it, arriving at once" in wide
    # The same model at two widths: the text is the same and the wrapping is not.
    assert narrow.count("\n") > wide.count("\n")
    assert max(len(line) for line in _visible_rows(narrow)) <= 30
    # An overlapping page after a reconnect is applied twice and shows once.
    model.apply_page({"items": [entry("2", "shell_output", {
        "shell_id": "sh", "stream": "output", "mode": "append",
        "content": {"text": "DOUBLED"},
    })]})
    assert "DOUBLED" not in render.mirror(model, 100)

    # …and a prompt the harness DISCARDED stays gone across that same replay. Two
    # prompts naming one parent means the older is dead; deleting it without
    # remembering the decision would re-admit it as news on the next reconnect,
    # and it would stay, because the survivor that condemned it is applied once.
    def prompt(entry_id, reply_to):
        return entry(entry_id, "message", {
            "role": "user", "phase": "prompt",
            "content": {"text": "ask " + entry_id}, "reply_to": reply_to,
        })

    # Asserted on the MODEL rather than on the paint, because the mirror hides
    # the lead's own conversation on purpose (it is a window on the work, not a
    # second copy of the chat you are having) — so a paint would say nothing
    # about whether the entry is held.
    def prompt_ids():
        return [
            item["entry_id"] for item in model.feed()
            if not isinstance(item, model_module.ShellFold) and item["type"] == "message"
        ]

    model.apply_page({"items": [prompt("8", "parent-1"), prompt("9", "parent-1")]})
    assert prompt_ids() == ["9"]
    model.apply_page({"items": [prompt("8", "parent-1")]})
    assert prompt_ids() == ["9"]


# SGR colour, the screen-clearing pair, and OSC 8 hyperlinks — none of which
# occupies a column. A width assertion has to be made after all three are gone,
# and the link URI in particular is longer than the text it wraps.
_INVISIBLE = re.compile(r"\x1b\[[0-9;]*[mHJ]|\x1b\]8;;[^\x1b]*\x1b\\")


def _visible_rows(painted):
    """The painted rows as a person sees their WIDTH: escape sequences occupy no
    columns, so a width assertion has to be made after they are removed."""
    return [_INVISIBLE.sub("", row) for row in painted.split("\n")]


def test_the_mirror_draws_the_task_list_as_state_rather_than_as_history():
    """The task list is AGGREGATE state, so it is a panel and not feed rows.

    It used to be a line per change, from a `task.changed` event — which meant a
    list of three items that moved twice read as six lines of history, none of
    them the current list. Now the pane draws what the list IS, and the only thing
    it keeps from the old form is that a completed item stops being work.
    """
    model_module = load_shared("_model")
    render = load_shared("_render")

    def task(task_id, subject, state):
        return {
            "task_id": task_id, "subject": subject, "description": None,
            "state": state, "owner_actor_id": None,
        }

    model = model_module.SessionModel()
    model.apply_snapshot({
        "cursor": 1, "live": True, "actors": [],
        "session": {
            "session_id": "s", "lead_actor_id": "lead", "account": None,
            "tasks": [
                task("t1", "Rewrite the pane", "completed"),
                task("t2", "Rework the e2e suite", "in_progress"),
                task("t3", "Update the skill", "pending"),
                task("t4", "Abandoned", "deleted"),
            ],
        },
    })
    painted = "\n".join(_visible_rows(render.mirror(model, 60)))

    assert "tasks 1/3" in painted, "deleted tasks are not tasks, and done ones are a count"
    assert "Rework the e2e suite" in painted and "Update the skill" in painted
    assert "Rewrite the pane" not in painted, "a finished item is not work"
    assert "Abandoned" not in painted

    # A session with no list gets no panel at all — not an empty one.
    empty = model_module.SessionModel()
    empty.apply_snapshot({
        "cursor": 1, "live": True, "actors": [],
        "session": {"session_id": "s", "lead_actor_id": "lead", "account": None, "tasks": []},
    })
    assert "tasks" not in "\n".join(_visible_rows(render.mirror(empty, 60)))


def test_the_scoreboard_clock_moves_between_frames_only_while_the_actor_is_active():
    """The clock the daemon used to redraw once a second, now the pane's.

    Frames arrive on CHANGE, and `active_seconds` is measured when the daemon
    builds one — so on a working session that says nothing for a minute, a pane
    that only ever showed the number it was sent would show a clock standing
    still. `active` is what makes carrying it forward honest: the pane adds its
    own elapsed time only while an interval is open, and an idle actor's clock
    stays exactly where the daemon left it.
    """
    model_module = load_shared("_model")
    render = load_shared("_render")

    def scoreboard_with(active):
        model = model_module.SessionModel()
        model.apply_snapshot({
            "cursor": 1,
            "session": {"session_id": "s", "lead_actor_id": "lead", "account": None},
            "actors": [{
                "actor_id": "lead", "name": "Lead", "background": {},
                "usage": {"tokens": {}, "cost_in_usd": None},
                "statistics": {"active_seconds": 100.0, "active": active},
            }],
            "live": True,
        })
        return model, render

    working, render = scoreboard_with(True)
    assert "1m40s" in render.scoreboard(working, 80)
    # Rewind the model's own frame mark rather than sleeping: the elapsed time is
    # measured monotonically from when the frame landed, and a test may not spend
    # thirty seconds proving it.
    working._framed_at -= 30.0
    assert "2m10s" in render.scoreboard(working, 80)

    idle, render = scoreboard_with(False)
    idle._framed_at -= 30.0
    assert "1m40s" in render.scoreboard(idle, 80)


def test_the_otlp_receiver_forwards_an_export_and_acknowledges_it(daemon, monkeypatch):
    monkeypatch.setattr(clients, "PORT_NUMBER", daemon.port)
    listen_port = free_port()
    export = json.dumps({"resourceMetrics": []}).encode()
    process = subprocess.Popen(clients.command(CLAUDE_OTEL, listen_port, 30))
    try:
        connection = _wait_for(listen_port)
        # gzip too: Claude Code's exporter compresses, and being the endpoint is
        # what this process is for
        connection.request("POST", "/v1/metrics", gzip.compress(export),
                           {"Content-Encoding": "gzip", "Content-Type": "application/json"})
        response = connection.getresponse()
        assert (response.status, response.read()) == (200, b"{}")
        connection.close()
    finally:
        process.terminate()
        process.wait(timeout=10)

    delivery = daemon.delivery("/telemetry")
    assert delivery.path == "/api/harnesses/claude_code/telemetry"
    assert delivery.headers[TELEMETRY_KIND_HEADER] == "otlp"
    assert delivery.body == export                     # decompressed, unparsed


def _wait_for(port, attempts=400):
    """Poll until the receiver has bound its port — up to 20 s, because this runs
    under a parallel suite where a fresh interpreter can be slow to start."""
    for _attempt in range(attempts):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        with contextlib.suppress(OSError):
            connection.connect()
            return connection
        connection.close()
        threading.Event().wait(0.05)
    raise AssertionError("the receiver never bound its port")


USAGE = {
    "claude_hook.py": ([], b'{"session_id":"s"}'),
    "claude_statusline.py": ([], b'{"session_id":"s"}'),
    "codex_hook.py": ([], b'{"session_id":"s"}'),
    "terminal_keys.py": (["toggle"], b""),
    # Session, pane kind, target — the two click handlers reach the PANE now, not
    # the daemon, and with no pane running they must still say nothing.
    "terminal_view.py": (["baqylau-view://session-one/mirror/entry-9"], b""),
    "terminal_content.py": (["baqylau-content://session-one/mirror/sh:1:out"], b""),
}


def test_a_client_is_silent_when_the_daemon_is_down():
    """R5, and the rule that lets every client be this small.

    A hook must never fail its harness, a keypress has nowhere to print and the
    status line must render regardless — so an unreachable daemon is not an
    error to report, it is a delivery that did not happen. The audit rows this
    gives up (`<harness> hook (deliver)`, `otel delivery (daemon unreachable)`)
    were bought with the sqlite layer in nine processes' failure paths.
    """
    for name in PUBLISHED:
        arguments, stdin = USAGE[name]
        completed = run_client(name, arguments, stdin=stdin)
        assert completed.returncode == 0, f"{name}: {completed.stderr!r}"
        assert completed.stdout == b"", name
        assert completed.stderr == b"", name


def test_a_client_says_how_to_use_it_and_refuses_nothing_else():
    """Bad argv is the one failure a client may report: it comes from OUR config,
    not from the daemon, and it is what a human reads while writing that config."""
    for name in (TERMINAL_KEYS, TERMINAL_VIEW, TERMINAL_CONTENT):
        completed = run_client(name, ["nonsense"])
        assert completed.returncode == 2, name
        assert b"usage:" in completed.stderr, name
    for name, arguments in ((TERMINAL_PANE, []), (CLAUDE_OTEL, [])):
        completed = subprocess.run(
            [sys.executable, clients.path(name), *arguments],
            capture_output=True, timeout=15, check=False,
        )
        assert completed.returncode == 1 and b"usage:" in completed.stderr, name
