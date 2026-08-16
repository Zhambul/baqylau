# api/models.py — every typed request body, and the literal response models.
#
# Requests are pydantic models: the schema IS the validation, replacing the
# hand-rolled isinstance checks the stdlib handler grew. The control envelope
# is a discriminated union on `control_name`, and each variant knows how to
# become its contracts/harness.py dataclass — the registry-over-if/elif rule,
# expressed as polymorphism. Responses that are projection dataclasses stay
# dataclasses (serialized by dashboard.activity.to_wire, their one encoder);
# only the small hand-built replies get models here.
from __future__ import annotations

import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

from contracts.harness import (
    AnswerQuestion,
    ApplyRewind,
    AttachmentReference,
    AutoNameSession,
    CloseSession,
    Compact,
    ControlRequest,
    DecidePlan,
    Interrupt,
    LaunchRequest,
    MigrateAccount,
    OpenRewind,
    ReadPlanChoices,
    RenameSession,
    SelectEffort,
    SelectModel,
    SendText,
)
from domain.ids import AttentionId, MessageId, SessionId
from domain.values import StructuredContent

RequiredText = Annotated[str, Field(min_length=1)]
Scalar = str | int | float | bool | None


class AttachmentBody(BaseModel):
    local_path: RequiredText
    display_name: RequiredText
    media_type: str | None = None

    def reference(self) -> AttachmentReference:
        return AttachmentReference(self.local_path, self.display_name, self.media_type)


def _references(attachments: tuple[AttachmentBody, ...]) -> tuple[AttachmentReference, ...]:
    return tuple(attachment.reference() for attachment in attachments)


# -- launching -------------------------------------------------------------

class LaunchBody(BaseModel):
    harness: RequiredText
    working_directory: RequiredText
    initial_text: str | None = None
    model_id: str | None = None
    effort: str | None = None
    account_id: str | None = None
    resume_session_id: str | None = None
    attachments: tuple[AttachmentBody, ...] = ()

    def request(self) -> LaunchRequest:
        return LaunchRequest(
            working_directory=self.working_directory,
            initial_text=self.initial_text,
            model_id=self.model_id,
            effort=self.effort,
            account_id=self.account_id,
            resume_session_id=(
                SessionId(self.resume_session_id) if self.resume_session_id else None
            ),
            attachments=_references(self.attachments),
        )


# -- the control envelope ----------------------------------------------------
#
# One variant per gesture; `request()` builds the contracts dataclass the
# harness controllers execute. The discriminator gives an unknown control a
# schema-level rejection instead of a hand-written "unknown control" branch.

class ControlEnvelope(BaseModel):
    request_id: RequiredText


class SendTextBody(ControlEnvelope):
    control_name: Literal["send_text"]
    text: str
    attachments: tuple[AttachmentBody, ...] = ()
    replace_terminal_draft: bool = False

    @model_validator(mode="after")
    def _text_or_attachments(self):
        if not self.text and not self.attachments:
            raise ValueError("text or attachments are required")
        return self

    def request(self, session_id: SessionId) -> ControlRequest:
        return SendText(
            session_id,
            self.request_id,
            text=self.text,
            attachments=_references(self.attachments),
            replace_terminal_draft=self.replace_terminal_draft,
        )


class InterruptBody(ControlEnvelope):
    control_name: Literal["interrupt"]

    def request(self, session_id: SessionId) -> ControlRequest:
        return Interrupt(session_id, self.request_id)


class CloseSessionBody(ControlEnvelope):
    control_name: Literal["close_session"]

    def request(self, session_id: SessionId) -> ControlRequest:
        return CloseSession(session_id, self.request_id)


class RenameSessionBody(ControlEnvelope):
    control_name: Literal["rename_session"]
    name: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return RenameSession(session_id, self.request_id, name=self.name)


class AutoNameSessionBody(ControlEnvelope):
    control_name: Literal["auto_name_session"]

    def request(self, session_id: SessionId) -> ControlRequest:
        return AutoNameSession(session_id, self.request_id)


class OpenRewindBody(ControlEnvelope):
    control_name: Literal["open_rewind"]

    def request(self, session_id: SessionId) -> ControlRequest:
        return OpenRewind(session_id, self.request_id)


class ApplyRewindBody(ControlEnvelope):
    control_name: Literal["apply_rewind"]
    target_message_id: RequiredText
    target_text: RequiredText
    newer_prompt_count: int = 0
    mode: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return ApplyRewind(
            session_id,
            self.request_id,
            target_message_id=MessageId(self.target_message_id),
            target_text=self.target_text,
            newer_prompt_count=self.newer_prompt_count,
            mode=self.mode,
        )


class MigrateAccountBody(ControlEnvelope):
    control_name: Literal["migrate_account"]

    def request(self, session_id: SessionId) -> ControlRequest:
        return MigrateAccount(session_id, self.request_id)


class CompactBody(ControlEnvelope):
    control_name: Literal["compact"]

    def request(self, session_id: SessionId) -> ControlRequest:
        return Compact(session_id, self.request_id)


class SelectModelBody(ControlEnvelope):
    control_name: Literal["select_model"]
    model_id: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return SelectModel(session_id, self.request_id, model_id=self.model_id)


class SelectEffortBody(ControlEnvelope):
    control_name: Literal["select_effort"]
    effort: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return SelectEffort(session_id, self.request_id, effort=self.effort)


class AnswerQuestionBody(ControlEnvelope):
    control_name: Literal["answer_question"]
    attention_id: RequiredText
    decision: Literal["answer", "discuss"]
    answers: Any = None
    discussion: str | None = None

    def request(self, session_id: SessionId) -> ControlRequest:
        return AnswerQuestion(
            session_id,
            self.request_id,
            attention_id=AttentionId(self.attention_id),
            decision=self.decision,
            answers=(
                StructuredContent(json.dumps(self.answers, ensure_ascii=False))
                if self.answers is not None
                else None
            ),
            discussion=self.discussion,
        )


