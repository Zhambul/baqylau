# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe public model and account references."""

from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.models.base import OpaqueId


class ModelReference(CoreModel):
    """Keep a model's portable name and optional display name."""

    name: str
    display_name: str | None


class AccountReference(CoreModel):
    """Keep an account identity without an adapter handle."""

    account_id: OpaqueId
    display_name: str
