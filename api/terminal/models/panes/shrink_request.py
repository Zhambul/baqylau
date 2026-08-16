# Shrink the activity pane (columns defaults server-side).
from api.terminal.models.panes.pane_gesture_request import PaneGestureRequest


class ShrinkPaneRequest(PaneGestureRequest):
    columns: int | None = None
