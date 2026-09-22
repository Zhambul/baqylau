# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep canonical transform requests with referenced candidates and prior state."""

from baqylau_extension_api.models import transforms

from extensions.models.interpretation_bodies import (
    CANONICAL_FACT_KIND,
    PRIOR_STATE_KIND,
    BodyResolver,
    BodyStore,
)
from extensions.models.interpretation_storage_context import _load_context, _store_context
from extensions.models.interpretation_storage_requests import StoredCanonicalTransformRequest


def _store_canonical_request(
    request: transforms.CanonicalTransformRequest, store: BodyStore,
) -> StoredCanonicalTransformRequest:
    return StoredCanonicalTransformRequest(
        context=_store_context(request.context, store),
        inputs=tuple(store.intern(fact, CANONICAL_FACT_KIND) for fact in request.inputs),
        prior_state=store.intern(request.prior_state, PRIOR_STATE_KIND),
    )


def _load_canonical_request(
    request: StoredCanonicalTransformRequest, resolver: BodyResolver,
) -> transforms.CanonicalTransformRequest:
    return transforms.CanonicalTransformRequest(
        context=_load_context(request.context, resolver),
        inputs=tuple(resolver.resolve_fact(ref) for ref in request.inputs),
        prior_state=resolver.resolve_prior(request.prior_state),
    )
