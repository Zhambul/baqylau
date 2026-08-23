"""The process boundary for one configured application runtime."""

from __future__ import annotations

import multiprocessing
import os
import signal
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess

from api.runtime import ApplicationConfig, ApplicationEndpoint, DashboardApplication

START_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 15.0


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
            return cls(process=process, messages=parent, endpoint=first)
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
