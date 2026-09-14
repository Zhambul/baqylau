# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared native launch fixtures."""

from pathlib import Path
from types import SimpleNamespace

from harness.contract import HarnessPlugin
from harness.impl.discovery import installed
from harness.impl.opencode2.sources import HARNESS as OPENCODE_HARNESS
from harness.models.controls import AttachmentReference
from harness.models.launch import LaunchRequest, LaunchResult
from harness.runtime import HarnessRuntimeConfig, HarnessRuntimeConfigs, HarnessRuntimeEntry
from terminal.models.tabs import EnvironmentVariable
from tests.fake_terminal import FakeTerminal
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import support_audit, vocabulary as fixture


def _native_runtime_configs() -> HarnessRuntimeConfigs:
    return HarnessRuntimeConfigs(
        (
            HarnessRuntimeEntry(
                CLAUDE_CODE_HARNESS,
                HarnessRuntimeConfig(fixture.CLAUDE, Path(fixture.WORK_CLAUDE_HOME_PATH)),
            ),
            HarnessRuntimeEntry(
                CODEX_HARNESS,
                HarnessRuntimeConfig(fixture.CODEX_HARNESS, Path(fixture.WORK_CODEX_HOME_PATH)),
            ),
            HarnessRuntimeEntry(
                OPENCODE_HARNESS, HarnessRuntimeConfig("opencode2", Path("/work/opencode2-events")),
            ),
        ),
    )


def _installed_plugins(terminal: FakeTerminal) -> dict[str, HarnessPlugin]:
    return {
        str(plugin.harness_info.name): plugin
        for plugin in installed(
            _native_runtime_configs(),
            terminal.plugin(),
            SimpleNamespace(resumed=lambda *_arguments: None),
            support_audit.silent_audit(),
            (EnvironmentVariable(fixture.DASHBOARD_PORT_ENV, fixture.DASHBOARD_PORT_TEXT),),
        )
    }


def _native_launch_results(terminal: FakeTerminal) -> tuple[LaunchResult, LaunchResult]:
    plugins = _installed_plugins(terminal)
    claude_launcher = plugins[fixture.CLAUDE_CODE_HARNESS].launcher
    codex_launcher = plugins[fixture.CODEX_HARNESS].launcher
    assert claude_launcher is not None
    assert codex_launcher is not None
    attachment = AttachmentReference(
        "/work/context.md",
        "context.md",
        "text/markdown",
    )
    return (
        claude_launcher.launch(
            LaunchRequest(
                working_directory=fixture.WORK_PATH,
                initial_text=fixture.HELLO,
                model=fixture.FABLE,
                effort=fixture.HIGH,
                account_id=None,
                resume_session_id=None,
                attachments=(attachment,),
            ),
        ),
        codex_launcher.launch(
            LaunchRequest(
                working_directory=fixture.WORK_PATH,
                initial_text=fixture.HELLO,
                model="gpt-5.6-terra",
                effort=fixture.HIGH,
                account_id=None,
                resume_session_id=None,
                attachments=(attachment,),
            ),
        ),
    )
