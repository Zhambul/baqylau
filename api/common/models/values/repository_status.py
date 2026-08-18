# The git status drawn beside a session row.
from pydantic import BaseModel


class RepositoryStatusResponse(BaseModel):
    branch: str
    worktree: str | None
    dirty: bool
