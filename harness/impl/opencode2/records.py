# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the native OpenCode2 event fields used by Baqylau."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from domain.ids import AttentionId, MessageId, SessionId, TurnId
from harness.impl.opencode2.shell_records import NativeShell
from harness.impl.opencode2.tokens import NativeTokens
from harness.impl.opencode2.tool_records import NativeContent, NativeInput, NativeMetadata, NativeTool, NativeToolError

NATIVE_FIELDS = ConfigDict(extra="ignore")


class NativeModel(BaseModel):
    """Read a native model selection."""

    model_config = NATIVE_FIELDS
    id: str
    provider: str = Field(alias="providerID")
    variant: str | None = None


class NativeLocation(BaseModel):
    """Read a native working directory."""

    model_config = NATIVE_FIELDS
    directory: str


class NativeContext(BaseModel):
    """Read the model limit saved for one completed step."""

    model: NativeModel
    window_tokens: int | None = None


class NativeSession(BaseModel):
    """Read the session identity captured by the native plugin."""

    model_config = NATIVE_FIELDS
    id: str
    location: NativeLocation
    title: str | None = None
    model: NativeModel | None = None
    parent_id: SessionId | None = Field(default=None, alias="parentID")


class NativeData(BaseModel):
    """Read native event details without changing their meaning."""

    model_config = NATIVE_FIELDS
    session_id: SessionId | None = Field(default=None, alias="sessionID")
    inbox_id: MessageId | None = Field(default=None, alias="inboxID")
    assistant_message_id: MessageId | None = Field(default=None, alias="assistantMessageID")
    text: str | None = None
    title: str | None = None
    ordinal: int = 0
    model: NativeModel | None = None
    cost: Decimal | None = None
    finish: str | None = None
    tokens: NativeTokens | None = None
    id: str | None = None
    request_id: AttentionId | None = Field(default=None, alias="requestID")
    action: str | None = None
    resources: tuple[str, ...] = ()
    save: tuple[str, ...] = ()
    reply: str | None = None
    input: NativeInput | None = None
    content: tuple[NativeContent, ...] = ()
    metadata: NativeMetadata | None = None
    error: NativeToolError | None = None


class NativeEvent(BaseModel):
    """Read one native event with its stable identity."""

    model_config = NATIVE_FIELDS
    id: str
    type: str
    created: int | None = None
    details: NativeData = Field(alias="data")


class NativeRecord(BaseModel):
    """Keep native data and captured joins in one stored record."""

    model_config = ConfigDict(extra="forbid")
    event: NativeEvent
    server_process_id: int | None = None
    session: NativeSession
    turn_id: TurnId | None = None
    prompt: str | None = None
    attachment_names: tuple[str, ...] = ()
    # A native notice wakes the root without a person. It is not a prompt.
    notice: str | None = None
    message: str | None = None
    tool: NativeTool | None = None
    root_id: SessionId | None = None
    shell: NativeShell | None = None
    context: NativeContext | None = None
