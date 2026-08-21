# notify/channels/telegram.py — the Telegram channel, whole: the transport
# and the alert it carries.
#
# The OFF-DEVICE sibling of webpush.py, and it exists for one reason the reused
# `notify` skill could not serve: RETRACTION. The skill is spawned detached with
# DEVNULL stdio and never waited on, so Telegram's `sendMessage` reply — which
# carries the `message_id` that `deleteMessage` needs — is discarded before
# anything can read it. An alert you can't identify afterwards is an alert you
# can't take back. So the dashboard speaks to the Bot API itself when it can,
# and keeps the message id.
#
# Contract, deliberately the same as webpush.py's: `enabled()` is the feature
# probe, every call returns a Result instead of raising (the caller audits and
# swallows — nothing may escape into the Notifier's 1 s watcher loop), and the
# whole module is disabled when unconfigured. There is no alternate transport
# hidden behind this interface.
#
# CREDENTIALS live outside the repo, following dictate.py's Deepgram precedent
# (plain files under ~/.config, read at CALL time so the in-process test server
# can flip them per-test): `bot-token` and `chat-id`, one line each, in
# ~/.config/telegram/. BAQYLAU_DASHBOARD_TELEGRAM_DIR relocates the pair (which is
# also how the suite stays hermetic — an autouse fixture points it at an empty
# tmp dir, so the tests can never pick up a real token from the dev machine's
# home and talk to the actual Bot API); BAQYLAU_DASHBOARD_TELEGRAM_TOKEN / _CHAT
# supply the values directly. The token never appears in a response, an audit
# row, or an error detail — the same rule dictate.py holds for the Deepgram key.
from __future__ import annotations

import dataclasses
import os
import urllib.error
import urllib.parse
import urllib.request
import threading
import time
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from audit import record as A
from domain.ids import SessionId
from notify.channels.alert import FAILED, GONE, NOTHING, OK, PENDING, alert_text
from repository.contract.preferences import (
    PushSigningKeyRepository,
    PushSubscriptionRepository,
)

# Telegram's own JSON, read back — GENUINELY open (`extra="ignore"`): a Bot
# API `Message`/`Chat` carries dozens of fields (from, date, entities, ...)
# this module never reads one of. What it DOES read is declared below; an
# unexpected TYPE on one of those still fails (a str where an int was
# promised raises `ValidationError`), which is the failure this module's
# `except Exception` already turns into an audited, swallowed Result — never
# a silent wrong value.
FOREIGN = ConfigDict(extra="ignore", frozen=True)


class TelegramChat(BaseModel):
    model_config = FOREIGN
    id: int | str


class TelegramMessage(BaseModel):
    """The `result` of a successful `sendMessage` — the one Bot API reply this
    module keeps anything from (the retraction handle)."""

    model_config = FOREIGN
    message_id: int
    chat: TelegramChat


class TelegramApiResponse(BaseModel):
    """The Bot API's own reply shape, `{ok, description?, result?}`, shared by
    every method this module calls. `result` is a `Message` for
    `sendMessage`, or a bare `true` for `deleteMessage` — the Bot API's own
    contract, not a shape this codebase chose."""

    model_config = FOREIGN
    ok: bool
    description: str | None = None
    result: TelegramMessage | bool | None = None


@dataclass(frozen=True)
class SendMessageParams:
    """The `sendMessage` request body we build."""

    chat_id: str
    text: str


@dataclass(frozen=True)
class DeleteMessageParams:
    """The `deleteMessage` request body we build."""

    chat_id: int | str
    message_id: int


TelegramCallParams = SendMessageParams | DeleteMessageParams


