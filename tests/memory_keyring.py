# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep secret values in process memory, so a test never writes the user's Keychain."""

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class MemoryKeyring(KeyringBackend):
    """Store values for the life of one process; `PYTHON_KEYRING_BACKEND` selects it in tests."""

    priority = 1

    def __init__(self) -> None:
        """Start with no stored value."""
        super().__init__()
        self.stored_values: dict[tuple[str, str], str] = {}

    def read_value(self, service: str, username: str) -> str | None:
        """Read one value.

        Returns:
            The value, or None.

        """
        return self.stored_values.get((service, username))

    def store_value(self, service: str, username: str, password: str) -> None:
        """Store one value."""
        self.stored_values[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        """Remove one value.

        Raises:
            PasswordDeleteError: If no value is stored.

        """
        if self.stored_values.pop((service, username), None) is None:
            message = "no stored value"
            raise PasswordDeleteError(message)

    # The keyring backend API names these two methods.
    get_password = read_value
    set_password = store_value
