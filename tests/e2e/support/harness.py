"""One real harness CLI, started the way the product starts it.

Nothing here is invented. The argv comes from `plugin.launcher.prepare()`
wrapped by `terminal.launch.launch_tab_request`, exactly as
`HarnessLauncherService` builds it for a launch from the dashboard's composer.
The tab is opened, typed into, keyed at and read back through
`terminal/contract.py` — the same five protocols kitty implements — so a change
to either the launch convention or the terminal contract is a change this suite
feels, and the model/effort flags under test are the ones the product passes.

What differs is WHICH terminal: `terminal/impl/pty`, whose windows are
pseudo-terminals rather than kitty tabs, which is what lets the tests run
headless. The CLI still sees a tty, still runs its full TUI, and still fires its
own hooks — into the test daemon, because `BAQYLAU_DASHBOARD_PORT` rides its
environment and `client/_wire.py` reads it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from harness.impl import installed
from harness.models import LaunchRequest
from harness.registry import HarnessRegistry
from support.environment import child_environment
from terminal.contract import TerminalPlugin
from terminal.impl.pty.plugin import PtyWindows, pty_plugin
from terminal.launch import launch_tab_request
from terminal.models.input import KeySendRequest, TextSubmitRequest
from terminal.models.tabs import TabCloseRequest
from terminal.models.viewport import ScreenReadRequest

WHITESPACE = re.compile(r"\s+")
CHORD_KEY_PAUSE_SECONDS = 0.05

# The ONE-TIME gate both harnesses put in front of a directory they have not been
# run in before ("do you trust this folder"), with the affirmative answer already
# under the cursor. It is not part of any scenario — it is what a fresh checkout
# of the workspace costs — so the rig answers it and says so, rather than every
# feature file opening with a step about it.
#
# Matched against the screen with ALL whitespace removed: a TUI lays its words
# out across a grid, and a word may be split by the column the emulator wrapped
# at or spaced by the padding a box drew.
FIRST_RUN_GATES = (
    "trustthisfolder",                          # Claude Code
    "doyoutrustthecontentsofthisdirectory",     # Codex
)

# The hint a harness prints when a running command can be moved to the background.
# Claude Code registers the gesture's handler and prints this together, 2000 ms into
# a FOREGROUND command — so this marker is the only honest signal that pressing the
# key will do anything.
OFFER_MARKER = "runinbackground"


def flattened(screen: str) -> str:
    """Screen text with all whitespace removed — how to match a TUI's words when
    the grid they are laid out on decides where the spaces fall."""
    return WHITESPACE.sub("", screen.lower())


def _registry() -> HarnessRegistry:
    """The same registry the daemon builds, from the same installed() list."""
    registry = HarnessRegistry()
    for plugin in installed():
        registry.register(plugin)
    registry.validate()
    return registry


@dataclass
class LiveHarness:
    """A harness running in a terminal window, addressed through the contract."""

    harness: str
    command: tuple[str, ...]
    terminal: TerminalPlugin
    window_id: str
    gates_answered: list[str] = field(default_factory=list)

    def screen(self) -> str:
        """What the window SHOWS — for failure messages, not for assertions (the
        canonical event store is what this suite asserts on)."""
        response = self.terminal.viewport.read_screen(ScreenReadRequest(self.window_id))
        return response.text or ""

    def submit(self, text: str) -> None:
        """Type one line and press return."""
        response = self.terminal.input.submit_text(
            TextSubmitRequest(self.window_id, text, "type")
        )
        assert response.succeeded, f"could not type into the harness: {response.reason}"

    def send_chord(self, chord: str) -> None:
        """A keyboard chord ("ctrl+b", "ctrl+x ctrl+b"), one key event at a time.

        Through `TerminalInput.send_key` rather than by writing bytes: a key is
        an EVENT, and what a program reads for one depends on the keyboard mode
        it negotiated — which is the terminal's business, not this suite's.
        """
        for key in chord.split():
            response = self.terminal.input.send_key(KeySendRequest(self.window_id, key))
            assert response.succeeded, f"could not send {key!r}: {response.reason}"
            time.sleep(CHORD_KEY_PAUSE_SECONDS)

    def answer_first_run_gate(self) -> str | None:
        """Press return on a workspace-trust prompt, if one is on screen now.

        Called on every tick of the wait for the session rather than up front:
        the gate appears only the first time a harness sees a directory, and a
        fixed wait for something that usually never comes would tax every run.
        """
        visible = flattened(self.screen())
        for gate in FIRST_RUN_GATES:
            if gate in visible and gate not in self.gates_answered:
                self.gates_answered.append(gate)
                self.terminal.input.send_key(KeySendRequest(self.window_id, "enter"))
                return gate
        return None

    def running(self) -> bool:
        return self.window_id in {
            window.window_id for window in self.terminal.metadata.windows()
        }

    def stop(self) -> None:
        self.terminal.tabs.close_tab(TabCloseRequest(self.window_id))


def launch(
    harness: str,
    *,
    workspace: str,
    prompt: str,
    model: str | None,
    effort: str | None,
    port: int,
) -> LiveHarness:
    """Start `harness` in `workspace` with `prompt` as its first turn."""
    plugin = _registry().plugin(harness)
    if plugin.launcher is None:
        raise AssertionError(f"{harness} cannot be launched")
    plan = plugin.launcher.prepare(LaunchRequest(
        working_directory=workspace,
        initial_text=prompt,
        model_id=model,
        effort=effort,
        account_id=None,
        resume_session_id=None,
    ))
    request = launch_tab_request(
        working_directory=workspace,
        command=(plan.command, *plan.arguments),
        environment=plan.environment,
    )
    # The harness's own configuration is deliberately the real one; what is
    # replaced is this process's session identity and the port its hooks report
    # to (see support/environment.py).
    terminal = pty_plugin(PtyWindows(child_environment(
        BAQYLAU_DASHBOARD_PORT=port,
        TERM="xterm-256color",
    )))
    opened = terminal.tabs.open_tab(request)
    assert opened.succeeded and opened.window_id, f"could not open a window: {opened.reason}"
    return LiveHarness(
        harness=harness,
        command=request.command,
        terminal=terminal,
        window_id=opened.window_id,
    )
