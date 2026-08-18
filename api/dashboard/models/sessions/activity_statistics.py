# The counters the scorebar draws.
from pydantic import BaseModel


class ActivityStatisticsResponse(BaseModel):
    shell_command_count: int
    failed_shell_command_count: int
    file_count: int
    lines_added: int
    lines_removed: int
    actor_message_count: int
    operation_counts: dict[str, int]
