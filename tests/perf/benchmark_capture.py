# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the fixed capture of native Claude Code hooks that every profile receives."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from tests.extension_host import lifecycle_daemon_fixture as fixture

if TYPE_CHECKING:
    from harness.impl.claude_code.canonical.records import HookPayload

SMALL_MESSAGE_BYTES = 64
KIBIBYTE = 1024
LARGE_MESSAGE_KIBIBYTES = 256
LARGE_MESSAGE_BYTES = LARGE_MESSAGE_KIBIBYTES * KIBIBYTE


def capture(events: int, message_bytes: int = SMALL_MESSAGE_BYTES) -> tuple[bytes, ...]:
    """Build one session start, then finished turns with an assistant message.

    Returns:
        The encoded hooks.

    """
    text = "x" * message_bytes
    hooks = [fixture.hook("SessionStart", "start-0")]
    hooks.extend(finished_turn(index, text) for index in range(events))
    return tuple(hook.model_dump_json().encode() for hook in hooks)


def finished_turn(index: int, text: str) -> HookPayload:
    """Build one finished turn with a numbered message.

    Returns:
        The hook.

    """
    message = f"{index} {text}"
    return fixture.hook("Stop", f"stop-{index}").model_copy(update={"last_assistant_message": message})


def digest(hooks: tuple[bytes, ...]) -> str:
    """Name the capture by its bytes.

    Returns:
        The SHA-256 digest.

    """
    return hashlib.sha256(b"\n".join(hooks)).hexdigest()
