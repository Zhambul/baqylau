"""Codex launch command construction."""

from __future__ import annotations

import os
import sys

from contracts.harness import HarnessLaunchPlan, HarnessLauncher, LaunchRejected, LaunchRequest


class CodexLauncher(HarnessLauncher):
    def prepare(self, request: LaunchRequest) -> HarnessLaunchPlan:
        if request.account_id:
            raise LaunchRejected("Codex has no account switcher")
        attachment_text = " ".join(
            attachment.local_path
            for attachment in request.attachments
        )
        initial_text = request.initial_text or ""
        prompt = attachment_text + ("\n" + initial_text if attachment_text and initial_text else initial_text)
        arguments = []
        if request.resume_session_id is not None:
            arguments.extend(("resume", str(request.resume_session_id)))
        if request.working_directory:
            arguments.extend(("-C", request.working_directory))
        if request.model_id:
            arguments.extend(("-m", request.model_id))
        if request.effort:
            arguments.extend(("-c", f"model_reasoning_effort={request.effort}"))
        if prompt.strip():
            arguments.append(prompt)
        command_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "command.py")
        return HarnessLaunchPlan(
            command=sys.executable,
            arguments=(command_path, *arguments),
            title="Codex",
        )