@dataclass
class TelegramHandle:
    """The retraction handle `send_alert` hands back: what it takes to find
    the message again (`chat`/`msg_id`, set by the send thread) and where a
    retraction currently stands (`done`/`outcome`/`retry_at`/`deleting`, all
    read and written by the two off-watcher threads — see `retract_alert`).
    Mutable on purpose: it IS the shared state those threads and the
    watcher's poll all read and write, the same "atomic enough" single-field
    bargain `notify/presence.py`'s maps make."""

    ch: Literal["telegram"] = "telegram"
    session_id: SessionId | None = None
    kind: str | None = None
    chat: int | str | None = None
    msg_id: int | None = None
    done: bool = False
    outcome: str | None = None
    retry_at: float = 0.0
    deleting: bool = False


DEFAULT_CRED_DIR = "~/.config/telegram"
TOKEN_NAME = "bot-token"
CHAT_NAME = "chat-id"
DEFAULT_API_BASE = "https://api.telegram.org"

# One small HTTPS POST per call; fail fast so a Telegram outage can never hold a
# sender thread open for long (these run off the watcher, but they still hold a
# thread and a socket).
REQUEST_TIMEOUT_SECONDS = 10.0

# Telegram lets a bot delete its OWN messages in a private chat only within 48 h
# (Bot API `deleteMessage`). Nothing here enforces it — the caller's retraction
# TTL is what keeps us inside the window — but it is the hard ceiling any such
# TTL has to stay under, so it is written down where the API call lives.
DELETE_WINDOW_SECONDS = 48 * 3600
RETRACTION_RETRY_SECONDS = 30.0


def _api_base() -> str:
    """The Bot API origin. Overridable (BAQYLAU_DASHBOARD_TELEGRAM_API) so a hermetic
    test can point the whole module at a local stub server — the seam that makes
    send/delete testable without patching module internals or touching the
    network."""
    return (os.environ.get("BAQYLAU_DASHBOARD_TELEGRAM_API")
            or DEFAULT_API_BASE).rstrip("/")


def cred_dir() -> str:
    """Where `bot-token` and `chat-id` live. One knob for the pair, so a test
    (or a second install) relocates both with a single env var."""
    return os.path.expanduser(
        os.environ.get("BAQYLAU_DASHBOARD_TELEGRAM_DIR") or DEFAULT_CRED_DIR)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _cred(env: str, name: str) -> str:
    """One credential: the env override, else the single line in
    `cred_dir()/name`, else "" (feature off). Never raises — a missing,
    unreadable or non-UTF-8 file reads as absent, which HIDES the feature rather
    than breaking the alert path."""
    val = os.environ.get(env)
    if val:
        return val.strip()
    try:
        return _read(os.path.join(cred_dir(), name))
    except Exception:
        return ""


def token() -> str:
    return _cred("BAQYLAU_DASHBOARD_TELEGRAM_TOKEN", TOKEN_NAME)


def chat_id() -> str:
    return _cred("BAQYLAU_DASHBOARD_TELEGRAM_CHAT", CHAT_NAME)


def enabled() -> bool:
    """Whether the dashboard can talk to the Bot API directly."""
    return bool(token() and chat_id())


class Result:
    """One Bot API outcome. `ok` is the API's own `ok` flag (not merely HTTP
    200); `message_id`/`chat` identify what was sent (a send's retraction
    handle); `gone` means the target message no longer exists — for a delete
    that is SUCCESS by another name (someone cleared the chat first), so the
    caller must not treat it as a failure."""
    __slots__ = ("ok", "gone", "status", "error", "message_id", "chat")

    def __init__(self, ok: bool = False, gone: bool = False, status: int = 0,
                 error: str = "", message_id: int | None = None,
                 chat: int | str | None = None) -> None:
        self.ok, self.gone, self.status, self.error = ok, gone, status, error
        self.message_id, self.chat = message_id, chat


