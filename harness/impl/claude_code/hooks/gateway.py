# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert one Claude Code hook delivery into raw events and a reply."""

from __future__ import annotations

import time
from typing import override

from domain.ids import RawEventId
from harness.contract import HarnessHookGateway
from harness.impl.claude_code.canonical import records
from harness.impl.claude_code.hooks import constants, observation, permission, shell_output
from harness.models.hooks import HarnessHookRequest, HarnessHookResponse
from harness.models.raw_events import RawEvent, RawEventSourceContext

CLI_PROCESS_NAME = constants.CLI_PROCESS_NAME


def _base_hook_event(
    harness_hook_request: HarnessHookRequest,
    hook_observation: observation.HookObservation,
) -> RawEvent:
    actors = hook_observation.actors
    # The source type depends on agent metadata that can arrive after the first
    # delivery. It is part of the observation identity, so it is part of the raw
    # event id: a corrected re-delivery is a new observation, not an identity
    # conflict. The "hook" source type keeps the historical id unchanged.
    return RawEvent(
        raw_event_id=RawEventId(
            f"claude_code:{hook_observation.source_type}:{actors.session_id}:"
            f"{hook_observation.hook_name}:{hook_observation.observation_id}",
        ),
        harness=constants.HARNESS,
        source_type=hook_observation.source_type,
        source_name=hook_observation.hook_name,
        source_position=hook_observation.observation_id,
        session_id=actors.session_id,
        actor_id=actors.actor_id,
        parent_actor_id=actors.lead_actor_id if actors.native_actor_id else None,
        observed_at=time.time(),
        encoding="json",
        payload=harness_hook_request.payload,
        source_identity=f"claude_code:hook:{actors.session_id}",
        terminal_window_id=harness_hook_request.terminal_window_id,
        harness_process_id=harness_hook_request.harness_process_id,
        account_id=None,
        account_display_name=None,
    )


def _launch_event(
    harness_hook_request: HarnessHookRequest,
    hook_observation: observation.HookObservation,
) -> RawEvent | None:
    if hook_observation.hook_name != "SessionStart" or not (
        harness_hook_request.launch_model or harness_hook_request.launch_effort
    ):
        return None
    selections = records.LaunchSelectionDocument(
        model=harness_hook_request.launch_model or None,
        effort=harness_hook_request.launch_effort or None,
    )
    actors = hook_observation.actors
    return RawEvent(
        raw_event_id=RawEventId(
            f"claude_code:launch:{actors.session_id}:{hook_observation.native_event_id}",
        ),
        harness=constants.HARNESS,
        source_type="launch",
        source_name=hook_observation.hook_name,
        source_position=hook_observation.native_event_id,
        session_id=actors.session_id,
        actor_id=actors.lead_actor_id,
        parent_actor_id=None,
        observed_at=time.time(),
        encoding="json",
        payload=selections.model_dump_json().encode("utf-8"),
        source_identity=f"claude_code:launch:{actors.session_id}",
    )


def _hook_events(
    harness_hook_request: HarnessHookRequest,
    hook_observation: observation.HookObservation,
) -> list[RawEvent]:
    events = [_base_hook_event(harness_hook_request, hook_observation)]
    launch_event = _launch_event(harness_hook_request, hook_observation)
    if launch_event is not None:
        events.append(launch_event)
    return events


def _source_context(
    hook_observation: observation.HookObservation,
) -> RawEventSourceContext:
    actors = hook_observation.actors
    return RawEventSourceContext(
        session_id=actors.session_id,
        lead_actor_id=actors.lead_actor_id,
        actor_id=actors.actor_id,
        parent_actor_id=actors.lead_actor_id if actors.native_actor_id else None,
        source_reference=hook_observation.source_reference,
    )


class ClaudeHookGateway(HarnessHookGateway):
    """Convert Claude Code hook deliveries into the harness event model."""

    @override
    def receive_hook(self, harness_hook_request: HarnessHookRequest) -> HarnessHookResponse:
        """Convert one hook delivery.

        Returns:
            The harness hook response.

        """
        document = records.HookPayload.model_validate_json(harness_hook_request.payload)
        hook_observation = observation.hook_observation(document, harness_hook_request.payload)
        raw_events = _hook_events(harness_hook_request, hook_observation)
        output_events, reply = shell_output.shell_output_events(
            document,
            _source_context(hook_observation),
            permission.permission_reply(document),
        )
        raw_events.extend(output_events)
        return HarnessHookResponse(tuple(raw_events), reply)
