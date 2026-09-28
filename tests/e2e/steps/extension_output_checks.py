# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check the output that an extension package keeps from a live shell.

Claude Code follows a foreground command's output from a file, so most of it
arrives after the shell finished. A call that owns its output must keep it.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

from sdk.client import wait_for
from tests.e2e.testkit import extension_queries as reads

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tests.e2e.testkit.extension_context import ExtensionContext


def _output_kept(context: ExtensionContext, scope: Mapping[str, str], group: str) -> bool | None:
    return True if reads.kept_output_lines(context.host, scope, group) else None


@then(parsers.parse('the adapters "{group}" call in session "{session_name}" keeps its output'))
def adapters_call_keeps_output(extension_context: ExtensionContext, group: str, session_name: str) -> None:
    """Wait for the call that owns the shell's output to keep at least one line of it."""
    kept = partial(_output_kept, extension_context, extension_context.session_scope(session_name), group)
    wait_for(f"the output of the adapters {group!r} call", kept, timeout=extension_context.wait_policy.feed)
