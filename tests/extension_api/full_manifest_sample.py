# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare a backend, settings, schemas, operations, and an external view."""

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.data import DocumentDefinition, ProcessingSelection, ScopeKinds
from baqylau_extension_api.manifest.e2e import E2eCase
from baqylau_extension_api.manifest.operations import CommandDefinition, PublicService, QueryDefinition
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.settings import SettingsDefinition

from tests.extension_api import manifest_samples, samples

SESSION_SCOPES: ScopeKinds = ("session",)


def full_manifest() -> ExtensionManifest:
    """Return a full data-only example with no private host imports.

    Returns:
        A package with checked cross-references in all selected contributions.

    """
    base = manifest_samples.backend_manifest()
    web = manifest_samples.web_manifest()
    return base.model_copy(update={
        "capabilities": ("lifecycle", "sources", "translator", "canonical_transformer", "queries", "commands"),
        "schemas": (samples.schema_definition(),),
        "settings": SettingsDefinition(defaults=samples.encoded_document(), scopes=("installation",)),
        "contributions": _contributions(), "assets": web.assets,
        "e2e": (E2eCase(case_id="full", path="tests/e2e/test_full.py", surfaces=("worker", "web", "api")),),
    })


def _contributions() -> Contributions:
    schema_ref = samples.schema_definition().reference
    return Contributions(
        source_types=(DocumentDefinition(name="test.reader.raw", schema_ref=schema_ref, scopes=SESSION_SCOPES),),
        event_types=(DocumentDefinition(name="test.reader.event", schema_ref=schema_ref, scopes=SESSION_SCOPES),),
        queries=(QueryDefinition(
            name="test.reader.read", scopes=SESSION_SCOPES, arguments=schema_ref, result=schema_ref,
        ),),
        commands=(CommandDefinition(
            name="test.reader.write", scopes=SESSION_SCOPES, arguments=schema_ref, result=schema_ref,
            effect="write", reconciliation=True,
        ),),
        processing=(ProcessingSelection(
            capability="canonical_transformer", scopes=SESSION_SCOPES, input_types=("shell.started",),
        ),),
        services=(PublicService(name="test.reader.records", version="1.0", queries=("test.reader.read",)),),
        web=manifest_samples.web_manifest().contributions.web,
    )
