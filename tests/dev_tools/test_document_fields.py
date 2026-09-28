# Copyright (c) 2026 Zhambyl Yermagambet
"""A pydantic document's fields are contract entries; other unused code still fails the dead-code gate."""

from pathlib import Path

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"
FEATURE = '''# Copyright (c) 2026 Zhambyl Yermagambet
"""Write one greeting document that only JSON readers read."""

import sys

from pydantic import BaseModel, ConfigDict


class Greeting(BaseModel):
    """Carry one greeting text."""

    model_config = ConfigDict(extra="forbid")

    text: str


def display_name(name: str) -> str:
    """Build a name for the fixture's display.

    Returns:
        A named display string.

    """
    return f"Extension: {name}"


def main() -> None:
    """Write the greeting as JSON."""
    sys.stdout.write(Greeting(text=display_name("sample")).model_dump_json())


if __name__ == "__main__":
    main()
'''
UNUSED = '''

def unused_feature() -> int:
    """Give a number that no code asks for.

    Returns:
        One.

    """
    return 1
'''
SDK_DEPENDENCY = '"baqylau-extension-api==0.1.0a1"'


def package_with_document(directory: Path, extra: str = "") -> None:
    """Replace the feature with one document and declare pydantic, as a real package does."""
    source = fixtures.create_package(directory)
    source.write_text(FEATURE + extra, encoding=ENCODING)
    project = directory / "pyproject.toml"
    text = project.read_text(encoding=ENCODING)
    project.write_text(text.replace(SDK_DEPENDENCY, f'{SDK_DEPENDENCY}, "pydantic>=2"'), encoding=ENCODING)


def test_document_fields_are_entries(tmp_path: Path) -> None:
    """Fields and the model configuration of a document pass the dead-code gate."""
    package_with_document(tmp_path)

    completed = fixtures.invoke(tmp_path, "deadcode")

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_other_unused_code_still_fails(tmp_path: Path) -> None:
    """A document does not hide an unused function beside it."""
    package_with_document(tmp_path, UNUSED)

    completed = fixtures.invoke(tmp_path, "deadcode")

    assert completed.returncode != 0
    assert "unused_feature" in completed.stdout
