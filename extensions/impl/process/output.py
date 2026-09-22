# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture bounded process logs without using stdout or stderr as a transport."""

from dataclasses import dataclass, field
from threading import Lock

from anyio.abc import ByteReceiveStream

from extensions.models.workers import WorkerDiagnostics, WorkerFailure


@dataclass
class WorkerOutput:
    """Share bounded byte and exit evidence with synchronous host callers."""

    process_id: int
    limit: int
    _stdout: bytearray = field(default_factory=bytearray)
    _stderr: bytearray = field(default_factory=bytearray)
    _failure: WorkerFailure | None = None
    _lock: Lock = field(default_factory=Lock)

    async def drain(self, stream: ByteReceiveStream, *, error: bool) -> None:
        """Drain one stream while a separate task drains its peer."""
        async for chunk in stream:
            self._append(chunk, error=error)

    def exited(self, *, expected: bool) -> None:
        """Record process exit without replacing an earlier failure reason."""
        with self._lock:
            if not expected and self._failure is None:
                self._failure = "process_exit"

    def load_failed(self) -> None:
        """Keep earlier process evidence when a load reply fails or times out."""
        with self._lock:
            if self._failure is None:
                self._failure = "load_failed"

    def transport_closed(self) -> None:
        """Record an unexpected socket close before forced process cleanup."""
        with self._lock:
            if self._failure is None:
                self._failure = "transport_closed"

    def snapshot(self, return_code: int | None) -> WorkerDiagnostics:
        """Read immutable evidence, including after the worker environment is closed.

        Returns:
            At most the selected combined byte budget.

        """
        with self._lock:
            return WorkerDiagnostics(
                self.process_id, return_code, bytes(self._stdout), bytes(self._stderr), self._failure,
            )

    def _append(self, chunk: bytes, *, error: bool) -> None:
        with self._lock:
            available = self.limit - len(self._stdout) - len(self._stderr)
            destination = self._stderr if error else self._stdout
            destination.extend(chunk[:available])
            if len(chunk) > available:
                self._failure = "output_limit"
                message = "extension worker exceeded its process output budget"
                raise WorkerOutputLimitError(message)


class WorkerOutputLimitError(RuntimeError):
    """The worker used its finite process output budget."""
