# What the SESSION is, folded from canonical facts — the half of the session
# page that is the same for every reader (the other half is yours: drafts, a
# queue, a half-made choice).
from pydantic import BaseModel

from api.dashboard.models.sessions.activity_statistics import ActivityStatisticsResponse
from api.dashboard.models.sessions.actor_summary import ActorSummaryResponse
from api.dashboard.models.sessions.attention_state import AttentionStateResponse
from api.dashboard.models.sessions.background_work import BackgroundWorkResponse
from api.dashboard.models.sessions.context_summary import ContextSummaryResponse
from api.dashboard.models.sessions.goal_state import GoalStateResponse
from api.dashboard.models.sessions.session_summary import SessionSummaryResponse
from api.dashboard.models.sessions.tab_state import TabStateResponse
from api.dashboard.models.sessions.task_summary import TaskSummaryResponse
from api.dashboard.models.sessions.usage_summary import UsageSummaryResponse

class CanonicalSnapshotResponse(BaseModel):
    cursor: int
    session: SessionSummaryResponse | None
    tab_state: TabStateResponse | None
    actors: tuple[ActorSummaryResponse, ...]
    usage: UsageSummaryResponse
    context: ContextSummaryResponse
    attention: AttentionStateResponse
    tasks: tuple[TaskSummaryResponse, ...]
    goal: GoalStateResponse | None
    background_work: BackgroundWorkResponse
    statistics: ActivityStatisticsResponse
