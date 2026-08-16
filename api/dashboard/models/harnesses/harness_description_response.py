# One installed harness, as the new-session form sees it.
from pydantic import BaseModel


class HarnessDescriptionResponse(BaseModel):
    name: str
    display_name: str
    launchable: bool
    default_for_launch: bool
    supports_attachments: bool
    control_names: tuple[str, ...]
    supports_accounts: bool
    supports_terminal_input: bool
