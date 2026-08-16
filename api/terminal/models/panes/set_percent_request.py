# Set the activity pane to an explicit width percentage.
from api.terminal.models.panes.pane_gesture_request import PaneGestureRequest


class SetPanePercentRequest(PaneGestureRequest):
    percent: int
