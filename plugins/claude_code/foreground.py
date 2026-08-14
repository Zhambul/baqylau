"""Claude Code foreground-command rewriting and canonical output observation."""

from __future__ import annotations

import hashlib
import base64
import json
import os
import time
from dataclasses import asdict, dataclass

from contracts.harness import (
    CheckpointStore,
    HarnessEventSource,
    RawEvent,
    RawEventDelivery,
    SourceCheckpoint,
)
from domain.ids import ActorId, RawEventId, SessionId
from plugins.claude_code import shell

READ_SIZE = 64 * 1024
MAXIMUM_LIFETIME_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class ForegroundObservation:
    session_id: str
    actor_id: str
    parent_actor_id: str | None
    operation_id: str
    source_path: str
    completion_path: str
    delete_source: bool
    initial_size: int
    initial_modified_at: int
    wait_for_source_change: bool
    created_at: float


@dataclass(frozen=True)
class PreparedForegroundCommand:
    output: bytes
    action: StartForegroundObservation


def _safe_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _directory(session_id: str) -> str:
    configuration_directory = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser(
        "~/.claude"
    )
    return os.path.join(
        configuration_directory,
        "baqylau",
        "foreground",
        _safe_identity(session_id),
    )


def _paths(session_id: str, operation_id: str) -> tuple[str, str, str]:
    stem = os.path.join(_directory(session_id), _safe_identity(operation_id))
    return stem + ".json", stem + ".out", stem + ".done"


