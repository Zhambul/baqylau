# The session page's two halves in one reply: what the SESSION is (folded from
# canonical facts, the same for everyone) and what YOU have on it (drafts, a
# queue, a half-made choice). They are separate keys because they change for
# different reasons and the page re-renders them independently.
from pydantic import BaseModel

from api.dashboard.models.application.session_application_response import (
    SessionApplicationResponse,
)
from api.dashboard.models.sessions.canonical_snapshot import CanonicalSnapshotResponse


class SessionSnapshotResponse(BaseModel):
    canonical: CanonicalSnapshotResponse
    application: SessionApplicationResponse
