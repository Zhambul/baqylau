# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify terminal client architecture and behavior."""

from __future__ import annotations

import ast
import os
import sys
from importlib import util as importlib_util
from pathlib import Path
from typing import TYPE_CHECKING

from tests import (
    client_loading_api_dependencies as api_dependencies,
    client_loading_runtime_dependencies as runtime_dependencies,
)
from tests.test_client_architecture import (
    _route_paths,
    code_strings,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


CLIENT = ROOT / "client"


CLAUDE_HOOK = "claude_hook.py"


CLAUDE_STATUSLINE = "claude_statusline.py"


CLAUDE_OTEL = "claude_otel.py"


CODEX_HOOK = "codex_hook.py"


TERMINAL_PANE = "terminal_pane.py"


TERMINAL_KEYS = "terminal_keys.py"


TERMINAL_VIEW = "terminal_view.py"


TERMINAL_CONTENT = "terminal_content.py"


PUBLISHED = (CLAUDE_HOOK, CLAUDE_STATUSLINE, CODEX_HOOK, TERMINAL_KEYS, TERMINAL_VIEW, TERMINAL_CONTENT)


LAUNCHED = (CLAUDE_OTEL, TERMINAL_PANE)


CLIENT_NAMERS = frozenset(("terminal/pane_client.py", "harness/impl/claude_code/otel/launch.py"))


TEXT_ENCODING = "utf-8"


PYTHON_FILE_PATTERN = "*.py"


OUR_PACKAGES = (
    "api",
    "app",
    "core",
    "dashboard",
    "audit",
    "domain",
    "engine",
    "harness",
    "notify",
    "repository",
    "terminal",
)


def _client_name_violations(path: Path) -> Iterator[str]:
    relative_path = path.relative_to(ROOT).as_posix()
    for literal in code_strings(path):
        if literal == "client" and relative_path != "core/clients.py":
            yield f"{relative_path} builds a path into client/"
        yield from (
            f"{relative_path} names {client_name}"
            for client_name in PUBLISHED + LAUNCHED
            if client_name in literal and relative_path not in CLIENT_NAMERS
        )


def _project_python_paths() -> Iterator[Path]:
    for package in OUR_PACKAGES:
        yield from sorted((ROOT / package).rglob(PYTHON_FILE_PATTERN))


def test_only_launcher_names_its_client_and_only() -> None:
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
        assert Path(runtime_dependencies.clients.path(name)).is_file(), name
    # The two launchers say which file they run, and those are the two we launch.
    assert runtime_dependencies.terminal_adapter.PANE_CLIENT == TERMINAL_PANE
    assert runtime_dependencies.otel_launch.RECEIVER_CLIENT == CLAUDE_OTEL

    violations = [violation for path in _project_python_paths() for violation in _client_name_violations(path)]
    assert not violations


def load_shared(name: str) -> ModuleType:
    """Load a shared client module by file and keep one copy in the module cache.

    One copy preserves class identity across the client modules.

    Returns:
        The cached module, or the module loaded from the client directory.

    Raises:
        ImportError: If no module loader is available for the file.

    """
    loaded_module = sys.modules.get(name)
    if loaded_module is not None:
        return loaded_module
    sys.path.insert(0, str(CLIENT))
    specification = importlib_util.spec_from_file_location(name, CLIENT / (f"{name}.py"))
    if specification is None or specification.loader is None:
        message = f"cannot load client module {name!r}"
        raise ImportError(message)
    module = importlib_util.module_from_spec(specification)
    sys.modules[name] = module  # `_render` imports `_model`
    specification.loader.exec_module(module)
    return module


def load_http() -> ModuleType:
    """Return HTTP.

    Returns:
        HTTP.

    """
    return load_shared("_http")


def test_the_http_module_matches_the_daemon() -> None:
    """The one duplication this design accepts, pinned.

    `client/_http.py` is our side of the HTTP runtime_dependencies.contract and it is a COPY: a client
    that imported the daemon's constants would import the daemon. So the copy is
    checked against the modules that read it — and every path against the routes
    the server actually serves, which is the half a hand-written constant cannot
    keep true on its own.
    """
    http_module = load_http()
    assert (http_module.HOST, http_module.PORT) == (
        runtime_dependencies.contract.HOST_ADDRESS,
        runtime_dependencies.contract.PORT_NUMBER,
    )
    assert (
        http_module.TERMINAL_WINDOW_HEADER,
        http_module.CLIENT_PROCESS_HEADER,
        http_module.LAUNCH_MODEL_HEADER,
        http_module.LAUNCH_EFFORT_HEADER,
        http_module.TELEMETRY_KIND_HEADER,
        http_module.LAUNCH_MODEL_VARIABLE,
        http_module.LAUNCH_EFFORT_VARIABLE,
        http_module.PROBE_VARIABLE,
    ) == (
        runtime_dependencies.headers.TERMINAL_WINDOW_HEADER,
        runtime_dependencies.headers.CLIENT_PROCESS_HEADER,
        runtime_dependencies.headers.LAUNCH_MODEL_HEADER,
        runtime_dependencies.headers.LAUNCH_EFFORT_HEADER,
        runtime_dependencies.telemetry.TELEMETRY_KIND_HEADER,
        runtime_dependencies.claude_launcher.LAUNCH_MODEL_VARIABLE,
        runtime_dependencies.claude_launcher.LAUNCH_EFFORT_VARIABLE,
        runtime_dependencies.claude_live_usage.PROBE_VARIABLE,
    )
    # One line per terminal we can drive: a client cannot import a plugin to ask
    # which variable names the current window, so it carries the union. BOTH are
    # pinned, because a terminal missing from this union is a terminal whose
    # sessions have no window — and therefore no gesture that needs one.
    assert set(http_module.WINDOW_ID_VARIABLES) == {
        runtime_dependencies.kitty_remote.WINDOW_ID_VARIABLE,
        runtime_dependencies.pty_registry.WINDOW_ID_VARIABLE,
    }

    # Routers, not modules: one module now declares two of them, because typing
    # into a terminal is guarded and looking at one is not.
    served = {
        path
        for router in (
            api_dependencies.hook_routes.router,
            api_dependencies.telemetry_routes.router,
            api_dependencies.pane_routes.router,
            api_dependencies.control_routes.router,
            api_dependencies.session_data_routes.router,
            api_dependencies.session_data_streams.router,
        )
        for path in _route_paths(router)
    }
    assert {
        http_module.HOOK_PATH % "{harness}",
        http_module.TELEMETRY_PATH % "{harness}",
        *http_module.PANE_COMMAND_PATHS.values(),
    } <= served
    # The pane's three reads. Split on "?" because the constants carry their
    # query template and a route does not.
    assert all(
        (template % arguments).split("?")[0] in served
        for template, arguments in (
            (http_module.SESSION_DATA_PATH, ("{session_id}",)),
            (http_module.SESSION_ENTRIES_PATH, ("{session_id}", 0)),
            (http_module.SESSION_STREAM_PATH, ("{session_id}", 0, 0)),
        )
    )


def test_the_published_client_paths_are_an_api() -> None:
    """Verify the published client paths are an api.

    Three files we do not own hold 48 references to six of these — 28 hook
        commands and a status line in ~/.claude/settings.json, 10 in
        ~/.codex/hooks.json, 8 keymaps and 2 click protocols in kitty's config.

        Each is captured and cached by whatever launched the process, so a rename
        does not fail at the next launch: it blocks evidence MID-SESSION, for every
        session that already named the old path. Nothing checked that before.
    """
    for name in PUBLISHED + LAUNCHED:
        path = Path(runtime_dependencies.clients.path(name))
        source = path.read_text(encoding=TEXT_ENCODING)
        assert source.startswith("#!/usr/bin/env python3\n"), name
        assert os.access(path, os.X_OK), f"{name} is not executable"
        ast.parse(source, filename=str(path))