def _call(
    method: str,
    params: TelegramCallParams,
) -> tuple[TelegramMessage | bool | None, Result | None]:
    """POST one Bot API method. Returns (result, Result-on-failure) — exactly
    one of the two is meaningful. Never raises: a reply that does not match
    `TelegramApiResponse` (bad JSON, a field of the wrong type) is caught by
    the same `except Exception` a network failure is, and comes back as an
    ordinary failed Result — audited by the caller, never silent."""
    tok = token()
    if not tok:
        return None, Result(error="no token")
    url = "%s/bot%s/%s" % (_api_base(), tok, method)
    data = urllib.parse.urlencode(dataclasses.asdict(params)).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = TelegramApiResponse.model_validate_json(resp.read() or b"{}")
            status = resp.status
    except urllib.error.HTTPError as e:
        # The API reports its real reason in a JSON body even on a 4xx, and that
        # body is the only way to tell "already deleted" (a benign 400) from a
        # genuine failure — so it is parsed rather than discarded.
        try:
            body, status = TelegramApiResponse.model_validate_json(e.read() or b"{}"), e.code
        except Exception:
            return None, Result(status=e.code, error=str(e))
    except Exception as e:
        return None, Result(error=str(e))
    if not body.ok:
        desc = body.description or ""
        # 400 "message to delete not found" / "message can't be deleted" — the
        # message is already out of the chat, which is what we wanted.
        gone = status == 400 and "not found" in desc.lower()
        return None, Result(gone=gone, status=status, error=desc)
    return body.result, None


def send_message(text: str) -> Result:
    """Send `text` to the configured chat. On success the Result carries the
    `message_id` + `chat` that `delete()` needs — the whole point of this module
    existing beside the fire-and-forget script. Synchronous network I/O, so
    callers run it OFF the watcher thread."""
    chat = chat_id()
    if not chat:
        return Result(error="no chat")
    res, err = _call("sendMessage", SendMessageParams(chat_id=chat, text=text))
    if err is not None or not isinstance(res, TelegramMessage):
        return err or Result(error="empty result")
    return Result(ok=True, status=200, message_id=res.message_id, chat=res.chat.id)


def delete_message(chat: int | str | None, message_id: int | None) -> Result:
    """Delete a message this bot sent (the retraction). `gone` counts as done —
    a message someone already cleared is a message that no longer needs you.
    Synchronous; callers run it off the watcher thread."""
    if not (chat and message_id):
        return Result(error="no handle")
    _res, err = _call("deleteMessage", DeleteMessageParams(chat_id=chat, message_id=message_id))
    if err is not None:
        return err
    return Result(ok=True, status=200, message_id=message_id, chat=chat)


# ------------------------------------------------ the alert this channel carries

def send_alert(
    entry: dict[str, str],
    reason: str | None = None,
) -> TelegramHandle | None:
    """Send the deferred alert to Telegram. `reason` (in the audit row) says WHY
    it fired: `escalation` (the nudge after an on-device push you ignored),
    `no-device` (nobody was push-subscribed — the immediate fallback), or
    `always` (_ALWAYS forced both) — so a Telegram alert is never an unexplained
    duplicate.

    The Bot API call runs on a daemon thread and its `message_id` lands in the
    returned retractable handle. An unconfigured channel returns None."""
    head, title, url = alert_text(entry)
    msg = "%s — %s\n%s" % (head, title, url)
    if not enabled():
        return None
    # The handle is created NOW and filled by the sender thread, because the
    # watcher must not block on a round-trip and a retraction can beat the send
    # home. `msg_id` None + `done` False is exactly the PENDING state retract()
    # reads.
    raw_session_id = entry.get("session_id")
    telegram_handle = TelegramHandle(
        session_id=SessionId(raw_session_id) if raw_session_id is not None else None,
        kind=entry.get("kind"))
    threading.Thread(target=_telegram_send_body, args=(telegram_handle, msg, reason),
                     daemon=True).start()
    return telegram_handle


