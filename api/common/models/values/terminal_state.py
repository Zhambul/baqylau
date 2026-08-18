# Where a session is on screen, and what its own TUI is showing right now.
from pydantic import BaseModel


class TerminalInputStateResponse(BaseModel):
    typed_text: str | None
    suggestion: str | None


class TerminalStateResponse(BaseModel):
    window_id: str | None
    input_state: TerminalInputStateResponse | None
