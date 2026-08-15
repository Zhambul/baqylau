"""Claude Code launch command construction."""

from __future__ import annotations

import os
import sys

from contracts.harness import HarnessLaunchPlan, HarnessLauncher, LaunchRejected, LaunchRequest
from plugins.claude_code import account


class ClaudeCodeLauncher(HarnessLauncher):
    def prepare(self, request: LaunchRequest) -> HarnessLaunchPlan:
        account_alias = account.alias_for(request.account_id or "")
        if account_alias is None:
            raise LaunchRejected("unknown Claude Code account")
        attachment_text = " ".join(
            f"@{attachment.local_path}"
            for attachment in request.attachments
        )
        initial_text = request.initial_text or ""
        prompt = attachment_text + ("\n" + initial_text if attachment_text and initial_text else initial_text)
        arguments = []
        if request.resume_session_id is not None:
            arguments.extend(("--resume", str(request.resume_session_id)))
        if request.model_id:
            arguments.extend(("--model", request.model_id))
        if request.effort:
            arguments.extend(("--effort", request.effort))
        if prompt.strip():
            arguments.append(prompt)
        # Launch through the wrapper: it registers the session BEFORE the
        # harness can fire a hook, and records the process-exit observation.
        command_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "command.py")
        return HarnessLaunchPlan(
            command=sys.executable,
            arguments=(command_path, account_alias or "claude", *arguments),
            title="Claude Code",
        )