def _updated_input(tool_input: dict, command: str) -> bytes:
    updated_input = dict(tool_input)
    updated_input["command"] = command
    return (
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": updated_input,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def prepare(document: dict) -> PreparedForegroundCommand | None:
    """Prepare one native rewrite; no observer starts before hook facts commit."""
    tool_input = document.get("tool_input") or {}
    command = str(tool_input.get("command") or "")
    if not command.strip() or tool_input.get("run_in_background"):
        return None
    session_id = str(document.get("session_id") or "")
    operation_id = str(document.get("tool_use_id") or "")
    if not session_id or not operation_id:
        raise ValueError("Claude Code foreground command has no session or operation id")
    actor_id = str(document.get("agent_id") or f"{session_id}:lead")
    parent_actor_id = f"{session_id}:lead" if document.get("agent_id") else None
    manifest_path, tee_path, completion_path = _paths(session_id, operation_id)
    os.makedirs(os.path.dirname(manifest_path), mode=0o700, exist_ok=True)

    redirect = shell.redirected_output(command, document.get("cwd"))
    if redirect is None:
        source_path = tee_path
        descriptor = os.open(
            source_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        os.close(descriptor)
        wrapped_command = shell.copy_output_to(command, source_path)
        delete_source = True
        initial_size = 0
        initial_modified_at = 0
        wait_for_source_change = False
    else:
        source_path, append = redirect
        try:
            source_stat = os.stat(source_path)
            initial_size = source_stat.st_size
            initial_modified_at = source_stat.st_mtime_ns
        except FileNotFoundError:
            initial_size = 0
            initial_modified_at = 0
        wrapped_command = command
        delete_source = False
        wait_for_source_change = not append

    observation = ForegroundObservation(
        session_id=session_id,
        actor_id=actor_id,
        parent_actor_id=parent_actor_id,
        operation_id=operation_id,
        source_path=source_path,
        completion_path=completion_path,
        delete_source=delete_source,
        initial_size=initial_size,
        initial_modified_at=initial_modified_at,
        wait_for_source_change=wait_for_source_change,
        created_at=time.time(),
    )
    return PreparedForegroundCommand(
        _updated_input(tool_input, wrapped_command),
        StartForegroundObservation(manifest_path, observation),
    )


@dataclass(frozen=True)
class StartForegroundObservation:
    manifest_path: str
    observation: ForegroundObservation

    def start(self) -> None:
        if os.path.isfile(self.manifest_path):
            return
        temporary_path = f"{self.manifest_path}.{os.getpid()}.tmp"
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as manifest:
                json.dump(asdict(self.observation), manifest, separators=(",", ":"))
            try:
                os.link(temporary_path, self.manifest_path)
            except FileExistsError:
                return
        except FileExistsError:
            return
        finally:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass


@dataclass(frozen=True)
class FinishForegroundObservation:
    manifest_path: str
    completion_path: str

    def start(self) -> None:
        if not os.path.isfile(self.manifest_path):
            return
        descriptor = os.open(
            self.completion_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        os.close(descriptor)


def finish_action(document: dict) -> FinishForegroundObservation:
    session_id = str(document.get("session_id") or "")
    operation_id = str(document.get("tool_use_id") or "")
    if not session_id or not operation_id:
        raise ValueError("Claude Code completed command has no session or operation id")
    manifest_path, _source_path, completion_path = _paths(session_id, operation_id)
    return FinishForegroundObservation(manifest_path, completion_path)


def _source_changed(observation: ForegroundObservation) -> bool:
    try:
        source_stat = os.stat(observation.source_path)
    except FileNotFoundError:
        return False
    return (
        source_stat.st_size != observation.initial_size
        or source_stat.st_mtime_ns != observation.initial_modified_at
    )


class ForegroundOutputSource(HarnessEventSource):
    def __init__(self, manifest_path: str, checkpoints: CheckpointStore) -> None:
        self.manifest_path = manifest_path
        self.checkpoints = checkpoints
        with open(manifest_path, encoding="utf-8") as manifest:
            self.observation = ForegroundObservation(**json.load(manifest))
        manifest_hash = hashlib.sha256(manifest_path.encode("utf-8")).hexdigest()
        self.source_identity = f"claude_code:foreground:{manifest_hash}"

    def drain(self, delivery: RawEventDelivery) -> None:
        observation = self.observation
        checkpoint = self.checkpoints.load(self.source_identity)
        if time.time() - observation.created_at >= MAXIMUM_LIFETIME_SECONDS:
            self._remove()
            return
        if (
            checkpoint is None
            and observation.wait_for_source_change
            and not _source_changed(observation)
        ):
            return
        position = (
            int(checkpoint.position)
            if checkpoint is not None
            else (0 if observation.wait_for_source_change else observation.initial_size)
        )
        if os.path.isfile(observation.source_path):
            with open(observation.source_path, "rb") as source:
                source.seek(position)
                while True:
                    chunk_position = source.tell()
                    content = source.read(READ_SIZE)
                    if not content:
                        break
                    delivery.deliver(self._raw_event(chunk_position, content))
                    position = source.tell()
                    self.checkpoints.commit(
                        SourceCheckpoint(
                            SessionId(observation.session_id),
                            self.source_identity,
                            str(position),
                        )
                    )
        if os.path.isfile(observation.completion_path):
            self._remove()

    def _raw_event(self, position: int, content: bytes) -> RawEvent:
        observation = self.observation
        document = json.dumps(
            {
                "operation_id": observation.operation_id,
                "ordinal": position,
                "stream": "output",
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        content_hash = hashlib.sha256(content).hexdigest()
        return RawEvent(
            RawEventId(f"{self.source_identity}:{position}:{content_hash}"),
            "claude_code",
            "foreground_output",
            observation.source_path,
            str(position),
            SessionId(observation.session_id),
            ActorId(observation.actor_id),
            ActorId(observation.parent_actor_id) if observation.parent_actor_id else None,
            time.time(),
            "json",
            document,
        )

    def _remove(self) -> None:
        observation = self.observation
        for path in (observation.completion_path, self.manifest_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        if observation.delete_source:
            try:
                os.remove(observation.source_path)
            except FileNotFoundError:
                pass


def sources(session_id: SessionId, checkpoints: CheckpointStore) -> tuple[ForegroundOutputSource, ...]:
    directory = _directory(str(session_id))
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
    except FileNotFoundError:
        return ()
    return tuple(
        ForegroundOutputSource(os.path.join(directory, name), checkpoints)
        for name in names
    )
