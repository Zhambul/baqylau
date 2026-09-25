# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one ordered page of the calling package's own record collection."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.records import RecordState
from baqylau_extension_api.models.scopes import ExtensionScope

MAX_RECORD_PAGE = 500


class RecordPageRequest(WireModel):
    """Select one declared collection, one exact scope, and the key after which the page starts."""

    collection: Identifier
    scope: ExtensionScope
    after_key: str = ""
    limit: Annotated[int, Field(ge=1, le=MAX_RECORD_PAGE)] = 100


class RecordPageReply(WireModel):
    """Return the page in key order and the key that starts the next page."""

    records: Annotated[tuple[RecordState, ...], Field(max_length=MAX_RECORD_PAGE)]
    next_key: str | None = None
