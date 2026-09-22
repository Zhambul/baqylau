# Copyright (c) 2026 Zhambyl Yermagambet
"""Return raw operations with the new immutable content they need."""

from baqylau_extension_api.models.content import ContentBundle
from baqylau_extension_api.models.events import RawInput
from baqylau_extension_api.models.transforms import TransformResult


class RawTransformResult(TransformResult[RawInput]):
    """Supply changed bytes without writing source storage or host files."""

    content_snapshot: ContentBundle = ContentBundle()
