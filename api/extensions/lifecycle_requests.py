# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept JSON arrays at HTTP while preserving strict lifecycle field values."""

from typing import Annotated

from baqylau_extension_api.models.base import ExtensionId
from pydantic import Field

from extensions.models.lifecycle_requests import LifecyclePlanRequest, LifecycleRequest


class LifecyclePreviewRequest(LifecyclePlanRequest):
    """Publish the checked preview fields as an HTTP request contract."""


class LifecycleChangeRequest(LifecycleRequest):
    """Convert only the JSON array container; owner IDs and other fields stay strict."""

    confirmed_dependents: Annotated[tuple[ExtensionId, ...], Field(max_length=1000, strict=False)] = ()
