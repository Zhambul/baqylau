# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep a typed worker reply separate from its checked output."""

from collections.abc import Callable
from dataclasses import dataclass

from extensions.interpretation_failures import call_failure
from extensions.models.interpretation_steps import AppliedStep, FailedStep, StepOutcome


@dataclass(frozen=True)
class CheckedReply[Reply, Output]:
    """Return no derived output when either the call or its full check fails."""

    outcome: StepOutcome[Reply]
    output: Output | None
    reply: Reply | None = None


@dataclass
class InterpretationCall[Reply, Output]:
    """Retain a rejected typed reply without applying any of its output."""

    call: Callable[[], Reply]
    check: Callable[[Reply], Output]
    reply: Reply | None = None

    def run(self) -> CheckedReply[Reply, Output]:
        """Run a pure call and its complete validation as one failure boundary.

        Returns:
            The original typed reply, checked output, or an explicit failure.

        """
        try:
            reply, output = self._checked()
        except Exception:  # noqa: BLE001 -- Failed calls and invalid output must preserve the preceding input.
            return CheckedReply(FailedStep(diagnostic=call_failure(), reply=self.reply), None, self.reply)
        return CheckedReply(AppliedStep(reply=reply), output, reply)

    def _checked(self) -> tuple[Reply, Output]:
        self.reply = self.call()
        return self.reply, self.check(self.reply)
