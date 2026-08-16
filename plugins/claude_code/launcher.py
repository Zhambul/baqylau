"""Claude Code launch command construction."""

from __future__ import annotations

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
        # Launching is just running the CLI: the tab opens in a login shell, so
        # the account alias resolves exactly as it does when typed by hand. The
        # session announces itself through its own hook evidence.
        return HarnessLaunchPlan(
            command=account_alias or "claude",
            arguments=tuple(arguments),
            title="Claude Code",
        )
