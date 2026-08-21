"""Codex launch command construction."""

from __future__ import annotations

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
        if launch_request.model_id:
            arguments.extend(("-m", launch_request.model_id))
        if launch_request.effort:
            arguments.extend(("-c", f"model_reasoning_effort={launch_request.effort}"))
        if prompt.strip():
            arguments.append(prompt)
        # Launching is just running the CLI; the session announces itself
        # through its own hook raw events.
        return HarnessLaunchPlan(
            command="codex",
            arguments=tuple(arguments),
            title="Codex",
        )
