"""Generate, validate, and apply concise session titles."""

from __future__ import annotations

import html
import re
import time
import unicodedata
from collections.abc import Callable

from audit.recorder import AuditRecorder
from domain.entries import MessageBody
from domain.ids import RawEventId, RequestId
from domain.naming import NamingJob, NamingJobState
from domain.values import TitleOrigin, content_text
from harness.models import (
    AUTOMATIC_TITLE_SOURCE_TYPE,
    ControlAcknowledgement,
    ControlOutcome,
    ControlResult,
    RawEvent,
    Session,
)
from harness.models.directives import SessionRenameObservation
from inference.contract import ModelFactory, ModelPromptRequest
from inference.errors import ModelUnavailableError
from repository.contract.facts import RawEventRepository
from repository.contract.naming import NamingJobRepository
from repository.contract.session_data import SessionDataRepository
from repository.mapper.documents import encode_document

FIRST_PROMPT_LIMIT = 4_000
TITLE_LIMIT = 80
TITLE_WORD_LIMIT = 8
TITLE_PROMPT = """Create a short title for this coding session.

Return one plain-text title only.
Use 3 to 8 words.
Use at most 80 Unicode characters.
Do not use quotes, Markdown, paths, URLs, or terminal output.

User request:
{prompt}"""
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
HTML_TAG = re.compile(r"<[^>]*>")
MARKUP = re.compile(r"[`*_#>|~]+")
WHITESPACE = re.compile(r"\s+")


class AutomaticSessionNamer:
    def __init__(
        self,
        model_factory: ModelFactory,
        naming_job_repository: NamingJobRepository,
        raw_event_repository: RawEventRepository,
        session_data_repository: SessionDataRepository,
        audit_recorder: AuditRecorder,
    ) -> None:
        self.models = model_factory
        self.jobs = naming_job_repository
        self.raw_events = raw_event_repository
        self.read_model = session_data_repository
        self.audit = audit_recorder

    def initial_name(self, session: Session, first_prompt: str) -> str:
        title = self._generate(first_prompt, str(session.session_id))
        key = f"initial:{session.session_id}"
        self._record_automatic_title(session, key, title)
        return title

    def requested_name(
        self,
        session: Session,
        request_id: RequestId,
        apply_title: Callable[[str], ControlOutcome],
    ) -> ControlOutcome:
        key = f"requested:{session.session_id}:{request_id}"
        job, inserted = self.jobs.register_running(
            NamingJob(key, session.session_id, "", NamingJobState.RUNNING)
        )
        if not inserted:
            if job.state == NamingJobState.COMPLETED and job.title:
                return apply_title(job.title)
            return ControlResult(
                request_id,
                ControlAcknowledgement.INDETERMINATE,
                "automatic naming request is already in progress" if job.state == NamingJobState.RUNNING
                else "no small model is currently available",
            )
        try:
            prompt = self._session_prompt(session)
            title = self._generate(prompt, str(session.session_id))
            self.jobs.complete(key, title)
            outcome = apply_title(title)
            self.audit.state_file(
                str(session.session_id),
                "",
                "automatic_title",
                {"job_key": key, "title": title, "status": outcome.status},
            )
            return outcome
        except ModelUnavailableError as error:
            self.audit.error(
                str(session.session_id),
                "automatic naming (requested)",
                {
                    "job_key": key,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
            self.jobs.fail(key, "no small model is currently available")
            self._audit_failure(session, key)
            return ControlResult(
                request_id,
                ControlAcknowledgement.INDETERMINATE,
                "no small model is currently available",
            )
        except Exception as error:
            self.audit.error(
                str(session.session_id),
                "automatic naming (requested)",
                {
                    "job_key": key,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
            self.jobs.fail(key, "automatic title generation failed")
            self._audit_failure(session, key)
            return ControlResult(
                request_id,
                ControlAcknowledgement.INDETERMINATE,
                "automatic title generation failed",
            )

    def _generate(self, first_prompt: str, session_id: str) -> str:
        bounded = bounded_prompt(first_prompt)
        response = self.models.small().send(
            ModelPromptRequest(TITLE_PROMPT.format(prompt=bounded), session_id)
        )
        return normalize_title(response.text)

    def _session_prompt(self, session: Session) -> str:
        entries = self.read_model.entries_of_types(session.session_id, ("message",))
        first = next(
            (
                entry.body
                for entry in entries
                if isinstance(entry.body, MessageBody)
                and entry.body.role == "user"
                and entry.body.phase == "prompt"
            ),
            None,
        )
        if first is None:
            raise ModelUnavailableError("session has no semantic user prompt")
        return content_text(first.content)

    def _record_automatic_title(self, session: Session, key: str, title: str) -> None:
        if session.plugin is None:
            raise ValueError(f"session has no attached harness plugin: {session.session_id}")
        observation = SessionRenameObservation(title, TitleOrigin.AUTOMATIC)
        self.raw_events.record(
            (
                RawEvent(
                    raw_event_id=RawEventId(f"automatic-title:{key}"),
                    harness=session.plugin.info.name,
                    source_type=AUTOMATIC_TITLE_SOURCE_TYPE,
                    source_name="automatic_title",
                    source_position=key,
                    session_id=session.session_id,
                    actor_id=session.lead_actor_id,
                    parent_actor_id=None,
                    observed_at=time.time(),
                    encoding="json",
                    payload=encode_document(observation),
                    source_identity=f"automatic-title:{session.session_id}",
                ),
            )
        )

    def _audit_failure(self, session: Session, key: str) -> None:
        self.audit.state_file(
            str(session.session_id),
            "",
            "automatic_title",
            {"job_key": key, "status": "failed"},
        )


def bounded_prompt(prompt: str) -> str:
    plain = "".join(
        character
        for character in prompt
        if character in "\n\t" or not unicodedata.category(character).startswith("C")
    )
    return plain.strip()[:FIRST_PROMPT_LIMIT]


def normalize_title(title: str) -> str:
    lines = tuple(line.strip() for line in title.splitlines() if line.strip())
    if not lines:
        raise ModelUnavailableError("model returned an empty title")
    cleaned = html.unescape(HTML_TAG.sub("", lines[0]))
    cleaned = MARKDOWN_LINK.sub(r"\1", cleaned)
    cleaned = MARKUP.sub("", cleaned)
    cleaned = "".join(
        character for character in cleaned if not unicodedata.category(character).startswith("C")
    )
    cleaned = WHITESPACE.sub(" ", cleaned).strip(" \"'“”‘’")
    words = cleaned.split()
    if len(words) < 3:
        raise ModelUnavailableError("model returned fewer than three title words")
    cleaned = " ".join(words[:TITLE_WORD_LIMIT])[:TITLE_LIMIT].rstrip()
    if not cleaned:
        raise ModelUnavailableError("model returned an empty title")
    return cleaned
