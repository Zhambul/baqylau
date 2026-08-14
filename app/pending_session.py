"""Transient binding from a launched terminal session to its canonical identity."""

from __future__ import annotations

import os
import time

from app.data import data_directory
from domain.ids import SessionId

PENDING_PREFIX = "pending-"
POLL_SECONDS = 0.05


def identity(native_process_id: int) -> SessionId:
    return SessionId(f"{PENDING_PREFIX}{native_process_id}")


def is_pending(session_id: SessionId) -> bool:
    return str(session_id).startswith(PENDING_PREFIX)


def _binding_path(pending_session_id: SessionId) -> str:
    if not is_pending(pending_session_id):
        raise ValueError("pending session identity is required")
    return os.path.join(
        data_directory(),
        "pending-sessions",
        f"{pending_session_id}.session",
    )


def bind(pending_session_id: SessionId, session_id: SessionId) -> None:
    path = _binding_path(pending_session_id)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary_path = f"{path}.{os.getpid()}"
    with open(temporary_path, "w", encoding="utf-8") as binding:
        binding.write(str(session_id))
    os.replace(temporary_path, path)


def wait(pending_session_id: SessionId) -> SessionId:
    path = _binding_path(pending_session_id)
    while True:
        try:
            with open(path, encoding="utf-8") as binding:
                session_id = binding.read().strip()
        except FileNotFoundError:
            session_id = ""
        if session_id:
            return SessionId(session_id)
        time.sleep(POLL_SECONDS)


def clear(pending_session_id: SessionId) -> None:
    try:
        os.unlink(_binding_path(pending_session_id))
    except FileNotFoundError:
        pass
