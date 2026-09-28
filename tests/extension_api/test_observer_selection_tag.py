# Copyright (c) 2026 Zhambyl Yermagambet
"""A manifest writer that leaves out defaults still writes the observer's tag.

The tag had a default, so `model_dump_json(exclude_defaults=True)` left it out,
and the SDK then refused the package's own manifest.
"""

from pydantic import TypeAdapter

from baqylau_extension_api.manifest import observers

SELECTIONS: TypeAdapter[observers.DeclaredProcessingSelection] = TypeAdapter(observers.DeclaredProcessingSelection)


def test_a_compact_observer_selection_reads_back() -> None:
    """The dump without defaults keeps the tag, and reads back as the same selection."""
    selection = observers.ObserverSelection(
        capability="observer", scopes=("session",), input_types=("shell.finished",), effect="read",
    )

    compact = selection.model_dump_json(exclude_defaults=True)

    assert '"capability":"observer"' in compact
    assert SELECTIONS.validate_json(compact) == selection
