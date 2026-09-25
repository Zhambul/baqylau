# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep extension secret values in a store outside settings documents and the database."""

from typing import Protocol


class SecretStore(Protocol):
    """Read, write, and delete one owner's secret values by declared reference name."""

    def read_secret(self, owner: str, name: str) -> str | None:
        """Read one stored value, or None when the user has stored none."""
        ...

    def write_secret(self, owner: str, name: str, secret: str) -> None:
        """Store or replace one value."""
        ...

    def delete_secret(self, owner: str, name: str) -> None:
        """Remove one value; a missing value is not an error."""
        ...
