# Copyright (c) 2026 Zhambyl Yermagambet
"""Run a core-aware raw or canonical decision inside an isolated worker process.

The host copies this module into an external fixture package, so it imports the
SDK and the standard library only. The package owner selects the behavior:
`test.lifecycle-drop`, `test.lifecycle-replace`, `test.lifecycle-fail`,
`test.lifecycle-invalid`, `test.lifecycle-rawdrop`, `test.lifecycle-rawreplace`,
or `test.lifecycle-rawinsert`.
"""

import hashlib
import json
import time
from dataclasses import dataclass

from baqylau_extension_api import identities
from baqylau_extension_api.contracts import (
    lifecycle as lifecycle_contract,
    plugin,
    processing,
    services as host_services,
)
from baqylau_extension_api.models import canonical, content, documents, events, lifecycle, raw_transforms, transforms

BEHAVIOR_SEPARATOR = "-"
DROP_BEHAVIOR = "drop"
REPLACE_BEHAVIOR = "replace"
FAIL_BEHAVIOR = "fail"
INVALID_BEHAVIOR = "invalid"
SLOW_BEHAVIOR = "slow"
SLOW_SECONDS = 3.0
RAW_DROP_BEHAVIOR = "rawdrop"
RAW_REPLACE_BEHAVIOR = "rawreplace"
RAW_INSERT_BEHAVIOR = "rawinsert"
RAW_BEHAVIOR_PREFIX = "raw"
MISSING_CAUSE = "missing-fixture-cause"
EVENT_SUFFIX = ".fact"
SCHEMA_TEXT = '{"type":"string"}'
SCHEMA_NAME = "text"
SCHEMA_VERSION = 1
EXTRA_TEXT = '"lifecycle extra"'
EXTRA_KEY = "lifecycle-extra"
DROP_REASON = "Fixture activity suppression"
REPLACED_OUTCOME = "failed"
RAW_DROP_REASON = "Fixture raw suppression"
RAW_INSERT_KEY = "raw-extra"
RAW_REPLACE_FIELD = "last_assistant_message"
RAW_REPLACE_TEXT = "fixture raw replacement"
JSON_MEDIA_TYPE = "application/json"
ENCODING = "utf-8"


def behavior_of(owner: str) -> str:
    """Return the fixture behavior selected by one package owner.

    Returns:
        The behavior name after the last separator.

    """
    return owner.rsplit(BEHAVIOR_SEPARATOR, 1)[-1]


def schema_reference(owner: str) -> documents.SchemaRef:
    """Return the exact declared schema identity for one fixture owner.

    Returns:
        The content-addressed text schema.

    """
    digest = hashlib.sha256(SCHEMA_TEXT.encode("utf-8")).hexdigest()
    return documents.SchemaRef(owner=owner, name=SCHEMA_NAME, version=SCHEMA_VERSION, digest=digest)


@dataclass(frozen=True)
class LifecycleCanonical(processing.ExtensionCanonicalTransformer):
    """Record one operation set selected by the package owner."""

    def transform(
        self, canonical_request: transforms.CanonicalTransformRequest,
    ) -> transforms.CanonicalTransformResult:
        """Return the complete reply for the owner's behavior.

        Returns:
            One reply for every input in its original order.

        Raises:
            RuntimeError: If the owner selects the failing behavior.

        """
        behavior = behavior_of(canonical_request.context.extension_id)
        if behavior == FAIL_BEHAVIOR:
            message = "fixture canonical failure"
            raise RuntimeError(message)
        if behavior == SLOW_BEHAVIOR:
            time.sleep(SLOW_SECONDS)
        if behavior == DROP_BEHAVIOR:
            return transforms.CanonicalTransformResult(operations=tuple(
                transforms.Drop(input_id=fact.event_id, reason=DROP_REASON)
                for fact in canonical_request.inputs
            ))
        if behavior == INVALID_BEHAVIOR:
            return self._invalid(canonical_request)
        operations: tuple[transforms.TransformOperation[canonical.CanonicalFact], ...] = tuple(
            self._replacement(fact) if behavior == REPLACE_BEHAVIOR else transforms.Keep(input_id=fact.event_id)
            for fact in canonical_request.inputs
        )
        if behavior == REPLACE_BEHAVIOR and canonical_request.inputs:
            operations += (self._addition(canonical_request),)
        return transforms.CanonicalTransformResult(operations=operations)

    def _replacement(
        self, fact: canonical.CanonicalFact,
    ) -> transforms.TransformOperation[canonical.CanonicalFact]:
        if fact.kind != "core" or fact.payload.kind != "turn.finished":
            return transforms.Keep(input_id=fact.event_id)
        changed = fact.payload.model_copy(update={"outcome": REPLACED_OUTCOME})
        document = fact.model_copy(update={"payload": changed})
        return transforms.Replace(input_id=fact.event_id, document=document)

    def _invalid(
        self, canonical_request: transforms.CanonicalTransformRequest,
    ) -> transforms.CanonicalTransformResult:
        addition = self._addition(canonical_request)
        assert isinstance(addition, transforms.Insert)
        document = addition.document
        assert isinstance(document, events.ExtensionFact)
        changed = document.model_copy(update={"causes": (*document.causes, MISSING_CAUSE)})
        changed_addition = addition.model_copy(update={"document": changed})
        operations = (*self._keeps(canonical_request), changed_addition)
        return transforms.CanonicalTransformResult(operations=operations)

    def _keeps(
        self, canonical_request: transforms.CanonicalTransformRequest,
    ) -> tuple[transforms.TransformOperation[canonical.CanonicalFact], ...]:
        return tuple(transforms.Keep(input_id=fact.event_id) for fact in canonical_request.inputs)

    def _addition(
        self, canonical_request: transforms.CanonicalTransformRequest,
    ) -> transforms.TransformOperation[canonical.CanonicalFact]:
        anchor = canonical_request.inputs[0]
        owner = canonical_request.context.extension_id
        identity = identities.DerivedIdentity(
            extension_id=owner, input_id=anchor.event_id, output_key=EXTRA_KEY,
        )
        return transforms.Insert(
            input_id=anchor.event_id,
            output_key=EXTRA_KEY,
            position="after",
            document=events.ExtensionFact(
                event_id=identities.derived_event_id(identity),
                scope=anchor.scope,
                event_type=f"{owner}{EVENT_SUFFIX}",
                document=documents.EncodedDocument(
                    schema_ref=schema_reference(canonical_request.context.extension_id), json_text=EXTRA_TEXT,
                ),
                causes=(anchor.event_id,),
            ),
        )


