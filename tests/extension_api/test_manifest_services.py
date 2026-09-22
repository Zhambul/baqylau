# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate cross-package service contracts without importing peer code."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.metadata import PackageDependency
from baqylau_extension_api.manifest.operations import PublicService, ServiceRequirement
from baqylau_extension_api.manifest.validation import validate_manifest

from tests.extension_api import full_manifest_sample, manifest_samples, samples

CONTRIBUTIONS = "contributions"


@pytest.mark.parametrize("requirement", [">=1,<2", ">=2"])
def test_required_service_version(requirement: str) -> None:
    """Match service versions separately from the providing package release."""
    consumer = manifest_samples.backend_manifest("test.consumer").model_copy(update={
        "dependencies": (PackageDependency(extension_id=samples.EXTENSION_ID, version_range=">=1"),),
        CONTRIBUTIONS: Contributions(consumes=(ServiceRequirement(
            owner=samples.EXTENSION_ID, name="test.reader.records", version_range=requirement,
        ),)),
    })
    proposed = (consumer, full_manifest_sample.full_manifest())
    if requirement == ">=1,<2":
        assert activation_order(proposed) == (samples.EXTENSION_ID, "test.consumer")
    else:
        with pytest.raises(ExtensionContractError, match="public extension service"):
            activation_order(proposed)


def test_service_rejects_private_operation() -> None:
    """Require each service endpoint to name a declared query or command."""
    manifest = full_manifest_sample.full_manifest()
    changed = manifest.contributions.model_copy(update={"services": (
        PublicService(name="test.reader.records", version="1", queries=("test.reader.undeclared",)),
    )})
    with pytest.raises(ExtensionContractError, match="registered operations"):
        validate_manifest(manifest.model_copy(update={CONTRIBUTIONS: changed}))


def test_service_consumption_requires_dependency() -> None:
    """Require a declared package relationship for service discovery."""
    manifest = manifest_samples.backend_manifest("test.consumer").model_copy(update={
        CONTRIBUTIONS: Contributions(consumes=(ServiceRequirement(
            owner=samples.EXTENSION_ID, name="test.reader.records", version_range=">=1",
        ),)),
    })
    with pytest.raises(ExtensionContractError, match="package dependencies"):
        validate_manifest(manifest)


def test_conflicting_views_fail_before_activation() -> None:
    """Reject two replacements for the same feed target and scope."""
    proposed = tuple(
        manifest_samples.web_manifest(owner) for owner in ("test.first", "test.second")
    )
    changed = tuple(manifest.model_copy(update={
        CONTRIBUTIONS: Contributions(web=(manifest.contributions.web[0].model_copy(update={
            "slot": "feed", "mode": "replace", "target": "shell_started",
        }),)),
    }) for manifest in proposed)
    with pytest.raises(ExtensionContractError, match="exclusive"):
        activation_order(changed)
