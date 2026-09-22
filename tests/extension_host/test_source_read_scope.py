# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep source progress scoped and reusable across valid runtime replacement."""

from dataclasses import replace
from pathlib import Path

import pytest
from baqylau_extension_api.models.scopes import ExtensionScope, RepositoryScope, SessionScope

from extensions.models.source_reads import source_key
from tests.extension_host import observation_requests as originals, source_read_fixture as fixtures

REPOSITORY = RepositoryScope(repository_id="project", worktree="/project", git_directory="/project/.git")
SESSION = SessionScope(session_id="session", actor_id="actor", harness="test")


@pytest.mark.parametrize(("scope", "foreign"), [
    (REPOSITORY, REPOSITORY.model_copy(update={"worktree": "/other"})),
    (REPOSITORY, REPOSITORY.model_copy(update={"git_directory": "/other/.git"})),
    (SESSION, SESSION.model_copy(update={"actor_id": "other"})),
    (SESSION, SESSION.model_copy(update={"harness": "other"})),
])
def test_source_checkpoint_uses_complete_scope(
    tmp_path: Path, scope: ExtensionScope, foreign: ExtensionScope,
) -> None:
    """Repository paths and session actor or harness identities cannot share source progress."""
    case = fixtures.installed(tmp_path)
    context = case.request.context.model_copy(update={
        "binding": case.request.context.binding.model_copy(update={"scope": scope}),
    })
    case = replace(case, request=case.request.model_copy(update={"context": context}))
    outcome = case.store.record_source_read(fixtures.proposal(case))
    assert case.store.source_checkpoint(source_key(case.request)) == outcome.checkpoint
    foreign_key = outcome.checkpoint.key.model_copy(update={"scope": foreign})
    assert case.store.source_checkpoint(foreign_key).revision == 0


def test_new_runtime_resumes_committed_progress(tmp_path: Path) -> None:
    """A valid reload keeps the source checkpoint while requiring the new runtime for acceptance."""
    case = fixtures.installed(tmp_path)
    first = case.store.record_source_read(fixtures.proposal(case))
    new_runtime = originals.reload_request(case.original).runtime_revision
    context = case.request.context.model_copy(update={
        "binding": case.request.context.binding.model_copy(update={"runtime_revision": new_runtime}),
    })
    case = replace(case, request=case.request.model_copy(update={"context": context}))
    request = fixtures.proposal(case, "read-2", "position-2")
    assert request.proposal.checkpoint == first.checkpoint
    assert case.store.record_source_read(request).checkpoint.revision == first.checkpoint.revision + 1
