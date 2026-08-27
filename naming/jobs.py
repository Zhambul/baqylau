"""Prompt reaction and the background worker for automatic naming."""

from __future__ import annotations

import threading
from collections.abc import Callable

from audit.recorder import AuditRecorder
from domain.events import CanonicalEvent, EventPayload, MessageCreated
from domain.naming import NamingJob
from domain.values import TextContent
from harness.registry import HarnessRegistry
from inference.errors import ModelUnavailableError
from naming.audit import NamingAudit
from naming.service import AutomaticSessionNamer, bounded_prompt
from repository.contract.naming import NamingJobRepository
from repository.contract.sessions import SessionRepository

NAMING_POLL_SECONDS = 0.25


class AutomaticNamingReaction:
    def __init__(
        self,
        harness_registry: HarnessRegistry,
        naming_job_repository: NamingJobRepository,
    ) -> None:
        self.registry = harness_registry
        self.jobs = naming_job_repository

    def react(self, event: CanonicalEvent[EventPayload]) -> None:
        payload = event.payload
        if not (
            isinstance(payload, MessageCreated)
            and payload.role == "user"
            and payload.phase == "prompt"
            and isinstance(payload.content, TextContent)
        ):
            return
        if self.registry.plugin(event.harness).info.supports_native_automatic_renaming:
            return
        prompt = bounded_prompt(payload.content.text)
        if not prompt:
            return
        self.jobs.enqueue(NamingJob(f"initial:{event.session_id}", event.session_id, prompt))


class NamingJobWorker:
    def __init__(
        self,
        naming_job_repository: NamingJobRepository,
        session_repository: SessionRepository,
        automatic_session_namer: AutomaticSessionNamer,
        audit_recorder: AuditRecorder,
    ) -> None:
        self.jobs = naming_job_repository
        self.sessions = session_repository
        self.namer = automatic_session_namer
        self.audit = audit_recorder

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self.tick(stop_event.is_set)
            stop_event.wait(NAMING_POLL_SECONDS)

    def tick(self, cancelled: Callable[[], bool] | None = None) -> bool:
        is_cancelled = cancelled or (lambda: False)
        job = self.jobs.claim_next()
        if job is None:
            return False
        session = self.sessions.find(job.session_id)
        if session is None:
            self.jobs.fail(job.key, "session is unavailable")
            return True
        try:
            title = self.namer.initial_name(session, job.prompt)
            self.jobs.complete(job.key, title)
            self.audit.state_file(
                str(session.session_id),
                "",
                "automatic_title",
                NamingAudit(job_key=job.key, title=title, status="completed"),
            )
        except ModelUnavailableError:
            if is_cancelled():
                self.jobs.fail(job.key, "application stopped")
                self.audit.state_file(
                    str(session.session_id),
                    "",
                    "automatic_title",
                    NamingAudit(job_key=job.key, status="cancelled"),
                )
                return True
            self.jobs.fail(job.key, "no small model is currently available")
            self.audit.state_file(
                str(session.session_id),
                "",
                "automatic_title",
                NamingAudit(job_key=job.key, status="failed"),
            )
        except Exception as error:
            self.audit.error(
                str(session.session_id),
                "automatic naming (initial)",
                NamingAudit(
                    job_key=job.key,
                    error_type=type(error).__name__,
                    error=str(error),
                ),
            )
            self.jobs.fail(job.key, "no small model is currently available")
            self.audit.state_file(
                str(session.session_id),
                "",
                "automatic_title",
                NamingAudit(job_key=job.key, status="failed"),
            )
        return True
