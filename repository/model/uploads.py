"""Row shape for the uploads table."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UploadRow:
    upload_id: str
    session_id: str
    name: str
    media_type: str
    byte_size: int
    stored_path: str
    created_at: float
