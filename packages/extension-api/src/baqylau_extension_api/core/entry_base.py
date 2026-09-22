# Copyright (c) 2026 Zhambyl Yermagambet
"""Tag each closed core feed body without changing its stored fields."""

from baqylau_extension_api.core.base import CoreModel


class CoreEntryBodyModel(CoreModel):
    """Require each body to declare its exact feed kind."""

    kind: str
