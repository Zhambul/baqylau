# Copyright (c) 2026 Zhambyl Yermagambet
"""Run engine stages only after an input notice or a known deadline."""

from contextlib import closing
from functools import partial
from pathlib import Path
from threading import Event

from audit.failures import FailureContext
from core.input_events import InputEvents
from core.work_queue import WorkKind, WorkQueue
from engine import extensions_boundary, mixed_processing, projection_stage, source_processing, work_batch
from engine.interpret.loop import Interpreter
from engine.interpret.output_source import MAXIMUM_LIFETIME_SECONDS
from engine.react.loop import ReactionLoop
from extensions.processing_contract import ExtensionProcessingBatch


class EngineWorker:
    """Own one ordered worker and its external input subscriptions."""

    def __init__(
        self,
        interpreter: Interpreter,
        reaction_loop: ReactionLoop,
        work_queue: WorkQueue,
        profiles: tuple[Path, ...],
        extensions: source_processing.EngineExtensionServices | None = None,
    ) -> None:
        """Connect input notices to one ordered worker."""
        self.interpreter = interpreter
        self.reaction_loop = reaction_loop
        self.work_queue = work_queue

        self.extension_services = extensions or source_processing.EngineExtensionServices()
        self._batch = work_batch.EngineWorkBatch(work_queue, interpreter.failures)
        self.extensions = extensions_boundary.EngineExtensionBoundary(
            self.extension_services.runtime, work_queue,
            partial(interpreter.failures.record, "extension lifecycle", FailureContext()),
        )
        changed = partial(work_queue.put, WorkKind.SOURCES)
        self.inputs = InputEvents(changed, profiles)
        interpreter.puller.watch_files = self.inputs.watch_files
        interpreter.puller.retry = partial(work_queue.schedule, WorkKind.SOURCES, 1.0, key="source retry")

    def run(self, stop_event: Event) -> None:
        """Drain persisted work at startup, then wait for notices."""
        self.inputs.start()
        for kind in WorkKind:
            self.work_queue.put(kind)
        with closing(self.inputs):
            while not stop_event.is_set():
                pending = self.work_queue.take()
                if not pending:
                    return
                self._process(pending, stop_event)

    def stop(self) -> None:
        """Release the worker's idle wait."""
        self.work_queue.close()

    def _process(self, pending: set[WorkKind], stop_event: Event) -> None:
        pending = work_batch.ready_stages(self.extensions.prepare(pending))
        if not pending:
            return
        try:
            with self.extension_services.capture_batch() as sources:
                stage = partial(self._stage, stop_event=stop_event, sources=sources)
                self._batch.run(pending, stop_event.is_set, stage)
        except Exception:  # noqa: BLE001 -- Retain work after a failed runtime capture.
            self._batch.retry(pending, "engine batch")

    def _stage(
        self, work_kind: WorkKind, stop_event: Event, sources: ExtensionProcessingBatch | None,
    ) -> None:
        if work_kind is WorkKind.EXTENSIONS:
            return
        if work_kind is WorkKind.SOURCES:
            self._subscriptions()
            self.interpreter.read_sources()
            self._expiry_deadline()
        if work_kind in {WorkKind.SOURCES, WorkKind.EXTENSION_SOURCES}:
            source_processing.EngineSourceReads(
                self.inputs, self.work_queue, self.interpreter.dependencies.runtime.clock,
            ).read(sources, stop_event.is_set, refresh_plans=work_kind is WorkKind.SOURCES)
            return
        if work_kind is WorkKind.CANONICAL:
            self.reaction_loop.drain(stop_event.is_set)
            projection_stage.project(self, sources)
            return
        if sources is not None:
            mixed_processing.read_mixed(sources, self.interpreter.translation, self.work_queue, stop_event.is_set)
            return
        while not stop_event.is_set():
            if not self.interpreter.translation.translate():
                return

    def _subscriptions(self) -> None:
        dependencies = self.interpreter.dependencies
        sessions = dependencies.repositories.sessions.watchable()
        outputs = tuple(
            following
            for session in sessions
            for following in dependencies.repositories.shell_output.find_for_session(session.session_id)
        )
        self.inputs.update(
            {Path(session.source_reference).resolve().parent for session in sessions},
            {Path(following.source_path).resolve() for following in outputs},
        )
        self.inputs.watch_processes({
            session.harness_process_id for session in sessions if session.harness_process_id is not None
        })

    def _expiry_deadline(self) -> None:
        dependencies = self.interpreter.dependencies
        oldest = dependencies.repositories.shell_output.oldest_created_at()
        if oldest is not None:
            delay = oldest + MAXIMUM_LIFETIME_SECONDS - dependencies.runtime.clock()
            self.work_queue.schedule(WorkKind.SOURCES, delay, key="output expiry")
