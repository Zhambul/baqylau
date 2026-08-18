# The list page in one frame: every session row, the account fuel gauges, the
# latest notice, and the preferences the page itself owns. Sent on /api/stream
# whenever any of it changes.
from pydantic import BaseModel

from domain.ids import SessionId

from api.common.models.values.usage_row import UsageRowResponse
from api.dashboard.models.sessions.session_list_item import SessionListItemResponse


class NotificationNoticeResponse(BaseModel):
    revision: int
    session_id: SessionId
    kind: str
    project: str
    title: str


class GlobalNotificationStateResponse(BaseModel):
    enabled: bool
    latest: NotificationNoticeResponse | None


class NewSessionPreferencesResponse(BaseModel):
    working_directory: str | None
    harness: str | None
    model: str | None
    effort: str | None


class NewSessionDraftResponse(BaseModel):
    working_directory: str
    text: str
    sequence: float


class DashboardLimitsResponse(BaseModel):
    upload_bytes: int
    rename_characters: int
    presence_seconds: float


class GlobalPreferencesResponse(BaseModel):
    new_session: NewSessionPreferencesResponse
    new_session_drafts: tuple[NewSessionDraftResponse, ...]
    hidden_directories: dict[str, float]
    limits: DashboardLimitsResponse


class GlobalApplicationResponse(BaseModel):
    sessions: tuple[SessionListItemResponse, ...]
    usage_rows: tuple[UsageRowResponse, ...]
    notifications: GlobalNotificationStateResponse
    preferences: GlobalPreferencesResponse
