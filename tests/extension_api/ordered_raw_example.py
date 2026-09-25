# Copyright (c) 2026 Zhambyl Yermagambet
"""Change recorded bytes through SDK-only raw transform operations."""

import os
import time
from dataclasses import dataclass

from baqylau_extension_api.contracts.processing import ExtensionRawTransformer
from baqylau_extension_api.models import content, documents, events, raw_transforms, transforms

from tests.extension_api import ordered_transform_operations as operations

# Longer than any test's call deadline: the host must end the call.
HANG_SECONDS = 3600


@dataclass(frozen=True)
class OrderedRaw(ExtensionRawTransformer):
    """Use fixture identities to make order and process loss observable."""

    def transform(self, raw_request: transforms.RawTransformRequest) -> raw_transforms.RawTransformResult:
        """Replace selected content and add a stable input in the first worker.

        Returns:
            A complete reply, including deliberately invalid source bytes for one case.

        """
        first = raw_request.context.extension_id == operations.FIRST_OWNER
        encoded = operations.raw_content(raw_request, raw_request.inputs[0])
        if first and operations.decode_text(encoded).startswith("hang"):
            time.sleep(HANG_SECONDS)
        if first and operations.decode_text(encoded).startswith("raw-drop"):
            return raw_transforms.RawTransformResult(operations=tuple(
                transforms.Drop(input_id=source.input_id, reason="Fixture raw suppression")
                for source in raw_request.inputs
            ))
        return self._changed(raw_request, first=first)

    def _changed(
        self, request: transforms.RawTransformRequest, *, first: bool,
    ) -> raw_transforms.RawTransformResult:
        replacements = tuple(
            self._replacement(request, source, first=first) for source in request.inputs
        )
        changes: tuple[transforms.TransformOperation[events.RawInput], ...] = tuple(
            transforms.Replace(
                input_id=source.input_id, document=source.model_copy(update={"content": blob.reference}),
            )
            for source, blob in zip(request.inputs, replacements, strict=True)
        )
        if first:
            changes += (operations.raw_addition(request.inputs[0]),)
        return raw_transforms.RawTransformResult(
            operations=changes, content_snapshot=content.ContentBundle(blobs=replacements),
            diagnostics=(documents.Diagnostic(code="fixture.process", message=str(os.getpid())),),
        )

    def _replacement(
        self, request: transforms.RawTransformRequest, source: events.RawInput, *, first: bool,
    ) -> content.ContentBlob:
        encoded = operations.raw_content(request, source)
        suffix = "raw-first" if first else "raw-second"
        invalid = operations.decode_text(encoded).startswith("bad-raw")
        if not first and invalid and source == request.inputs[-1]:
            return content.encode_content(b"42", "application/json")
        return content.encode_content(operations.append_text(encoded, suffix).encode("utf-8"), "application/json")
