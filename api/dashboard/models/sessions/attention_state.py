# What the session is WAITING on: the permission prompts, questions and plans
# it has open, with their options rendered for the dialog that answers them.
from typing import Literal

from pydantic import BaseModel

from domain.ids import ActorId, AttentionId


class AttentionOptionResponse(BaseModel):
    value: str
    label: str
    description: str | None


class AttentionQuestionResponse(BaseModel):
    question_id: str
    title: str | None
    text: str
    multiple: bool
    options: tuple[AttentionOptionResponse, ...]


class PendingAttentionResponse(BaseModel):
    actor_id: ActorId
    attention_id: AttentionId
    attention_type: Literal["permission", "question", "plan", "confirmation"]
    questions: tuple[AttentionQuestionResponse, ...]
    plan_html: str | None


class AttentionStateResponse(BaseModel):
    pending: tuple[PendingAttentionResponse, ...]
