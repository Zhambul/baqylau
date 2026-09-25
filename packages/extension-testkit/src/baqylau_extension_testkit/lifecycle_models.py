# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the catalog and lifecycle replies that a test needs; ignore fields that this kit does not use.

A test kit version can run against a newer host, so a reply field that the kit
does not read is not an error.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

LifecycleAction = Literal["enable", "disable", "reload"]


class HostDocument(BaseModel):
    """Validate each field that the kit reads, and ignore the others."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class CatalogEntryDocument(HostDocument):
    """Name one discovered package; an invalid package has no identity or digest."""

    extension_id: str | None
    package_digest: str | None


class CatalogDocument(HostDocument):
    """Keep the catalog revision and its packages."""

    revision: int
    entries: tuple[CatalogEntryDocument, ...]


class RuntimeDocument(HostDocument):
    """Keep the lifecycle revision, phase, and cleanup state."""

    revision: int
    phase: str
    cleanup_pending: bool


class FailureDocument(HostDocument):
    """Keep the host's bounded failure code and detail."""

    code: str
    detail: str


class OperationDocument(HostDocument):
    """Keep one lifecycle operation's identity, status, and bounded failure reason."""

    operation_id: str
    status: str
    failure: FailureDocument | None = None


class LifecycleChange(BaseModel):
    """Ask for one lifecycle change at the observed revisions."""

    model_config = ConfigDict(frozen=True)

    action: LifecycleAction
    request_id: str
    expected_revision: int
    expected_catalog_revision: int
    package_digest: str | None