class ReadPlanChoicesBody(ControlEnvelope):
    control_name: Literal["read_plan_choices"]
    attention_id: RequiredText

    def request(self, session_id: SessionId) -> ControlRequest:
        return ReadPlanChoices(
            session_id, self.request_id, attention_id=AttentionId(self.attention_id)
        )


class DecidePlanBody(ControlEnvelope):
    control_name: Literal["decide_plan"]
    attention_id: RequiredText
    decision: RequiredText
    feedback: str | None = None

    def request(self, session_id: SessionId) -> ControlRequest:
        return DecidePlan(
            session_id,
            self.request_id,
            attention_id=AttentionId(self.attention_id),
            decision=self.decision,
            feedback=self.feedback,
        )


ControlBody = Annotated[
    Union[
        SendTextBody,
        InterruptBody,
        CloseSessionBody,
        RenameSessionBody,
        AutoNameSessionBody,
        OpenRewindBody,
        ApplyRewindBody,
        MigrateAccountBody,
        CompactBody,
        SelectModelBody,
        SelectEffortBody,
        AnswerQuestionBody,
        ReadPlanChoicesBody,
        DecidePlanBody,
    ],
    Field(discriminator="control_name"),
]


# -- terminal panes and views -------------------------------------------------

class PaneCommandBody(BaseModel):
    command: RequiredText
    working_directory: RequiredText
    window_id: str | None = None
    columns: int | None = None
    percent: int | None = None


class TerminalViewBody(BaseModel):
    content_reference: RequiredText


# -- application state: preferences, drafts, presence --------------------------

class NewSessionPreferencesBody(BaseModel):
    working_directory: str | None = None
    harness: str | None = None
    model: str | None = None
    effort: str | None = None


class NewSessionDraftBody(BaseModel):
    working_directory: str = ""
    text: str
    sequence: float


class HideDirectoryBody(BaseModel):
    working_directory: str


class PushSubscriptionKeysBody(BaseModel):
    p256dh: RequiredText
    auth: RequiredText


class PushSubscriptionDocumentBody(BaseModel):
    endpoint: Annotated[str, Field(pattern=r"^https://")]
    keys: PushSubscriptionKeysBody


class PushSubscriptionBody(BaseModel):
    subscription: PushSubscriptionDocumentBody
    device_id: RequiredText
    device_label: str | None = None


class PresenceBody(BaseModel):
    device_id: RequiredText
    session_id: str | None = None
    away: bool = False


class GlobalNotificationsBody(BaseModel):
    enabled: bool


class ComposerDraftBody(BaseModel):
    text: str
    origin: str
    sequence: float


class QueuedMessageBody(BaseModel):
    text: str


class ComposerQueueBody(BaseModel):
    items: tuple[QueuedMessageBody, ...]
    origin: str


class AnswerSelectionBody(BaseModel):
    selected: tuple[str, ...]
    other: str


class DialogDraftBody(BaseModel):
    attention_id: RequiredText
    origin: str
    answers: tuple[AnswerSelectionBody, ...]


class ViewModeBody(BaseModel):
    view_mode: RequiredText


class NotificationsMutedBody(BaseModel):
    muted: bool


class TasksHiddenBody(BaseModel):
    hidden: bool


# -- browser telemetry ---------------------------------------------------------

class OptimisticActionBody(BaseModel):
    action: Literal["composer", "close", "answer", "plan"]
    phase: Literal["shown", "reconciled", "dropped", "stale"]
    character_count: int | None = None
    elapsed_milliseconds: int | None = None
    reason: str | None = None


class ClientFailureBody(BaseModel):
    gesture: RequiredText
    failure_kind: Literal["transport", "http"]
    error: str | None = None
    status_code: int | None = None
    character_count: int | None = None


class BrowserEventBody(BaseModel):
    name: RequiredText
    session_id: str | None = None
    timestamp: int | None = None
    details: dict[str, Scalar] = {}


class BrowserEventsBody(BaseModel):
    client_id: RequiredText
    device_id: RequiredText
    connection: dict[str, Scalar] = {}
    events: tuple[BrowserEventBody, ...]


# -- files and dictation --------------------------------------------------------

class UploadBody(BaseModel):
    name: str
    mime: str = ""
    data: str
    session_id: str | None = None


class ClipboardFilesBody(BaseModel):
    names: Annotated[tuple[str, ...], Field(min_length=1)]
    session_id: str | None = None


class DictationTokenBody(BaseModel):
    sample_rate: int
    harness: RequiredText
    working_directory: str | None = None


# -- literal response models ------------------------------------------------------

class Saved(BaseModel):
    saved: bool = True


class Recorded(BaseModel):
    recorded: bool = True


class Opened(BaseModel):
    opened: bool


class HiddenDirectories(BaseModel):
    hidden: dict[str, float]


class PaneCommandReply(BaseModel):
    handled: bool
    succeeded: bool
    reason: str | None


class HarnessDescription(BaseModel):
    name: str
    display_name: str
    launchable: bool
    default_for_launch: bool
    supports_attachments: bool
    control_names: tuple[str, ...]
    supports_accounts: bool
    supports_terminal_input: bool


class DictationProbe(BaseModel):
    available: bool


class PushConfiguration(BaseModel):
    enabled: bool
    key: str | None


class UploadReply(BaseModel):
    ok: bool = True
    path: str
    name: str
    mime: str
    is_image: bool


class ClipboardMatches(BaseModel):
    paths: tuple[str, ...]


class DictationGrant(BaseModel):
    token: str
    expires_in: int | None
    ws_url: str
