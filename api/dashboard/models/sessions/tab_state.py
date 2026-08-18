# The session's liveness verdict — the tab colour, and the red/green alerts.
# A TypeAlias rather than a model: it is one string from a closed list, and the
# api layer names that list itself so a new projection state is a deliberate
# contract change.
from typing import Literal, TypeAlias

TabStateResponse: TypeAlias = Literal[
    "idle",
    "thinking",
    "working",
    "executing",
    "awaiting_background",
    "awaiting_attention",
    "awaiting_response",
]
