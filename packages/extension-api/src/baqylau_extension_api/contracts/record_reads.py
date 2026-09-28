# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the calling package's own stored records through the host."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from baqylau_extension_api.models.record_reads import RecordPageReply, RecordPageRequest


class ExtensionRecordReader(Protocol):
    """Read committed records of a declared collection; another package's records are not visible."""

    def read_records(self, record_page_request: RecordPageRequest) -> RecordPageReply:
        """Read one ordered page."""
        ...
