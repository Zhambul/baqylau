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
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest import REPOSITORY_ROOT
from api.common import content as content_routes, hooks as hook_routes, telemetry as telemetry_routes
from api.terminal import panes as pane_routes, streams as stream_routes, views as view_routes
from core import clients
from core.daemon import contract
from harness.hooks import headers
from harness.impl.claude_code import account, launcher as claude_launcher
from harness.impl.claude_code.otel import launch as otel_launch
from harness.impl.claude_code.usage import live as claude_live_usage
from harness.models import TELEMETRY_KIND_HEADER
from terminal import adapter as terminal_adapter
from terminal.impl.kitty import remote as kitty_remote

ROOT = Path(REPOSITORY_ROOT)
CLIENT = ROOT / "client"
SHARED = ("_wire.py", "_daemon.py")

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
    """
    forbidden = ("sqlite3", "repository", "audit", "app.providers",
                 "build_application", "RawEvent", "main.db", "audit.db",
                 "open(", ".write_text(", ".write_bytes(", "os.makedirs")
    violations = [
        f"{path.name} contains {marker}"
        for path in sorted(CLIENT.glob("*.py"))
        for marker in forbidden
        if marker in path.read_text(encoding="utf-8")
    ]
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
                if imported.split(".")[0] in {"client", "_wire", "_daemon"}:
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


# --- the wire ---------------------------------------------------------------


def load_wire():
    """`client/_wire.py` as a module, the way a client loads it: by file, with
    the client directory on sys.path and nothing else about it known."""
    import importlib.util

    specification = importlib.util.spec_from_file_location("_wire", CLIENT / "_wire.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_the_wire_matches_the_daemon():
    """The one duplication this design accepts, pinned.

    `client/_wire.py` is our side of the HTTP contract and it is a COPY: a client
    that imported the daemon's constants would import the daemon. So the copy is
    checked against the modules that read it — and every path against the routes
    the server actually serves, which is the half a hand-written constant cannot
    keep true on its own.
    """
    wire = load_wire()
    assert (wire.HOST, wire.PORT) == (contract.HOST_ADDRESS, contract.PORT_NUMBER)
    assert wire.CALLER_HEADER == contract.POST_HEADER
    assert wire.TERMINAL_WINDOW_HEADER == headers.TERMINAL_WINDOW_HEADER
    assert wire.CLIENT_PROCESS_HEADER == headers.CLIENT_PROCESS_HEADER
    assert wire.ACCOUNT_ID_HEADER == headers.ACCOUNT_ID_HEADER
    assert wire.ACCOUNT_NAME_HEADER == headers.ACCOUNT_NAME_HEADER
    assert wire.LAUNCH_MODEL_HEADER == headers.LAUNCH_MODEL_HEADER
    assert wire.LAUNCH_EFFORT_HEADER == headers.LAUNCH_EFFORT_HEADER
    assert wire.TELEMETRY_KIND_HEADER == TELEMETRY_KIND_HEADER
    assert wire.LAUNCH_MODEL_VARIABLE == claude_launcher.LAUNCH_MODEL_VARIABLE
    assert wire.LAUNCH_EFFORT_VARIABLE == claude_launcher.LAUNCH_EFFORT_VARIABLE
    assert wire.ACCOUNT_SLUG_VARIABLE == account.SLUG_VARIABLE
    assert wire.ACCOUNT_LABEL_VARIABLE == account.LABEL_VARIABLE
    assert wire.PROBE_VARIABLE == claude_live_usage.PROBE_VARIABLE
    # One line per terminal we can drive: a client cannot import a plugin to ask
    # which variable names the current window, so it carries the union.
    assert set(wire.WINDOW_ID_VARIABLES) == {kitty_remote.WINDOW_ID_VARIABLE}

    served = {
        route.path
        for module in (hook_routes, telemetry_routes, content_routes,
                       pane_routes, stream_routes, view_routes)
        for route in module.router.routes
    }
    assert wire.HOOK_PATH % "{harness}" in served
    assert wire.TELEMETRY_PATH % "{harness}" in served
    assert wire.PANE_STREAM_PATH.split("?")[0] % ("{session_id}", "{kind}") in served
    assert wire.VIEW_PATH in served
    assert set(wire.PANE_COMMAND_PATHS.values()) <= served


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
        if "/panes/" in self.path:
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
    assert delivery.headers[contract.POST_HEADER] == "1"
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


def test_the_view_and_content_handlers_resolve_nothing_themselves(daemon, tmp_path):
    daemon.reply = b"copied text"
    # A fake `pbcopy` on PATH: the real one would reach the machine's clipboard,
    # and a test may not.
    clipboard = tmp_path / "pbcopy"
    clipboard.write_text("#!/bin/sh\ncat > %s/copied\n" % tmp_path, encoding="utf-8")
    clipboard.chmod(0o755)

    view = run_client(TERMINAL_VIEW, ["baqylau-view://event-9:command"],
                      port=daemon.port)
    content = run_client(
        TERMINAL_CONTENT, ["baqylau-content://event-9:command"],
        port=daemon.port,
        environment={"PATH": "%s:%s" % (tmp_path, os.environ.get("PATH", ""))},
    )

    assert (view.returncode, content.returncode) == (0, 0)
    assert json.loads(daemon.delivery("/views").body) == {"content_reference": "event-9:command"}
    assert daemon.delivery("/api/content/").path == "/api/content/event-9%3Acommand"
    assert (tmp_path / "copied").read_bytes() == b"copied text"


def test_a_pane_copies_the_frames_the_daemon_renders(daemon, monkeypatch):
    """A pane is told its address, its session and its kind — everything it
    cannot observe — and paints nothing of its own."""
    monkeypatch.setattr(clients, "PORT_NUMBER", daemon.port)
    daemon.stream = (
        'event: session\ndata: {"session_id": "session-one"}\n\n'
        'event: frame\ndata: {"ansi": "HELLO"}\n\n'
    )
    process = subprocess.Popen(
        clients.command(TERMINAL_PANE, "session-one", "mirror"),
        stdout=subprocess.PIPE,
        env={**os.environ, "COLUMNS": "100", "LINES": "40"},
    )
    try:
        assert process.stdout.read(5) == b"HELLO"
    finally:
        process.terminate()
        process.wait(timeout=10)
    # The width is the pane's own, and it is a query on the stream it opens
    assert daemon.delivery("/panes/").path == (
        "/api/sessions/session-one/panes/mirror/stream?width=100"
    )


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
    "terminal_view.py": (["baqylau-view://event-9:command"], b""),
    "terminal_content.py": (["baqylau-content://event-9:command"], b""),
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
