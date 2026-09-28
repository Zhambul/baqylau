# Copyright (c) 2026 Zhambyl Yermagambet
"""Define package execution, assets, and dependencies."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import Digest, ExtensionId, NonemptyText, WireModel
from baqylau_extension_api.paths import RelativePath
from baqylau_extension_api.versions import VersionRange

PythonModule = Annotated[NonemptyText, Field(pattern=r"^[a-zA-Z_][a-zA-Z_0-9]*(\.[a-zA-Z_][a-zA-Z_0-9]*)*$")]
PythonName = Annotated[NonemptyText, Field(pattern=r"^[a-zA-Z_][a-zA-Z_0-9]*$")]


class BackendEnvironment(WireModel):
    """Declare standard hashed requirements and their bundled dependency wheels."""

    requirements: RelativePath
    wheelhouse: RelativePath


class BackendEntry(WireModel):
    """Name the factory that the SDK worker loads in its private environment."""

    module: PythonModule
    factory: PythonName = "build_extension"
    environment: BackendEnvironment | None = None


class PackageAsset(WireModel):
    """Declare one immutable web asset and its exact byte digest."""

    path: RelativePath
    digest: Digest
    media_type: NonemptyText


class PackageDependency(WireModel):
    """Declare a peer without importing its Python or web modules."""

    extension_id: ExtensionId
    version_range: VersionRange
    required: bool = True


class LoadOrder(WireModel):
    """Add processing order constraints among active peer packages."""

    before: Annotated[tuple[ExtensionId, ...], Field(max_length=100)] = ()
    after: Annotated[tuple[ExtensionId, ...], Field(max_length=100)] = ()
