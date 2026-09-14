# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the normal application graph with a test terminal."""

from pathlib import Path

from app import injection, provider_runtime
from engine.interpret.loop import Interpreter
from harness.impl.opencode2.definition import default_runtime_config
from harness.impl.opencode2.sources import HARNESS
from harness.runtime import HarnessRuntimeConfig, default_harness_runtime_configs
from tests import fake_terminal
from tests.provider_graph import ProviderGraph

WINDOW = "opencode-test-window"


def application(directory: Path) -> ProviderGraph:
    """Build an isolated graph with an OpenCode2 log directory.

    Returns:
        The application graph.

    """
    graph = ProviderGraph()
    injection.seed(
        graph.instances, provider_runtime.terminal_plugin,
        fake_terminal.FakeTerminal(windows=(fake_terminal.window(WINDOW),)).plugin(),
    )
    injection.seed(
        graph.instances, provider_runtime.harness_runtime_configs,
        default_harness_runtime_configs().updated(
            HARNESS, HarnessRuntimeConfig(default_runtime_config().executable, directory),
        ),
    )
    return graph


def drain(provider_graph: ProviderGraph) -> None:
    """Process registration, then read the newly registered source."""
    interpreter = provider_graph.provider("interpreter", Interpreter)
    interpreter.tick()
    provider_graph.reaction_loop.tick()
    interpreter.tick()
    provider_graph.reaction_loop.tick()
