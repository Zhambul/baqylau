# Copyright (c) 2026 Zhambyl Yermagambet
"""Inspect only workers and storage owned by a private daemon test."""

import os
from pathlib import Path

import psutil

from extensions.models import cleanup, interpretation_steps as steps
from sdk.client import BaqylauClient
from tests.extension_api import shutdown_example
from tests.extension_host import (
    lifecycle_fixture,
    lifecycle_http_fixture,
    ordered_processing_fixture as ordered,
    package_fixture,
)

WORKERS = 3


def configure(directory: Path, mode: str) -> None:
    """Replace one external fixture's backend before the daemon captures its source."""
    source = directory / "packages" / ordered.OWNERS[0]
    original = package_fixture.read_manifest(source)
    assert original.backend is not None
    backend = original.backend.model_copy(update={"module": "ordered_feature.shutdown_example"})
    package_fixture.save_manifest(source, original.model_copy(update={"backend": backend}))
    code = Path(shutdown_example.__file__).read_text(encoding="utf-8")
    code = code.replace("from tests.extension_api.", "from ordered_feature.")
    code = code.replace('SHUTDOWN_MODE = "jobs"', f"SHUTDOWN_MODE = {mode!r}")
    package_fixture.write_file(source, "ordered_feature/shutdown_example.py", code.encode("utf-8"))


def worker_processes(directory: Path, expected: int = WORKERS) -> tuple[psutil.Process, ...]:
    """Capture exact child process identities while all active workers are ready.

    Returns:
        Workers with a command path inside this test's private directory.

    """
    selected = tuple(
        child for child in psutil.Process(os.getpid()).children(recursive=True)
        if _worker_in_directory(child, directory)
    )
    assert len(selected) == expected
    return selected


def enable_fault(client: BaqylauClient) -> None:
    """Select one fault worker; the separate crash regression covers three-worker cleanup."""
    owner = ordered.OWNERS[0]
    request = lifecycle_http_fixture.lifecycle_request(client, owner, "enable", "enable-fault")
    admitted = client.extensions.lifecycle.change(owner, request)
    operation = lifecycle_http_fixture.wait_operation(client, admitted.operation.operation_id)
    assert operation.status == "succeeded", operation


def require_closed(directory: Path, owned: tuple[psutil.Process, ...]) -> cleanup.ShutdownRecord:
    """Check physical closure before reading retained uncertainty from a fresh store.

    Returns:
        The final stored observation after normal daemon exit.

    """
    assert all(not worker.is_running() for worker in owned)
    assert not tuple((directory / "extension-environments").iterdir())
    record = lifecycle_fixture.repository(directory).read_extension_lifecycle().last_shutdown
    assert record is not None and all(runtime.resources_closed for runtime in record.runtimes)
    return record


def require_crashed_worker(step: steps.InterpretationStep) -> None:
    """Keep the original process-exit assertion from the ordered processing regression."""
    assert isinstance(step, steps.RawTransformStep) and isinstance(step.outcome, steps.AppliedStep)
    reply = step.outcome.reply
    assert not psutil.pid_exists(int(reply.diagnostics[0].message))


def require_owner_issue(record: cleanup.ShutdownRecord, mode: str) -> None:
    """Require evidence for the exact worker with the selected shutdown fault."""
    issues = tuple(issue for runtime in record.runtimes for issue in runtime.issues)
    owner = ordered.OWNERS[0]
    issue = next(issue for issue in issues if issue.extension_id == owner)
    if mode == "jobs":
        assert issue.reason == "unresolved_jobs" and issue.pending_job_ids == ("external-write",)
    else:
        assert issue.reason == "deactivation_failed" and not issue.pending_job_ids


def _worker_in_directory(process: psutil.Process, directory: Path) -> bool:
    try:
        command = process.cmdline()
    except psutil.NoSuchProcess:
        return False
    return ("baqylau_extension_api.runtime.worker" in command
            and any(Path(argument).is_relative_to(directory) for argument in command))
