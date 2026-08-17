"""What the storage layer raises."""

from __future__ import annotations


class RepositoryError(RuntimeError):
    """Base for every failure this layer reports."""


class SchemaVersionMismatch(RepositoryError):
    """The file on disk was written by a different schema than this build."""


class EventIdentityConflict(RepositoryError):
    """A raw event id was reused for DIFFERENT bytes.

    Re-recording an identical observation is a no-op by design (sources re-read
    their last record on resume). This is the other case: corruption, not
    convergence.
    """