def _telegram_send_body(
    telegram_handle: TelegramHandle,
    msg: str,
    reason: str | None,
) -> None:
    """The off-watcher send body: call the Bot API, record the id in the handle,
    audit. `done` is set LAST and unconditionally — it is what releases retract()
    from PENDING, so an exception path that skipped it would pin the record until
    its TTL."""
    try:
        res = send_message(msg)
    except Exception:
        A.error("", "dashboard telegram notify", {"session_id": telegram_handle.session_id})
        telegram_handle.done = True
        return
    if res.ok:
        telegram_handle.chat, telegram_handle.msg_id = res.chat, res.message_id
    A.state_file("", "", "telegram-notify",
                 {"session_id": telegram_handle.session_id, "kind": telegram_handle.kind, "reason": reason,
                  "ok": res.ok, "status": res.status, "error": res.error,
                  # the retraction contract, recorded at the send: an alert with
                  # retractable=False can never be taken back, and this row is
                  # the only place that says so.
                  "retractable": bool(res.ok and res.message_id),
                  "message_id": res.message_id})
    telegram_handle.done = True


def retract_alert(
    telegram_handle: TelegramHandle,
    reason: str,
    badge: int = 0,
    *,
    push_signing_key_repository: PushSigningKeyRepository | None = None,
    push_subscription_repository: PushSubscriptionRepository | None = None,
) -> str:
    """Delete the message — OFF the watcher thread, for the same reason the send
    is: `delete_message` is a synchronous HTTPS round-trip with a 10 s timeout,
    and the 1 s scan loop cannot wear that. So the outcome is not known
    synchronously either, and this settles over two ticks: the first spawns the
    delete and answers PENDING, a later one reads what the thread left. The
    caller already retries PENDING (it must, for the in-flight send), so this
    needs no new machinery — and the `notify-retract` row it eventually writes
    still reports what actually happened at the HTTP boundary, rather than an optimistic
    guess made before the call returned."""
    del reason, badge, push_signing_key_repository, push_subscription_repository  # a Bot API message needs none
    if not telegram_handle.done:
        return PENDING                     # the SEND hasn't landed yet
    if telegram_handle.outcome in (OK, GONE):
        return str(telegram_handle.outcome)              # the delete thread finished
    if telegram_handle.outcome == FAILED:
        if time.monotonic() < telegram_handle.retry_at:
            return PENDING
        telegram_handle.outcome, telegram_handle.deleting = None, False
    if not (telegram_handle.chat and telegram_handle.msg_id):
        return NOTHING                     # the send failed — nothing is out there
    if not telegram_handle.deleting:                     # spawn once, however often we're asked
        telegram_handle.deleting = True
        threading.Thread(target=_telegram_delete_body, args=(telegram_handle,),
                         daemon=True).start()
    return PENDING


def _telegram_delete_body(telegram_handle: TelegramHandle) -> None:
    """The off-watcher delete body: `outcome` is set on every path (it is what
    releases the retraction from PENDING), and a `gone` message counts as done —
    someone clearing the chat first is the outcome we wanted, not a failure."""
    try:
        res = delete_message(telegram_handle.chat, telegram_handle.msg_id)
    except Exception:
        A.error("", "dashboard telegram retract", {"session_id": telegram_handle.session_id})
        telegram_handle.retry_at = time.monotonic() + RETRACTION_RETRY_SECONDS
        telegram_handle.outcome = FAILED
        A.state_file(
            "",
            "",
            "telegram-retract",
            {
                "session_id": telegram_handle.session_id,
                "kind": telegram_handle.kind,
                "message_id": telegram_handle.msg_id,
                "outcome": FAILED,
                "status": 0,
                "error": "exception",
            },
        )
        return
    outcome = OK if res.ok else (GONE if res.gone else FAILED)
    if outcome == FAILED:
        telegram_handle.retry_at = time.monotonic() + RETRACTION_RETRY_SECONDS
    telegram_handle.outcome = outcome
    A.state_file(
        "",
        "",
        "telegram-retract",
        {
            "session_id": telegram_handle.session_id,
            "kind": telegram_handle.kind,
            "message_id": telegram_handle.msg_id,
            "outcome": outcome,
            "status": res.status,
            "error": res.error,
        },
    )
