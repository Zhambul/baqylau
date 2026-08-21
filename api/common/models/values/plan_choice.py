# One decision a harness's live plan dialog offers.
from pydantic import BaseModel


class PlanChoiceResponse(BaseModel):
    # The keystroke the dialog answers to — sent back verbatim as the decision.
    digit: str
    label: str
    # The row that opens the free-text box instead of deciding.
    feedback: bool
