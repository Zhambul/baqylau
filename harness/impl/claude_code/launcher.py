"""Claude Code launch command construction."""

from __future__ import annotations

from harness.contract import HarnessLauncher
from harness.models import HarnessLaunchPlan, LaunchRejected, LaunchRequest
from harness.impl.claude_code.attachments import prompt_with_attachments

# The launch-time selections, riding the CLI's environment the way the account
# already does: Claude Code never echoes the effort in any raw event stream and
# reports the model only on its first assistant record, so the environment the
# hook process inherits is the one place a launch selection survives to be
# observed. Owned here (the one writer); the hook entry reads them back.
LAUNCH_MODEL_VARIABLE = "BAQYLAU_LAUNCH_MODEL"
LAUNCH_EFFORT_VARIABLE = "BAQYLAU_LAUNCH_EFFORT"
COMMAND = "claude"


class ClaudeCodeLauncher(HarnessLauncher):
    def prepare(self, launch_request: LaunchRequest) -> HarnessLaunchPlan:
        if launch_request.account_id is not None:
            raise LaunchRejected("Claude Code does not support account selection")
        prompt = prompt_with_attachments(
            launch_request.initial_text or "",
            launch_request.attachments,
        )
        arguments: list[str] = []
        environment: list[tuple[str, str]] = []   # name/value pairs, not flat argv
        if launch_request.resume_session_id is not None:
            arguments.extend(("--resume", str(launch_request.resume_session_id)))
        if launch_request.model:
            arguments.extend(("--model", launch_request.model))
            environment.append((LAUNCH_MODEL_VARIABLE, launch_request.model))
        if launch_request.effort:
            arguments.extend(("--effort", launch_request.effort))
            environment.append((LAUNCH_EFFORT_VARIABLE, launch_request.effort))
        if prompt.strip():
            arguments.append(prompt)
        # Launching is just running the CLI: the tab opens in a login shell, so
        # the account alias resolves exactly as it does when typed by hand. The
        # session announces itself through its own hook raw event.
        return HarnessLaunchPlan(
            command=COMMAND,
            arguments=tuple(arguments),
            title="Claude Code",
            environment=tuple(environment),
        )
