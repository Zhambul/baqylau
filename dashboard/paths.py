"""Filesystem locations the dashboard owns.

The directories themselves belong to `core/data.py` — the one owner of where
our files live, and now of the three database paths too. What is left here is
the uploads directory: the one place the dashboard writes bytes rather than
rows, because an attachment reaches the harness as an `@path`.

Resolved once, at import: the test suite substitutes these attributes to keep a
run out of your real data directory.
"""

from __future__ import annotations

import os
import re

from core.clients import REPOSITORY_ROOT
from core.data import data_directory

# The daemon re-spawns itself through `bin/`. The root it hangs off is resolved
# once, in core/clients.py, from a package's own location — this module used to
# count two directories up from itself, which is the mistake that once killed
# every pane process on startup.
BIN_DIRECTORY = os.path.join(str(REPOSITORY_ROOT), "bin")

UPLOADS_DIRECTORY = os.path.join(data_directory(), "uploads")


def safe_session_name(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", session_id)


def session_uploads_directory(session_id: str) -> str:
    name = safe_session_name(session_id.strip()) or "staging"
    return os.path.join(UPLOADS_DIRECTORY, name)
