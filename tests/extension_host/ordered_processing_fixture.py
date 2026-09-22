# Copyright (c) 2026 Zhambyl Yermagambet
"""Install three independent worker packages in one private daemon root."""

from pathlib import Path

from baqylau_extension_api.manifest import contributions, data, metadata
from baqylau_extension_api.manifest.package import ExtensionManifest

from extensions.models import interpretation_steps as steps, interpretations
from sdk.client import BaqylauClient
from tests.extension_api import operation_samples, ordered_transform_operations as operations, source_samples
from tests.extension_host import (
    environment_fixture,
    lifecycle_http_fixture,
    package_fixture,
    source_daemon_fixture,
)

OWNERS = (operations.FIRST_OWNER, operations.SECOND_OWNER)
MODULES = (
    "ordered_transform_operations", "ordered_raw_example", "ordered_canonical_example", "ordered_transform_example",
)


def installed(directory: Path, wheels: Path) -> source_daemon_fixture.SourceDaemon:
    """Keep the source empty until all selected workers are active.

    Returns:
        A source fixture with two separate transform packages.

    """
    case = source_daemon_fixture.installed(directory, wheels, "")
    for owner in OWNERS:
        source = environment_fixture.write_package(directory / owner, wheels)
        manifest = declaration(package_fixture.read_manifest(source), owner)
        package_fixture.save_manifest(source, manifest)
        copy_modules(source)
        source.rename(directory / "packages" / owner)
    return case


def declaration(original: ExtensionManifest, owner: str) -> ExtensionManifest:
    """Declare ordering which opposes lexical owner order.

    Returns:
        The exact source and fact selections plus one owned string schema.

    """
    assert original.backend is not None
    schema = operation_samples.schema_definition()
    reference = schema.reference.model_copy(update={"owner": owner})
    return original.model_copy(update={
        "extension_id": owner,
        "backend": original.backend.model_copy(update={"module": "ordered_feature.ordered_transform_example"}),
        "capabilities": ("lifecycle", "raw_transformer", "canonical_transformer"),
        "schemas": (schema.model_copy(update={"reference": reference}),),
        "load_order": metadata.LoadOrder(after=(operations.FIRST_OWNER,)) if owner == operations.SECOND_OWNER
        else metadata.LoadOrder(),
        "contributions": contributions.Contributions(
            event_types=(data.DocumentDefinition(
                name=f"{owner}.fact", schema_ref=reference, scopes=("installation",),
            ),),
            processing=(
                data.ProcessingSelection(capability="raw_transformer", scopes=("installation",),
                                         input_types=(operation_samples.SOURCE_TYPE,)),
                data.ProcessingSelection(capability="canonical_transformer", scopes=("installation",),
                                         input_types=(source_samples.EVENT_TYPE, *(f"{peer}.fact" for peer in OWNERS))),
            ),
        ),
    })


def copy_modules(source: Path) -> None:
    """Copy fixture modules under their own package, with no host test import path."""
    package_fixture.write_file(source, "ordered_feature/__init__.py", b"")
    directory = Path(operations.__file__).parent
    for name in MODULES:
        text = (directory / f"{name}.py").read_text(encoding="utf-8")
        external = text.replace("from tests.extension_api import", "from ordered_feature import")
        package_fixture.write_file(source, f"ordered_feature/{name}.py", external.encode("utf-8"))


def enable(client: BaqylauClient, owners: tuple[str, ...] = OWNERS) -> None:
    """Finish each public activation before supplying any source input."""
    for owner in (*owners, operation_samples.OWNER):
        request = lifecycle_http_fixture.lifecycle_request(client, owner, "enable", f"enable-{owner}")
        admitted = client.extensions.lifecycle.change(owner, request)
        outcome = lifecycle_http_fixture.wait_operation(client, admitted.operation.operation_id)
        assert outcome.status == "succeeded", outcome


def expected(seed: str) -> tuple[str, ...]:
    """Specify the complete output order independently of operation application.

    Returns:
        Replaced anchor, inserted fact, and translated raw addition.

    """
    return (
        f'"{seed}/raw-first/raw-second/canonical-first/canonical-second"',
        '"added/canonical-second"',
        f'"{seed}/raw-second/canonical-first/canonical-second"',
    )


def expected_alone(owner: str, seed: str) -> tuple[str, ...]:
    """Specify the complete output when only one transform owner is active.

    Returns:
        The replaced anchor, any owned addition, and the raw addition.

    """
    if owner == operations.FIRST_OWNER:
        return (f'"{seed}/raw-first/canonical-first"', '"added"', f'"{seed}/canonical-first"')
    return (f'"{seed}/raw-second/canonical-second"',)


def outcomes(commit: interpretations.InterpretationCommit) -> tuple[str, ...]:
    """Retain every stage when checking complete success and failure order.

    Returns:
        Reply verdicts, or an explicit different stage with no worker reply.

    """
    return tuple(
        step.outcome.kind if isinstance(
            step, (steps.RawTransformStep, steps.ExtensionTranslationStep, steps.CanonicalTransformStep),
        ) else step.stage
        for step in commit.proposal.steps
    )
