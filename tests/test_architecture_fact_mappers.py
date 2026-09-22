# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the public fact conversion used by storage below application services."""

from tests import architecture_test_files


def test_extension_fact_mappers_are_model_only() -> None:
    """A narrow repository mapper import must not open the extension service layer."""
    architecture_test_files.assert_imports(
        "extensions/mapper", {"domain"}, allowed_modules=frozenset(("extensions.mapper",)),
    )
