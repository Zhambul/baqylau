"""Row shape for the uploads table."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import SessionId, UploadId


@dataclass(frozen=True)
class UploadRow:
    upload_id: UploadId
    session_id: SessionId
    name: str
    media_type: str
    byte_size: int
    stored_path: str
    created_at: float
