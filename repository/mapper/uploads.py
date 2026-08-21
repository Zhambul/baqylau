"""Row DTO to a stored attachment."""

from __future__ import annotations

from domain.ids import SessionId
from domain.uploads import StoredUpload
from repository.model.uploads import UploadRow
from repository.model.sql import SqlValues


def stored_upload(upload_row: UploadRow) -> StoredUpload:
    return StoredUpload(
        upload_id=upload_row.upload_id,
        session_id=SessionId(upload_row.session_id) if upload_row.session_id else None,
        name=upload_row.name,
        media_type=upload_row.media_type,
        byte_size=upload_row.byte_size,
        stored_path=upload_row.stored_path,
        created_at=upload_row.created_at,
    )


def upload_values(stored_upload: StoredUpload) -> SqlValues:
    return (
        stored_upload.upload_id,
        str(stored_upload.session_id) if stored_upload.session_id else "",
        stored_upload.name,
        stored_upload.media_type,
        stored_upload.byte_size,
        stored_upload.stored_path,
        stored_upload.created_at,
    )
