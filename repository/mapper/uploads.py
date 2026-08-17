"""Row DTO to a stored attachment."""

from __future__ import annotations

from domain.ids import SessionId
from domain.uploads import StoredUpload
from repository.model.uploads import UploadRow


def stored_upload(row: UploadRow) -> StoredUpload:
    return StoredUpload(
        upload_id=row.upload_id,
        session_id=SessionId(row.session_id) if row.session_id else None,
        name=row.name,
        media_type=row.media_type,
        byte_size=row.byte_size,
        stored_path=row.stored_path,
        created_at=row.created_at,
    )


def upload_values(upload: StoredUpload) -> tuple[object, ...]:
    return (
        upload.upload_id,
        str(upload.session_id) if upload.session_id else "",
        upload.name,
        upload.media_type,
        upload.byte_size,
        upload.stored_path,
        upload.created_at,
    )
