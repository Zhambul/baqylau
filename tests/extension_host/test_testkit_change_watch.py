# Copyright (c) 2026 Zhambyl Yermagambet
"""The kit's change watch opens a real host stream and closes it at the end of its block (P10-T02)."""

from __future__ import annotations

import time
from contextlib import ExitStack
from typing import TYPE_CHECKING

from baqylau_extension_testkit.changes import watching
from baqylau_extension_testkit.isolation import PrivateRoots

from tests.extension_host import testkit_host_fixture as hosts

if TYPE_CHECKING:
    from pathlib import Path

INSTALLATION = '{"kind":"installation"}'
CLOSE_SECONDS = 10.0


def test_watch_opens_and_closes(tmp_path: Path) -> None:
    """A watch with no record changes counts no frame, and its block ends without waiting for a frame."""
    roots = PrivateRoots(tmp_path / "host")
    roots.create()
    with ExitStack() as cleanup:
        host = hosts.started(cleanup, roots)
        started = time.monotonic()
        with watching(host.client, "test.none", INSTALLATION) as watch:
            assert watch.opened.is_set()
            assert watch.count() == 0

        assert time.monotonic() - started < CLOSE_SECONDS
