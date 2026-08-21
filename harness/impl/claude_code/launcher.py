"""Claude Code launch command construction."""

from __future__ import annotations

from domain.ids import AccountId
from harness.contract import HarnessLauncher
from harness.models import HarnessLaunchPlan, LaunchRejected, LaunchRequest
from harness.impl.claude_code import account

# The launch-time selections, riding the CLI's environment the way the account
# already does: Claude Code never echoes the effort in any evidence stream and
# reports the model only on its first assistant record, so the environment the
# hook process inherits is the one place a launch selection survives to be
# observed. Owned here (the one writer); the hook entry reads them back.
LAUNCH_MODEL_VARIABLE = "BAQYLAU_LAUNCH_MODEL"
LAUNCH_EFFORT_VARIABLE = "BAQYLAU_LAUNCH_EFFORT"


class ClaudeCodeLauncher(HarnessLauncher):
    def prepare(self, launch_request: LaunchRequest) -> HarnessLaunchPlan:
        account_alias = account.alias_for(launch_request.account_id or AccountId(""))
        if account_alias is None:
            raise LaunchRejected("unknown Claude Code account")
        attachment_text = " ".join(
            f"@{attachment.local_path}"
            for attachment in launch_request.attachments
        )
        initial_text = launch_request.initial_text or ""
        prompt = attachment_text + ("\n" + initial_text if attachment_text and initial_text else initial_text)
        arguments: list[str] = []
        environment: list[tuple[str, str]] = []   # name/value pairs, not flat argv
        if launch_request.resume_session_id is not None:
            arguments.extend(("--resume", str(launch_request.resume_session_id)))
        if launch_request.model_id:
            arguments.extend(("--model", launch_request.model_id))
            environment.append((LAUNCH_MODEL_VARIABLE, launch_request.model_id))
        if launch_request.effort:
            arguments.extend(("--effort", launch_request.effort))
            environment.append((LAUNCH_EFFORT_VARIABLE, launch_request.effort))
        if prompt.strip():
            arguments.append(prompt)
        # Launching is just running the CLI: the tab opens in a login shell, so
        # the account alias resolves exactly as it does when typed by hand. The
        # session announces itself through its own hook evidence.
        return HarnessLaunchPlan(
            command=account_alias or "claude",
            arguments=tuple(arguments),
            title="Claude Code",
            environment=tuple(environment),
        )
