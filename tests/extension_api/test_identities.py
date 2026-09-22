# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep derived IDs stable and reject invalid repository path forms."""

import pytest
from baqylau_extension_api.identities import DerivedIdentity, derived_event_id
from baqylau_extension_api.models.scopes import RepositoryScope
from pydantic import ValidationError

OWNER = "test"
OUTPUT_KEY = "log"


def test_derived_identity_is_stable() -> None:
    """Keep identity independent of process, settings, and replay revisions."""
    identity = DerivedIdentity(extension_id=OWNER, input_id="input/世界", output_key=OUTPUT_KEY)
    restored = DerivedIdentity.model_validate_json(identity.model_dump_json())
    assert derived_event_id(identity) == derived_event_id(restored)
    assert derived_event_id(identity).startswith("extension:test:")


@pytest.mark.parametrize("second", [
    DerivedIdentity(extension_id="other", input_id="input", output_key=OUTPUT_KEY),
    DerivedIdentity(extension_id=OWNER, input_id="other", output_key=OUTPUT_KEY),
    DerivedIdentity(extension_id=OWNER, input_id="input", output_key="other"),
])
def test_identity_changes_with_each_seed_field(second: DerivedIdentity) -> None:
    """Give different owners, inputs, and output keys distinct identities."""
    first = DerivedIdentity(extension_id=OWNER, input_id="input", output_key=OUTPUT_KEY)
    assert derived_event_id(first) != derived_event_id(second)


def test_key_cannot_change_field_boundaries() -> None:
    """Reject a forged output key that contains the framing separator."""
    identity = DerivedIdentity(extension_id=OWNER, input_id="input\x1fpart", output_key=OUTPUT_KEY)
    forged = identity.model_copy(update={"output_key": "part\x1flog"})
    with pytest.raises(ValidationError):
        derived_event_id(forged)


@pytest.mark.parametrize("worktree", [
    "relative", "/work/../other", "/work/./tree", "/work//tree", "//server", "/work\x00",
])
def test_repository_path_requires_absolute_form(worktree: str) -> None:
    """Reject paths that cannot be accepted as resolved host paths."""
    with pytest.raises(ValidationError):
        RepositoryScope(repository_id="repository-1", worktree=worktree, git_directory="/work/.git")
