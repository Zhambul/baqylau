# Copyright (c) 2026 Zhambyl Yermagambet
"""Drain preparation output with one shared byte limit."""

from dataclasses import dataclass, field

from anyio.abc import ByteReceiveStream


class PreparationOutputLimitError(RuntimeError):
    """The owned subprocess exceeded its combined output budget."""


@dataclass
class PreparationBuffers:
    """Keep both streams bounded without losing either stream to backpressure."""

    limit: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)

    async def drain(self, stream: ByteReceiveStream, destination: bytearray) -> None:
        """Read one stream while its peer is drained by the same task group.

        Raises:
            PreparationOutputLimitError: If the next bytes exceed the shared budget.

        """
        async for chunk in stream:
            captured = len(self.stdout) + len(self.stderr)
            if captured + len(chunk) > self.limit:
                message = "extension preparation exceeded its output limit"
                raise PreparationOutputLimitError(message)
            destination.extend(chunk)
