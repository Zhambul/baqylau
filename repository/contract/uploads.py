"""Attachments the browser staged for a composer.

The bytes live on disk because the harness is handed an `@path`; this is the
record of them, which is what makes the directory prunable and a stray file
attributable.
"""

from __future__ import annotations

from typing import Protocol

from domain.uploads import StoredUpload


class UploadRepository(Protocol):
    def record(self, stored_upload: StoredUpload) -> None: ...

    def remove_expired(self, created_before: float) -> tuple[StoredUpload, ...]:
        """Drop rows older than the cutoff and return them, so the caller can
        unlink the files. A repository does not touch the filesystem."""
        ...
