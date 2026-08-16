# One session's task-list visibility switch.
from pydantic import BaseModel


class TasksHiddenRequest(BaseModel):
    hidden: bool
