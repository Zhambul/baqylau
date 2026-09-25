# Copyright (c) 2026 Zhambyl Yermagambet
"""Install a real worker package whose projector adds a feed card for each finished turn."""

from pathlib import Path

from baqylau_extension_api.manifest import contributions, data, metadata
from baqylau_extension_api.models.documents import SchemaDefinition, SchemaRef

from tests.extension_api import session_card_example as example
from tests.extension_host import environment_fixture, package_fixture

OWNER = package_fixture.OWNER


def install_cards(directory: Path, wheels: Path, cards_per_turn: int = 1) -> None:
    """Install the card projector as a real worker package that draws this many cards for each finished turn."""
    package = environment_fixture.write_package(directory, wheels)
    manifest = package_fixture.read_manifest(package)
    assert manifest.backend is not None
    reference = SchemaRef(owner=OWNER, name="text", version=1, digest=example.TEXT_DIGEST)
    schema = SchemaDefinition(reference=reference, json_text=example.TEXT_SCHEMA)
    package_fixture.save_manifest(package, manifest.model_copy(update={
        "backend": metadata.BackendEntry(module="card_backend", environment=manifest.backend.environment),
        "capabilities": ("lifecycle", "projector"),
        "schemas": (schema,),
        "contributions": contributions.Contributions(
            entry_types=(data.DocumentDefinition(name=f"{OWNER}.card", schema_ref=reference, scopes=("session",)),),
            processing=(data.ProcessingSelection(
                capability="projector", scopes=("session",), input_types=("turn.finished",),
            ),),
        ),
    }))
    package_fixture.write_file(package, "card_backend.py", counted_backend(cards_per_turn))


def counted_backend(cards_per_turn: int) -> bytes:
    """Give the card module with its count of cards for each finished turn.

    Returns:
        The module bytes.

    """
    source = Path(example.__file__).read_text(encoding="utf-8")
    return source.replace("CARDS_PER_TURN = 1\n", f"CARDS_PER_TURN = {cards_per_turn}\n").encode("utf-8")
