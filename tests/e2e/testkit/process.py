"""The process boundary for one configured application runtime."""

from __future__ import annotations

import multiprocessing
import os
import signal
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess

from api.diagnostics.models import DiagnosticsReportResponse
from api.runtime import ApplicationConfig, ApplicationEndpoint, DashboardApplication

START_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 15.0
HARNESS_PARENT_ENVIRONMENT_VARIABLES = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_OTEL_PORT",
    "CODEX_COMPANION_SESSION_ID",
    "BAQYLAU_LAUNCH_MODEL",
    "BAQYLAU_LAUNCH_EFFORT",
    "KITTY_WINDOW_ID",
)


def assert_clean_diagnostics(
    label: str,
    report: DiagnosticsReportResponse,
) -> None:
    findings = []
    if report.raw_event_count != report.verdict_count:
        findings.append(f"{report.raw_event_count - report.verdict_count} raw events have no verdict")
    findings.extend(
        f"raw event {item.raw_event_cursor} {item.source_type}:{item.source_position} "
        f"has decision {item.decision!r}: {item.reason or 'no reason'}; {item.payload}"
        for item in report.interpretation_problems
    )
    findings.extend(
        f"audit error {item.error_cursor} {item.component} {item.action}: {item.context}"
        for item in report.audit_problems
    )
    if findings:
        raise AssertionError(label + ":\n" + "\n".join(findings))


def _run_application(config: ApplicationConfig, messages: Connection) -> None:
    try:
        report = DashboardApplication(config).run(messages.send)
        messages.send(("exit", report.exit_code))
    except BaseException as error:
        messages.send(("error", type(error).__name__, str(error)))
        raise
    finally:
        messages.close()


@dataclass
class ApplicationProcess:
    process: SpawnProcess
    messages: Connection
    endpoint: ApplicationEndpoint
    config: ApplicationConfig

    @classmethod
    def start(cls, config: ApplicationConfig) -> ApplicationProcess:
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_run_application,
            args=(config, child),
            name="baqylau-e2e-application",
        )
        process.start()
        child.close()
        if not parent.poll(START_TIMEOUT_SECONDS):
            process.kill()
            process.join(STOP_TIMEOUT_SECONDS)
            raise AssertionError("the application did not report its endpoint")
        first = parent.recv()
        if isinstance(first, ApplicationEndpoint):
            return cls(process=process, messages=parent, endpoint=first, config=config)
        process.join(STOP_TIMEOUT_SECONDS)
        raise AssertionError(f"the application failed before startup: {first}")

    def stop(self) -> int:
        if self.process.is_alive() and self.process.pid is not None:
            os.kill(self.process.pid, signal.SIGTERM)
            self.process.join(STOP_TIMEOUT_SECONDS)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(STOP_TIMEOUT_SECONDS)
            raise AssertionError("the application did not stop after SIGTERM")
        return int(self.process.exitcode or 0)

    def restart(self) -> tuple[int, int]:
        """Replace the application process on the same endpoint and data."""
        before = self.process.pid
        if before is None:
            raise AssertionError("the application process has no process id")
        exit_code = self.stop()
        if exit_code != 0:
            raise AssertionError(f"the application exited with {exit_code}")
        self.messages.close()
        replacement = self.start(replace(self.config, port=self.endpoint.port))
        after = replacement.process.pid
        if after is None or after == before:
            replacement.stop()
            raise AssertionError("the application process was not replaced")
        self.process = replacement.process
        self.messages = replacement.messages
        self.endpoint = replacement.endpoint
        self.config = replacement.config
        return before, after
