"""Filesystem locations the dashboard owns.

The directories themselves belong to `core/data.py` — the one owner of where
our files live. What is dashboard-specific is which files hang off them: the
singleton lock the daemon holds, the preferences database, and the uploads a
browser attaches to a session.

Resolved once, at import: the test suite substitutes these attributes to keep a
run out of your real data directory.
"""

from __future__ import annotations

import os
import re

from core.data import data_directory, runtime_directory

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIRECTORY = os.path.join(REPOSITORY_ROOT, "bin")

DASHBOARD_LOCK_DATABASE = os.path.join(runtime_directory(), "baqylau-dashboard.db")
DASHBOARD_PREFERENCES_DATABASE = os.path.join(data_directory(), "dashboard-preferences.db")
UPLOADS_DIRECTORY = os.path.join(data_directory(), "uploads")


def safe_session_name(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", session_id)


def session_uploads_directory(session_id: str) -> str:
    name = safe_session_name(session_id.strip()) or "staging"
    return os.path.join(UPLOADS_DIRECTORY, name)
