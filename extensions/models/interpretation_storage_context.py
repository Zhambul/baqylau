# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep one processing context with its settings document stored once."""

from typing import Literal

from baqylau_extension_api.models import base, events, scopes

from extensions.models.interpretation_bodies import (
    ENCODED_DOCUMENT_KIND,
    BodyRef,
    BodyResolver,
    BodyStore,
)


class StoredProcessingContext(base.WireModel):
    """Keep processing metadata with its settings document stored once."""

    extension_id: base.ExtensionId
    runtime_revision: base.Identifier
    history_revision: base.Identifier
    scope: scopes.ExtensionScope
    input_cursor: base.Revision
    settings_revision: base.Revision
    settings: BodyRef | None = None
    mode: Literal["live", "replay"] = "live"


def _store_context(context: events.ProcessingContext, store: BodyStore) -> StoredProcessingContext:
    return StoredProcessingContext(
        extension_id=context.extension_id, runtime_revision=context.runtime_revision,
        history_revision=context.history_revision, scope=context.scope, input_cursor=context.input_cursor,
        settings_revision=context.settings_revision,
        settings=None if context.settings is None else store.intern(context.settings, ENCODED_DOCUMENT_KIND),
        mode=context.mode,
    )


def _load_context(context: StoredProcessingContext, resolver: BodyResolver) -> events.ProcessingContext:
    return events.ProcessingContext(
        extension_id=context.extension_id, runtime_revision=context.runtime_revision,
        history_revision=context.history_revision, scope=context.scope, input_cursor=context.input_cursor,
        settings_revision=context.settings_revision,
        settings=None if context.settings is None else resolver.resolve_document(context.settings),
        mode=context.mode,
    )
