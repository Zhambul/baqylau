"""Canonical HTTP resources and one-cursor session stream."""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs

from app.bootstrap import CanonicalApplication
from app.telemetry import (
    BrowserEvent,
    BrowserEventBatch,
    ClientFailureReport,
    OptimisticActionReport,
)
from contracts.harness import (
    AnswerQuestion,
    ApplyRewind,
    AttachmentReference,
    AutoNameSession,
    CloseSession,
    Compact,
    DecidePlan,
    Interrupt,
    LaunchRequest,
    MigrateAccount,
    OpenRewind,
    QueryContext,
    ReadPlanChoices,
    RenameSession,
    SelectEffort,
    SelectModel,
    SendText,
)
from dashboard.activity import to_wire
from dashboard.application import (
    AnswerSelection,
    BrowserPresence,
    BrowserPushSubscription,
    QueuedMessage,
)
from dashboard.config import BOOT_ID
from dashboard.diff import source_html, unified_diff_html
from domain.ids import ActorId, AttentionId, MessageId, SessionId
from domain.values import StructuredContent
from runtime.projections import ActivityScope

STREAM_POLL_SECONDS = 0.25
STREAM_HEARTBEAT_SECONDS = 15.0


def _integer(query: dict[str, list[str]], name: str, default: int | None = None) -> int | None:
    values = query.get(name)
    if not values:
        return default
    if len(values) != 1:
        raise ValueError(f"{name} must occur once")
    return int(values[0])


