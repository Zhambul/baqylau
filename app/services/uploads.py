"""Attachments the browser staged, and the prune that keeps them bounded.

The bytes are on disk because the harness is handed an `@path`; the row is what
makes them findable. This service owns the filesystem half — the repository
returns what it deleted and this unlinks it, because a repository does not
touch the filesystem.
"""

from __future__ import annotations

import os
import time
from typing import Callable

from diagnostics.models import ApplicationErrorRecord
from domain.uploads import StoredUpload
from repository.contract.diagnostics import DiagnosticWriteRepository
from repository.contract.uploads import UploadRepository

# An attachment is delivered into a composer within minutes of being staged.
# A week is generous, and bounds a directory that previously grew forever.
UPLOAD_LIFETIME_SECONDS = 7 * 24 * 60 * 60


class UploadService:
    def __init__(
        self,
        uploads: UploadRepository,
        audit: DiagnosticWriteRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.uploads = uploads
        self.audit = audit
        self.clock = clock

    def record(self, upload: StoredUpload) -> None:
        self.uploads.record(upload)

    def prune(self) -> int:
        """Drop expired rows and unlink their files. Returns how many went."""
        removed = self.uploads.remove_expired(self.clock() - UPLOAD_LIFETIME_SECONDS)
        for upload in removed:
            try:
                os.remove(upload.stored_path)
            except FileNotFoundError:
                continue
            except OSError as error:
                if self.audit is not None:
                    self.audit.record_error(
                        ApplicationErrorRecord(
                            session_id=str(upload.session_id or ""),
                            script="dashboard",
                            function="upload prune",
                            traceback="",
                            context=f"{upload.stored_path}: {error}",
                            process_id=os.getpid(),
                            timestamp=self.clock(),
                        )
                    )
        return len(removed)
