# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the package copies that stored state still refers to."""

from typing import Protocol


class RetainedDigests(Protocol):
    """Read every package digest that a catalog row, request, or runtime revision refers to."""

    def retained_digests(self) -> frozenset[str]:
        """Read the digests to keep."""
        ...