@dataclass(frozen=True)
class LifecycleRaw(processing.ExtensionRawTransformer):
    """Change recorded bytes without reading the harness's own records."""

    def transform(
        self, raw_request: transforms.RawTransformRequest,
    ) -> raw_transforms.RawTransformResult:
        """Return the complete raw reply for the owner's behavior.

        Returns:
            One reply for every selected input in its original order.

        """
        behavior = behavior_of(raw_request.context.extension_id)
        if behavior == RAW_DROP_BEHAVIOR:
            return raw_transforms.RawTransformResult(operations=tuple(
                transforms.Drop(input_id=source.input_id, reason=RAW_DROP_REASON)
                for source in raw_request.inputs
            ))
        if behavior == RAW_REPLACE_BEHAVIOR:
            return self._replaced(raw_request)
        if behavior == RAW_INSERT_BEHAVIOR:
            return self._inserted(raw_request)
        return raw_transforms.RawTransformResult(operations=tuple(
            transforms.Keep(input_id=source.input_id) for source in raw_request.inputs
        ))

    def _replaced(
        self, raw_request: transforms.RawTransformRequest,
    ) -> raw_transforms.RawTransformResult:
        blobs = tuple(
            content.encode_content(self._changed(raw_request, source), JSON_MEDIA_TYPE)
            for source in raw_request.inputs
        )
        operations = tuple(
            transforms.Replace(
                input_id=source.input_id, document=source.model_copy(update={"content": blob.reference}),
            )
            for source, blob in zip(raw_request.inputs, blobs, strict=True)
        )
        return raw_transforms.RawTransformResult(
            operations=operations, content_snapshot=content.ContentBundle(blobs=blobs),
        )

    def _inserted(
        self, raw_request: transforms.RawTransformRequest,
    ) -> raw_transforms.RawTransformResult:
        anchor = raw_request.inputs[0]
        identity = identities.DerivedIdentity(
            extension_id=raw_request.context.extension_id, input_id=anchor.input_id, output_key=RAW_INSERT_KEY,
        )
        addition = transforms.Insert(
            input_id=anchor.input_id,
            output_key=RAW_INSERT_KEY,
            position="after",
            document=anchor.model_copy(update={"input_id": identities.derived_input_id(identity)}),
        )
        keeps = tuple(transforms.Keep(input_id=source.input_id) for source in raw_request.inputs)
        return raw_transforms.RawTransformResult(operations=(*keeps, addition))

    def _changed(self, raw_request: transforms.RawTransformRequest, source: events.RawInput) -> bytes:
        blob = raw_request.content_snapshot.resolve(source.content)
        document = json.loads(content.decode_base64(blob.base64_text).decode(ENCODING))
        document[RAW_REPLACE_FIELD] = RAW_REPLACE_TEXT
        return json.dumps(document).encode(ENCODING)


@dataclass(frozen=True)
class Lifecycle(plugin.ExtensionPlugin):
    """Confirm lifecycle requests for the fixture package."""

    selected: plugin.ExtensionCapabilities
    identity: lifecycle.ExtensionInfo

    @property
    def extension_info(self) -> lifecycle.ExtensionInfo:
        """The fixture's package and API identity."""
        return self.identity

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The capabilities selected for this fixture instance."""
        return self.selected


@dataclass(frozen=True)
class ConfirmLifecycle(lifecycle_contract.ExtensionLifecycle):
    """Confirm activation and release without leaving pending work."""

    def activate(self, request: lifecycle.ActivationRequest) -> lifecycle.ActivationResult:
        """Confirm readiness for the requested revision.

        Returns:
            The same runtime revision.

        """
        return lifecycle.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle.DeactivationRequest) -> lifecycle.DeactivationResult:
        """Release the fixture without leaving pending work.

        Returns:
            A complete deactivation result.

        """
        return lifecycle.DeactivationResult(runtime_revision=request.runtime_revision)


def build_extension(services: host_services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Build the fixture plugin with the capabilities its owner declares.

    Returns:
        A plugin which records only its selected decisions.

    """
    owner = services.environment.extension_info.extension_id
    raw = LifecycleRaw() if behavior_of(owner).startswith(RAW_BEHAVIOR_PREFIX) else None
    return Lifecycle(
        plugin.ExtensionCapabilities(
            lifecycle=ConfirmLifecycle(),
            raw_transformer=raw,
            canonical_transformer=LifecycleCanonical(),
        ),
        services.environment.extension_info,
    )


FACTORY: plugin.ExtensionFactory = build_extension
