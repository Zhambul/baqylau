# The insights page: how much has been run, when, and where. Costs are floats
# here and not Decimals — these are aggregates drawn as chart heights, never a
# price quoted back to anyone.
from datetime import date

from pydantic import BaseModel



class DailySessionCountResponse(BaseModel):
    # A calendar day, not a label: `datetime.date` serializes as the same
    # "YYYY-MM-DD" the charts already read, and refuses anything that is not one.
    date: date
    session_count: int


class HourlySessionCountResponse(BaseModel):
    day_of_week: int
    hour: int
    session_count: int


class InsightProjectSummaryResponse(BaseModel):
    working_directory: str
    name: str
    session_count: int


class InsightWindowResponse(BaseModel):
    session_count: int
    active_session_count: int
    finished_session_count: int
    token_count: int
    cost_in_usd: float
    error_count: int
    projects: tuple[InsightProjectSummaryResponse, ...]


class ProjectInsightsResponse(BaseModel):
    working_directory: str
    name: str
    session_count: int
    token_count: int
    cost_in_usd: float
    error_count: int
    last_session_at: float
    daily_sessions: tuple[DailySessionCountResponse, ...]


class ApplicationInsightsResponse(BaseModel):
    generated_at: float
    total_session_count: int
    daily_sessions: tuple[DailySessionCountResponse, ...]
    hourly_sessions: tuple[HourlySessionCountResponse, ...]
    last_seven_days: InsightWindowResponse
    last_thirty_days: InsightWindowResponse
    all_time: InsightWindowResponse
    projects: tuple[ProjectInsightsResponse, ...]
