# Copyright (c) 2026 Zhambyl Yermagambet
"""Find the repository scope of a directory, or of a session, with one bounded Git call.

The SDK's rule names the repository, so the host and every package give a
repository the same scope. Callers use this at request time, never inside a
database transaction.
"""

from pathlib import Path

from baqylau_extension_api.models.scopes import ExtensionScope, RepositoryScope, SessionScope
from baqylau_extension_api.repositories import REPOSITORY_ARGUMENTS, repository_from_git

from core.repository import RepositoryQueries
from domain.session_state import SessionFacts


def repository_of(directory: str) -> RepositoryScope | None:
    """Resolve the repository of a directory.

    Returns:
        The scope, or None for a directory that is missing or not in a repository.

    """
    if not directory or not Path(directory).is_dir():
        return None
    completed = RepositoryQueries.run_git(directory, *REPOSITORY_ARGUMENTS)
    if completed is None or completed.returncode:
        return None
    try:
        return repository_from_git(completed.stdout)
    except ValueError:
        return None


def session_scopes(facts: SessionFacts) -> tuple[ExtensionScope, ...]:
    """Name a session's own scope, then the repository of its working directory, if any.

    Returns:
        The scopes, most specific first.

    """
    scope = SessionScope(session_id=facts.session_id, actor_id=facts.lead_actor_id, harness=facts.harness)
    repository = repository_of(facts.working_directory)
    return (scope,) if repository is None else (scope, repository)
