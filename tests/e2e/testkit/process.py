# Copyright (c) 2026 Zhambyl Yermagambet
"""The process boundary for one configured application runtime."""

from __future__ import annotations

import multiprocessing
import os
import signal
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess
from typing import TYPE_CHECKING

from api.runtime import ApplicationConfig, ApplicationEndpoint, DashboardApplication

if TYPE_CHECKING:

    from api.diagnostics.models import (
        AuditProblemResponse,
        DiagnosticsReportResponse,
        InterpretationProblemResponse,
    )

START_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 15.0
# A harness that keeps state in the state home writes it here, under the data
# directory of the test application, and not in the file of the person.
HARNESS_STATE_DIRECTORY = "harness-state"
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
    """Check that every raw event has a verdict and diagnostics contain no problems.

    Raises:
        AssertionError: If a verdict is missing or interpretation or audit problems exist.

    """
    findings = []
    if report.raw_event_count != report.verdict_count:
        missing_verdict_count = report.raw_event_count - report.verdict_count
        findings.append(f"{missing_verdict_count} raw events have no verdict")
    findings.extend(
        _interpretation_finding(problem)
        for problem in report.interpretation_problems
    )
    findings.extend(
        _audit_finding(problem)
        for problem in report.audit_problems
    )
    if findings:
        finding_details = "\n".join(findings)
        msg = f"{label}:\n{finding_details}"
        raise AssertionError(msg)


def _interpretation_finding(problem: InterpretationProblemResponse) -> str:
    reason = problem.reason or "no reason"
    return (
        f"raw event {problem.raw_event_cursor} "
        f"{problem.source_type}:{problem.source_position} "
        f"has decision {problem.decision!r}: {reason}; {problem.payload}"
    )


def _audit_finding(problem: AuditProblemResponse) -> str:
    error_cursor = problem.error_cursor
    component = problem.component
    action = problem.action
    context = problem.context
    return f"audit error {error_cursor} {component} {action}: {context}"


def _run_application(config: ApplicationConfig, messages: Connection) -> None:
    try:
        _run_and_report(config, messages)
    except BaseException as error:
        messages.send(("error", type(error).__name__, str(error)))
        raise
    finally:
        messages.close()


def _run_and_report(config: ApplicationConfig, messages: Connection) -> None:
    report = DashboardApplication(config).run(messages.send)
    messages.send(("exit", report.exit_code))


def _started_application(
    config: ApplicationConfig,
    process: SpawnProcess,
    messages: Connection,
) -> ApplicationProcess:
    startup_message = messages.recv()
    if isinstance(startup_message, ApplicationEndpoint):
        return ApplicationProcess(
            process=process,
            messages=messages,
            endpoint=startup_message,
            config=config,
        )
    process.join(STOP_TIMEOUT_SECONDS)
    message = f"the application failed before startup: {startup_message}"
    raise AssertionError(message)


@dataclass
class ApplicationProcess:
    """Represent application process."""

    process: SpawnProcess
    messages: Connection
    endpoint: ApplicationEndpoint
    config: ApplicationConfig

    @classmethod
    def start(cls, config: ApplicationConfig) -> ApplicationProcess:
        """Start the test application and wait for its endpoint.

        Returns:
            The running application and its endpoint.

        Raises:
            AssertionError: If the application does not report an endpoint in time.

        """
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
            message = "the application did not report its endpoint"
            raise AssertionError(message)
        return _started_application(config, process, parent)

    def stop(self) -> int:
        """Send SIGTERM and wait for the application to stop.

        Returns:
            The process exit code.

        Raises:
            AssertionError: If the process requires forced termination.

        """
        if self.process.is_alive() and self.process.pid is not None:
            os.kill(self.process.pid, signal.SIGTERM)
            self.process.join(STOP_TIMEOUT_SECONDS)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(STOP_TIMEOUT_SECONDS)
            message = "the application did not stop after SIGTERM"
            raise AssertionError(message)
        return int(self.process.exitcode or 0)

    def restart(self) -> tuple[int, int]:
        """Replace the application process on the same endpoint and data.

        Returns:
            The old and new process identities, in that order.

        Raises:
            AssertionError: If an identity is absent, shutdown fails, or replacement fails.

        """
        before = self.process.pid
        if before is None:
            message = "the application process has no process id"
            raise AssertionError(message)
        exit_code = self.stop()
        if exit_code != 0:
            message = f"the application exited with {exit_code}"
            raise AssertionError(message)
        self.messages.close()
        replacement = self.start(replace(self.config, port=self.endpoint.port))
        after = replacement.process.pid
        if after is None or after == before:
            replacement.stop()
            message = "the application process was not replaced"
            raise AssertionError(message)
        self.process = replacement.process
        self.messages = replacement.messages
        self.endpoint = replacement.endpoint
        self.config = replacement.config
        return before, after
