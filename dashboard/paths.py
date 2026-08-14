"""Filesystem locations owned by the dashboard application."""

from __future__ import annotations

import os
import re

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_DIRECTORY = os.path.join(REPOSITORY_ROOT, "bin")
RUNTIME_DIRECTORY = os.environ.get("BAQYLAU_RUNTIME_DIRECTORY") or "/tmp"
DATA_DIRECTORY = os.environ.get("BAQYLAU_DATA_DIRECTORY") or os.path.expanduser(
    "~/.local/share/baqylau"
)

DASHBOARD_LOCK_DATABASE = os.path.join(RUNTIME_DIRECTORY, "baqylau-dashboard.db")
DASHBOARD_PREFERENCES_DATABASE = os.path.join(DATA_DIRECTORY, "dashboard-preferences.db")
UPLOADS_DIRECTORY = os.path.join(DATA_DIRECTORY, "uploads")


def safe_session_name(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", session_id)


def session_uploads_directory(session_id: str) -> str:
    name = safe_session_name(session_id.strip()) or "staging"
    return os.path.join(UPLOADS_DIRECTORY, name)
