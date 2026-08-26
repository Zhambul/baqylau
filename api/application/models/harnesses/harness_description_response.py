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
    supports_readable_compaction_context: bool
    # The launch form's prompt is REQUIRED for this harness (it announces its
    # session only when the first turn begins) — the same rule the launcher
    # service enforces, served so the form can say so before the POST.
    requires_initial_message: bool
