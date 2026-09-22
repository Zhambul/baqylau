# Copyright (c) 2026 Zhambyl Yermagambet
"""Use separate canonical workers to check full-result acceptance and failure."""

import os
from dataclasses import dataclass

from baqylau_extension_api.contracts.processing import ExtensionCanonicalTransformer
from baqylau_extension_api.models import canonical, transforms

from tests.extension_api import ordered_transform_operations as operations


@dataclass(frozen=True)
class OrderedCanonical(ExtensionCanonicalTransformer):
    """Make every stage visible in the final string document."""

    def transform(
        self, canonical_request: transforms.CanonicalTransformRequest,
    ) -> transforms.CanonicalTransformResult:
        """Return replacements plus a valid or deliberately invalid addition.

        Returns:
            One whole reply, unless the selected fixture process exits.

        """
        first = canonical_request.context.extension_id == operations.FIRST_OWNER
        fact = canonical_request.inputs[0]
        assert fact.kind == "extension"
        text = operations.decode_text(fact.document.json_text)
        if first and text.startswith("crash"):
            os._exit(7)
        if first and text.startswith("canonical-drop"):
            return transforms.CanonicalTransformResult(operations=tuple(
                transforms.Drop(input_id=selected.event_id, reason="Fixture canonical suppression")
                for selected in canonical_request.inputs
            ))
        return self._changed(canonical_request, first=first, invalid=text.startswith("bad-fact"))

    def _changed(
        self, request: transforms.CanonicalTransformRequest, *, first: bool, invalid: bool,
    ) -> transforms.CanonicalTransformResult:
        changes: tuple[transforms.TransformOperation[canonical.CanonicalFact], ...] = tuple(
            self._replacement(fact, "canonical-first" if first else "canonical-second") for fact in request.inputs
        )
        if first:
            changes += (operations.fact_addition(request.inputs[0], request.context.extension_id),)
        elif invalid:
            addition = operations.fact_addition(request.inputs[0], request.context.extension_id)
            assert addition.document.kind == "extension"
            document = addition.document.model_copy(update={
                "causes": (*addition.document.causes, "missing-fixture-cause"),
            })
            changes += (addition.model_copy(update={"document": document}),)
        return transforms.CanonicalTransformResult(operations=changes)

    def _replacement(
        self, fact: canonical.CanonicalFact, suffix: str,
    ) -> transforms.Replace[canonical.CanonicalFact]:
        assert fact.kind == "extension"
        document = fact.document.model_copy(update={
            "json_text": operations.append_text(fact.document.json_text, suffix),
        })
        return transforms.Replace(
            input_id=fact.event_id, document=fact.model_copy(update={"document": document}),
        )
