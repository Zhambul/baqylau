"""Codex launch command construction."""

from __future__ import annotations

import os

from harness.contract import HarnessLauncher
from harness.models import HarnessLaunchPlan, LaunchRejected, LaunchRequest


class CodexLauncher(HarnessLauncher):
    def prepare(self, launch_request: LaunchRequest) -> HarnessLaunchPlan:
        if launch_request.account_id:
            raise LaunchRejected("Codex has no account switcher")
        attachment_text = " ".join(
            attachment.local_path
            for attachment in launch_request.attachments
        )
        initial_text = launch_request.initial_text or ""
        prompt = attachment_text + ("\n" + initial_text if attachment_text and initial_text else initial_text)
        arguments: list[str] = []
        if launch_request.resume_session_id is not None:
            arguments.extend(("resume", str(launch_request.resume_session_id)))
        if launch_request.working_directory:
            arguments.extend(("-C", launch_request.working_directory))
        if launch_request.model:
            arguments.extend(("-m", launch_request.model))
        if launch_request.effort:
            arguments.extend(("-c", f"model_reasoning_effort={launch_request.effort}"))
        # The rollout always stores encrypted reasoning, but its readable
        # summary defaults to "none" in current Codex model metadata. Baqylau
        # needs the native summary to present reasoning activity. It does not
        # inspect or reconstruct encrypted model state.
        arguments.extend(("-c", 'model_reasoning_summary="concise"'))
        if prompt.strip():
            arguments.append(prompt)
        environment: tuple[tuple[str, str], ...] = ()
        configured_home = os.environ.get("CODEX_HOME")
        if configured_home:
            environment = (("CODEX_HOME", configured_home),)
        # Launching is just running the CLI; the session announces itself
        # through its own hook raw events.
        return HarnessLaunchPlan(
            command="codex",
            arguments=tuple(arguments),
            title="Codex",
            environment=environment,
        )
