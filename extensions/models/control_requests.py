# Copyright (c) 2026 Zhambyl Yermagambet
"""Pin shared management revisions without exposing manager authority."""

from baqylau_extension_api.models.base import Revision, WireModel


class ControlRevisions(WireModel):
    """Name the state a client read before requesting a change."""

    expected_revision: Revision
    expected_catalog_revision: Revision