def _text(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(f"{name} must occur once")
    return values[0]


def _scope(
    query: dict[str, list[str]],
    lead_actor_id: ActorId,
) -> ActivityScope:
    actor_id = _text(query, "actor_id")
    return ActivityScope(actor_id=ActorId(actor_id) if actor_id else lead_actor_id)


def _required_text(body: dict, name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _attachments(body: dict) -> tuple[AttachmentReference, ...]:
    documents = body.get("attachments", [])
    if not isinstance(documents, list):
        raise ValueError("attachments must be an array")
    attachments = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("attachment must be an object")
        attachments.append(
            AttachmentReference(
                local_path=_required_text(document, "local_path"),
                display_name=_required_text(document, "display_name"),
                media_type=document.get("media_type"),
            )
        )
    return tuple(attachments)


class _CanonicalMixin:
    def _application(self) -> CanonicalApplication:
        return self.server.canonical_application

    def route(self, url, parts):
        if parts[:1] != ["api"]:
            return super().route(url, parts)
        api = parts[1:]
        try:
            if api == ["sessions"]:
                return self._json(to_wire(self._application().dashboard_sessions.sessions()))
            if api == ["stream"]:
                return self._global_stream()
            if api == ["harnesses"]:
                return self._json(self._harnesses())
            if api == ["insights"]:
                return self._json(to_wire(self._application().insights.snapshot()))
            if api == ["resumable-sessions"]:
                query = parse_qs(url.query, keep_blank_values=True)
                return self._json(
                    to_wire(
                        self._application().resumable_sessions.sessions_for(
                            _text(query, "working_directory") or "",
                            _text(query, "search"),
                        )
                    )
                )
            if len(api) == 3 and api[0] == "harnesses" and api[2] == "catalog":
                return self._catalog(api[1], parse_qs(url.query, keep_blank_values=True))
            if len(api) == 2 and api[0] == "content":
                return self._content(api[1], parse_qs(url.query, keep_blank_values=True))
            if len(api) >= 2 and api[0] == "sessions":
                return self._session_get(SessionId(api[1]), api[2:], url.query)
        except (KeyError, ValueError) as error:
            return self._json({"error": str(error)}, 400)
        return super().route(url, parts)

    def route_post(self, parts):
        try:
            if parts == ["api", "sessions"]:
                return self._launch()
            if parts == ["api", "application", "notifications"]:
                return self._set_global_notifications()
            if parts == ["api", "application", "new-session-preferences"]:
                return self._save_new_session_preferences()
            if parts == ["api", "application", "new-session-drafts"]:
                return self._save_new_session_draft()
            if parts == ["api", "application", "hidden-directories"]:
                return self._hide_directory()
            if parts == ["api", "application", "push-subscriptions"]:
                return self._register_push_subscription()
            if parts == ["api", "application", "presence"]:
                return self._report_presence()
            if parts == ["api", "application", "browser-events"]:
                return self._record_browser_events()
            if (
                len(parts) == 4
                and parts[0] == "api"
                and parts[1] == "sessions"
                and parts[3] == "controls"
            ):
                return self._control(SessionId(parts[2]))
            if len(parts) == 5 and parts[:2] == ["api", "sessions"]:
                session_id = SessionId(parts[2])
                if parts[3:] == ["application", "composer-draft"]:
                    return self._save_composer_draft(session_id)
                if parts[3:] == ["application", "composer-queue"]:
                    return self._save_composer_queue(session_id)
                if parts[3:] == ["application", "dialog-draft"]:
                    return self._save_dialog_draft(session_id)
                if parts[3:] == ["application", "view-mode"]:
                    return self._set_view_mode(session_id)
                if parts[3:] == ["application", "notifications-muted"]:
                    return self._set_notifications_muted(session_id)
                if parts[3:] == ["application", "tasks-hidden"]:
                    return self._set_tasks_hidden(session_id)
                if parts[3:] == ["application", "optimistic-actions"]:
                    return self._record_optimistic_action(session_id)
                if parts[3:] == ["application", "client-failures"]:
                    return self._record_client_failure(session_id)
        except (KeyError, TypeError, ValueError) as error:
            return self._json({"error": str(error)}, 400)
        return super().route_post(parts)

    def _session_get(self, session_id: SessionId, rest: list[str], query_text: str):
        query = parse_qs(query_text, keep_blank_values=True)
        application = self._application()
        lead_actor_id = application.sessions.load(session_id).lead_actor_id
        scope = _scope(query, lead_actor_id)
        if not rest:
            return self._json(
                {
                    "canonical": to_wire(
                        application.dashboard_sessions.snapshot(session_id, scope)
                    ),
                    "application": to_wire(
                        application.session_application.snapshot(session_id)
                    ),
                }
            )
        if rest == ["activity"]:
            block_count = _integer(query, "block_count", 100)
            if block_count is None or block_count <= 0:
                raise ValueError("block_count must be positive")
            page = application.dashboard_activity.backlog(
                session_id,
                _integer(query, "before_cursor"),
                scope,
                block_count,
            )
            return self._json(to_wire(page))
        if rest == ["stream"]:
            last_event_id = self.headers.get("Last-Event-ID")
            after_cursor = (
                int(last_event_id)
                if last_event_id is not None
                else (_integer(query, "after_cursor") or 0)
            )
            return self._canonical_stream(session_id, after_cursor, scope)
        if rest == ["memory"]:
            return self._json(to_wire(application.memory.snapshot(session_id)))
        if rest == ["memory", "documents"]:
            return self._json(
                to_wire(
                    application.memory.document(
                        session_id,
                        _text(query, "path"),
                        _text(query, "stem"),
                    )
                )
            )
        return self._json({"error": "not found"}, 404)

    def _canonical_stream(
        self,
        session_id: SessionId,
        cursor: int,
        scope: ActivityScope,
    ) -> None:
        self._sse_start()
        heartbeat_at = time.monotonic()
        application = self._application()
        previous_application = None
        while True:
            frame = application.dashboard_stream.frame(session_id, cursor, scope)
            if frame is not None:
                try:
                    self.wfile.write(frame.sse().encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    return
                cursor = frame.cursor
                heartbeat_at = time.monotonic()
            application_snapshot = to_wire(
                application.session_application.snapshot(session_id)
            )
            encoded_application = json.dumps(
                application_snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if encoded_application != previous_application:
                if not self._sse("application", application_snapshot):
                    return
                previous_application = encoded_application
                heartbeat_at = time.monotonic()
            now = time.monotonic()
            if now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                if not self._sse_beat():
                    return
                heartbeat_at = now
            time.sleep(STREAM_POLL_SECONDS)

    def _global_stream(self) -> None:
        self._sse_start()
        application = self._application()
        if not self._sse("ready", {"boot_id": BOOT_ID}):
            return
        previous_snapshot = None
        heartbeat_at = time.monotonic()
        while True:
            snapshot = to_wire(application.global_application.snapshot())
            encoded_snapshot = json.dumps(
                snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if encoded_snapshot != previous_snapshot:
                if not self._sse("application", snapshot):
                    return
                previous_snapshot = encoded_snapshot
                heartbeat_at = time.monotonic()
            now = time.monotonic()
            if now - heartbeat_at >= STREAM_HEARTBEAT_SECONDS:
                if not self._sse_beat():
                    return
                heartbeat_at = now
            time.sleep(STREAM_POLL_SECONDS)

    def _harnesses(self) -> list[dict]:
        rows = []
        for plugin in self._application().registry.plugins():
            rows.append(
                {
                    "name": plugin.info.name,
                    "display_name": plugin.info.display_name,
                    "launchable": plugin.launcher is not None,
                    "default_for_launch": plugin.info.default_for_launch,
                    "supports_attachments": plugin.info.supports_attachments,
                    "control_names": (
                        sorted(plugin.controller.handlers)
                        if plugin.controller
                        else []
                    ),
                    "supports_accounts": plugin.info.supports_accounts,
                    "supports_terminal_input": plugin.terminal_probe is not None,
                    "supports_memory": plugin.memory is not None,
                }
            )
        return rows

    def _catalog(self, harness: str, query: dict[str, list[str]]):
        session_text = _text(query, "session_id")
        context = QueryContext(
            session_id=SessionId(session_text) if session_text else None,
            working_directory=_text(query, "working_directory"),
        )
        # The menu payload is composed here, from the two places its parts
        # honestly live: the STATIC vocabulary on the plugin's HarnessInfo (built
        # once, as a literal) and the per-directory part from the catalogue. The
        # contract keeps them apart; this endpoint is where the browser wants
        # them together.
        info = self._application().registry.plugin(harness).info
        snapshot = self._application().catalog.read(harness, context)
        payload = to_wire(snapshot)
        payload["models"] = to_wire(info.models)
        payload["rewind_modes"] = to_wire(info.rewind_modes)
        return self._json(payload)

    def _content(self, content_reference: str, query: dict[str, list[str]]):
        text = self._application().content.resolve(content_reference)
        view = _text(query, "view")
        if view in ("diff", "source"):
            path = _text(query, "path")
            if not path:
                raise ValueError("path is required for file view")
            rendered = unified_diff_html(text, path) if view == "diff" else source_html(text, path)
            return self._send(200, rendered, "text/html; charset=utf-8")
        return self._send(200, text, "text/plain; charset=utf-8")

    def _launch(self):
        body = self._post_guard()
        if body is None:
            return None
        harness = _required_text(body, "harness")
        resume_text = body.get("resume_session_id")
        request = LaunchRequest(
            working_directory=_required_text(body, "working_directory"),
            initial_text=body.get("initial_text"),
            model_id=body.get("model_id"),
            effort=body.get("effort"),
            account_id=body.get("account_id"),
            resume_session_id=SessionId(resume_text) if resume_text else None,
            attachments=_attachments(body),
        )
        result = self._application().launcher.launch(harness, request)
        return self._json(to_wire(result), 202 if result.status == "started" else 409)

    def _save_new_session_preferences(self):
        body = self._post_guard()
        if body is None:
            return None
        values = {}
        for field in ("working_directory", "harness", "model", "effort"):
            value = body.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{field} must be a string")
            values[field] = value or None
        self._application().global_application.save_new_session_preferences(**values)
        return self._json({"saved": True})

    def _save_new_session_draft(self):
        body = self._post_guard()
        if body is None:
            return None
        working_directory = body.get("working_directory", "")
        text = body.get("text")
        sequence = body.get("sequence")
        if not isinstance(working_directory, str):
            raise ValueError("working_directory must be a string")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if not isinstance(sequence, (int, float)) or isinstance(sequence, bool):
            raise ValueError("sequence must be a number")
        saved = self._application().global_application.save_new_session_draft(
            working_directory,
            text,
            float(sequence),
        )
        return self._json({"saved": saved})

    def _hide_directory(self):
        body = self._post_guard()
        if body is None:
            return None
        working_directory = body.get("working_directory")
        if not isinstance(working_directory, str):
            raise ValueError("working_directory must be a string")
        hidden = self._application().global_application.hide_directory(working_directory)
        return self._json({"hidden": hidden})

    def _register_push_subscription(self):
        body = self._post_guard()
        if body is None:
            return None
        document = body.get("subscription")
        if not isinstance(document, dict):
            raise ValueError("subscription must be an object")
        endpoint = document.get("endpoint")
        keys = document.get("keys")
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ValueError("subscription endpoint must use https")
        if not isinstance(keys, dict):
            raise ValueError("subscription keys must be an object")
        public_key = _required_text(keys, "p256dh")
        authentication_secret = _required_text(keys, "auth")
        device_id = _required_text(body, "device_id")
        device_label = body.get("device_label")
        if device_label is not None and not isinstance(device_label, str):
            raise ValueError("device_label must be a string")
        self._application().global_application.register_push_subscription(
            BrowserPushSubscription(
                endpoint,
                public_key,
                authentication_secret,
                device_id,
                device_label or None,
            )
        )
        return self._json({"saved": True})

    def _report_presence(self):
        body = self._post_guard()
        if body is None:
            return None
        device_id = _required_text(body, "device_id")
        session_text = body.get("session_id")
        away = body.get("away", False)
        if session_text is not None and not isinstance(session_text, str):
            raise ValueError("session_id must be a string")
        if not isinstance(away, bool):
            raise ValueError("away must be a boolean")
        self._application().global_application.report_presence(
            BrowserPresence(
                device_id,
                SessionId(session_text) if session_text else None,
                away,
            )
        )
        return self._json({"saved": True})

    def _record_optimistic_action(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        action = _required_text(body, "action")
        phase = _required_text(body, "phase")
        if action not in {"composer", "close", "answer", "plan"}:
            raise ValueError("unknown optimistic action")
        if phase not in {"shown", "reconciled", "dropped", "stale"}:
            raise ValueError("unknown optimistic action phase")
        self._application().browser_telemetry.record_optimistic_action(
            OptimisticActionReport(
                session_id,
                action,
                phase,
                self._optional_integer(body, "character_count"),
                self._optional_integer(body, "elapsed_milliseconds"),
                body.get("reason") if isinstance(body.get("reason"), str) else None,
            )
        )
        return self._json({"recorded": True})

    def _record_client_failure(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        failure_kind = _required_text(body, "failure_kind")
        if failure_kind not in {"transport", "http"}:
            raise ValueError("unknown client failure kind")
        error = body.get("error")
        if error is not None and not isinstance(error, str):
            raise ValueError("error must be a string")
        self._application().browser_telemetry.record_client_failure(
            ClientFailureReport(
                session_id,
                _required_text(body, "gesture"),
                failure_kind,
                error,
                self._optional_integer(body, "status_code"),
                self._optional_integer(body, "character_count"),
            )
        )
        return self._json({"recorded": True})

    def _record_browser_events(self):
        body = self._post_guard()
        if body is None:
            return None
        events = body.get("events")
        connection = body.get("connection", {})
        if not isinstance(events, list):
            raise ValueError("events must be an array")
        if not isinstance(connection, dict):
            raise ValueError("connection must be an object")
        parsed_events = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("each browser event must be an object")
            session_text = event.get("session_id")
            details = event.get("details", {})
            if session_text is not None and not isinstance(session_text, str):
                raise ValueError("session_id must be a string")
            if not isinstance(details, dict):
                raise ValueError("event details must be an object")
            parsed_events.append(
                BrowserEvent(
                    SessionId(session_text) if session_text else None,
                    _required_text(event, "name"),
                    self._optional_integer(event, "timestamp"),
                    self._scalar_values(details),
                )
            )
        self._application().browser_telemetry.record_events(
            BrowserEventBatch(
                _required_text(body, "client_id"),
                _required_text(body, "device_id"),
                self._scalar_values(connection),
                tuple(parsed_events),
            )
        )
        return self._json({"recorded": True})

    @staticmethod
    def _optional_integer(document: dict, field: str) -> int | None:
        value = document.get(field)
        if value is None:
            return None
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{field} must be a number")
        return int(value)

    @staticmethod
    def _scalar_values(document: dict) -> dict:
        if not all(
            isinstance(value, (str, int, float, bool)) or value is None
            for value in document.values()
        ):
            raise ValueError("telemetry details must contain scalar values")
        return {str(key): value for key, value in document.items()}

    def _control(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        request = self._control_request(session_id, body)
        outcome = self._application().controls.execute(request)
        status = {"acknowledged": 200, "indeterminate": 202, "rejected": 409}[
            outcome.status
        ]
        return self._json(to_wire(outcome), status)

    def _save_composer_draft(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        text = body.get("text")
        origin = body.get("origin")
        sequence = body.get("sequence")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if not isinstance(origin, str):
            raise ValueError("origin must be a string")
        if not isinstance(sequence, (int, float)) or isinstance(sequence, bool):
            raise ValueError("sequence must be a number")
        saved = self._application().session_application.save_composer_draft(
            session_id, text, origin, float(sequence)
        )
        return self._json({"saved": saved})

    def _save_composer_queue(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        items = body.get("items")
        origin = body.get("origin")
        if not isinstance(items, list):
            raise ValueError("items must be an array")
        if not isinstance(origin, str):
            raise ValueError("origin must be a string")
        messages = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("each queued message must contain text")
            if item["text"].strip():
                messages.append(QueuedMessage(item["text"]))
        self._application().session_application.save_composer_queue(
            session_id, tuple(messages), origin
        )
        return self._json({"saved": True})

    def _save_dialog_draft(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        attention_id = AttentionId(_required_text(body, "attention_id"))
        origin = body.get("origin")
        answers = body.get("answers")
        if not isinstance(origin, str):
            raise ValueError("origin must be a string")
        if not isinstance(answers, list):
            raise ValueError("answers must be an array")
        selections = []
        for answer in answers:
            if not isinstance(answer, dict):
                raise ValueError("each answer must be an object")
            selected = answer.get("selected")
            other = answer.get("other")
            if not isinstance(selected, list) or not all(
                isinstance(value, str) for value in selected
            ):
                raise ValueError("selected answers must be strings")
            if not isinstance(other, str):
                raise ValueError("other must be a string")
            selections.append(AnswerSelection(tuple(selected), other))
        self._application().session_application.save_dialog_draft(
            session_id, attention_id, tuple(selections), origin
        )
        return self._json({"saved": True})

    def _set_view_mode(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        view_mode = _required_text(body, "view_mode")
        self._application().session_application.set_view_mode(session_id, view_mode)
        return self._json({"saved": True})

    def _set_notifications_muted(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        muted = body.get("muted")
        if not isinstance(muted, bool):
            raise ValueError("muted must be a boolean")
        self._application().session_application.set_notifications_muted(session_id, muted)
        return self._json({"saved": True})

    def _set_tasks_hidden(self, session_id: SessionId):
        body = self._post_guard()
        if body is None:
            return None
        hidden = body.get("hidden")
        if not isinstance(hidden, bool):
            raise ValueError("hidden must be a boolean")
        self._application().session_application.set_tasks_hidden(session_id, hidden)
        return self._json({"saved": True})

    def _set_global_notifications(self):
        body = self._post_guard()
        if body is None:
            return None
        enabled = body.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        self._application().global_application.set_notifications_enabled(enabled)
        return self._json({"saved": True})

    @staticmethod
    def _control_request(session_id: SessionId, body: dict):
        control_name = _required_text(body, "control_name")
        request_id = _required_text(body, "request_id")
        target = (session_id, request_id)
        if control_name == "send_text":
            attachments = _attachments(body)
            message_text = body.get("text")
            if not isinstance(message_text, str):
                raise ValueError("text must be a string")
            if not message_text and not attachments:
                raise ValueError("text or attachments are required")
            return SendText(
                *target,
                text=message_text,
                attachments=attachments,
                replace_terminal_draft=bool(body.get("replace_terminal_draft", False)),
            )
        if control_name == "interrupt":
            return Interrupt(*target)
        if control_name == "close_session":
            return CloseSession(*target)
        if control_name == "rename_session":
            return RenameSession(*target, name=_required_text(body, "name"))
        if control_name == "auto_name_session":
            return AutoNameSession(*target)
        if control_name == "open_rewind":
            return OpenRewind(*target)
        if control_name == "apply_rewind":
            return ApplyRewind(
                *target,
                target_message_id=MessageId(_required_text(body, "target_message_id")),
                target_text=_required_text(body, "target_text"),
                newer_prompt_count=int(body.get("newer_prompt_count", 0)),
                mode=_required_text(body, "mode"),
            )
        if control_name == "migrate_account":
            return MigrateAccount(*target)
        if control_name == "compact":
            return Compact(*target)
        if control_name == "select_model":
            return SelectModel(*target, model_id=_required_text(body, "model_id"))
        if control_name == "select_effort":
            return SelectEffort(*target, effort=_required_text(body, "effort"))
        if control_name == "answer_question":
            answers = body.get("answers")
            return AnswerQuestion(
                *target,
                attention_id=AttentionId(_required_text(body, "attention_id")),
                decision=_required_text(body, "decision"),
                answers=(
                    StructuredContent(json.dumps(answers, ensure_ascii=False))
                    if answers is not None
                    else None
                ),
                discussion=body.get("discussion"),
            )
        if control_name == "read_plan_choices":
            return ReadPlanChoices(
                *target,
                attention_id=AttentionId(_required_text(body, "attention_id")),
            )
        if control_name == "decide_plan":
            return DecidePlan(
                *target,
                attention_id=AttentionId(_required_text(body, "attention_id")),
                decision=_required_text(body, "decision"),
                feedback=body.get("feedback"),
            )
        raise ValueError(f"unknown control: {control_name}")
