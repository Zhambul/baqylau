# One row of the list page: what the session is, where it is on screen, what it
# has spent, and the state of the checkout it is working in.
from pydantic import BaseModel

from api.common.models.values.repository_status import RepositoryStatusResponse
from api.common.models.values.terminal_state import TerminalStateResponse
from api.dashboard.models.sessions.activity_statistics import ActivityStatisticsResponse
from api.dashboard.models.sessions.context_summary import ContextSummaryResponse
from api.dashboard.models.sessions.session_summary import SessionSummaryResponse
from api.dashboard.models.sessions.tab_state import TabStateResponse
from api.dashboard.models.sessions.usage_summary import UsageSummaryResponse

class SessionListItemResponse(BaseModel):
    session: SessionSummaryResponse
    terminal: TerminalStateResponse
    project_directory: str
    tab_state: TabStateResponse | None
    statistics: ActivityStatisticsResponse
    usage: UsageSummaryResponse
    context: ContextSummaryResponse
    repository: RepositoryStatusResponse | None
