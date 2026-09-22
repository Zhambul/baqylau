# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose one accepted fact boundary and explicit decoder-state identity."""

from baqylau_extension_api.models import base, scopes

from extensions.models.interpretations import StoredCanonicalFact


class CanonicalPage(base.WireModel):
    """Return a bounded page and the history's accepted cursor in one read transaction.

    A consumer advances to its last returned row. An empty live stream page
    can advance to the head. Scope pages do not imply a global consumer checkpoint.
    A content budget can reduce the returned count. An oversized first fact is
    returned alone; a content limit cannot hide all remaining facts.
    """

    history_revision: base.Identifier
    head: base.Revision
    facts: tuple[StoredCanonicalFact, ...]


class TranslationStateKey(base.WireModel):
    """Name one owner's decoder state for an exact history, scope, and source."""

    extension_id: base.ExtensionId
    history_revision: base.Identifier
    scope: scopes.ExtensionScope
    source_identity: base.OpaqueId
