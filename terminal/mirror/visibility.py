"""Terminal-specific visibility for canonical session activity."""

from engine.projections import Activity, MessageActivity, ReasoningActivity
from domain.ids import ActorId


def visible(activity: Activity, lead_actor_id: ActorId) -> bool:
    if isinstance(activity, MessageActivity):
        return activity.role != "system" and activity.context.actor_id != lead_actor_id
    if isinstance(activity, ReasoningActivity):
        return activity.context.actor_id != lead_actor_id
    return True
