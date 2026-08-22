"""The live-harness suite: its fixtures, and the wiring that finds its steps.

This suite is not hermetic and does not pretend to be. It starts the REAL daemon
the way a person starts one, runs the REAL harness CLI against a real workspace
on disk, and spends real tokens — because the failure it exists to catch is a
harness release changing its evidence under an integration that keeps reporting
success. Nothing simulated can catch that.

What it does isolate is our own state: a private data directory (both databases)
and a private port, both passed as the daemon's own launch flags. The harness's
OWN configuration — credentials, installed hooks — is deliberately the real one
(see support/environment.py).

The steps themselves live in `impl/`, one module per concern. They are pulled
into this namespace, and that is not a style choice: pytest-bdd registers each
step as a FIXTURE in the module that defines it, and pytest discovers fixtures
only from conftest and test modules. So a step in a third module is invisible
until its names are here.

The one thing NOT expressible as a sentence is the invariant every scenario is
held to — that nothing the harness said went uninterpreted. It is a fixture, not
a `Then`, precisely because a forgotten assertion is the failure mode this suite
exists to remove.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from impl.files import *               # noqa: F403
from impl.messages import *            # noqa: F403 — see the module docstring
from impl.persistence import *         # noqa: F403
from impl.session import *             # noqa: F403
from impl.shells import *              # noqa: F403
from impl.subagents import *           # noqa: F403
from impl.usage import *               # noqa: F403
from impl.world import World
from support import observe
from support.daemon import Daemon, free_port, start

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_WORKSPACE = os.path.expanduser("~/code/personal/baqylau-tests")

INTERPRETER_DRAIN_TIMEOUT_SECONDS = 30.0


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("baqylau live-harness tests")
    group.addoption(
        "--e2e-workspace",
        default=DEFAULT_WORKSPACE,
        help="the directory the harness runs in (default: %(default)s)",
    )
    group.addoption(
        "--e2e-data-dir",
        default=None,
        help="keep the run's databases here instead of a tmpdir — what you want after a failure",
    )
    # The two overrides that let a new model be tried against the WHOLE suite
    # without editing a single Examples table.
    group.addoption("--e2e-model", default=None, help="override every scenario's model")
    group.addoption("--e2e-effort", default=None, help="override every scenario's effort")


@pytest.fixture(scope="session")
def workspace(pytestconfig: pytest.Config) -> str:
    """The directory the harness works in. A real git repository, because a
    harness behaves differently outside one and the dashboard reads its status."""
    directory = os.path.abspath(os.path.expanduser(str(pytestconfig.getoption("--e2e-workspace"))))
    if not os.path.isdir(directory):
        raise pytest.UsageError(f"workspace does not exist: {directory}")
    return os.path.realpath(directory)


@pytest.fixture(scope="session")
def daemon(pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Daemon]:
    """One daemon for the whole run, on its own port and its own databases."""
    configured = pytestconfig.getoption("--e2e-data-dir")
    data_directory = (
        os.path.abspath(os.path.expanduser(str(configured)))
        if configured
        else str(tmp_path_factory.mktemp("baqylau-live-data"))
    )
    running = start(REPOSITORY_ROOT, data_directory, free_port())
    print(f"\nlive daemon · {running.url} · data {data_directory}")
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture(autouse=True)
def nothing_went_uninterpreted(world: World, daemon: Daemon) -> Iterator[None]:
    """After every scenario: the harness said nothing we failed to understand.

    Checked before the CLI is stopped (its exit writes evidence of its own) and
    after the interpreter has drained, so the verdict is complete rather than
    merely early. `ignored_nonsemantic` is not a finding — that is the
    interpreter recognising a record and having nothing to say about it.
    """
    yield
    try:
        if world.session_id is None:
            return
        observe.until(
            "the interpreter to rule on every raw event",
            lambda: observe.unverdicted_count(daemon) == 0,
            timeout=INTERPRETER_DRAIN_TIMEOUT_SECONDS,
        )
        unknown = observe.uninterpreted(daemon, world.session_id)
        errors = observe.audit_errors(daemon, world.session_id)
        assert not unknown, "the harness said things we did not understand:\n" + "\n".join(unknown)
        assert not errors, "the machinery recorded errors:\n" + "\n".join(errors)
    finally:
        if world.live is not None:
            world.live.stop(world.session_id)
