# Copyright (c) 2026 Zhambyl Yermagambet
"""Start real question work through one harness-neutral test interface."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from tests.e2e.testkit.work_models import WorkRequest

if TYPE_CHECKING:
    from sdk.client import SessionRef
    from tests.e2e.testkit.references import SessionSpec, WorkerKind, WorkRef
    from tests.e2e.testkit.work import WorkDriver
    from tests.e2e.testkit.work_models import StartedWork


def native_question_prompt(spec: SessionSpec, prompt: str) -> str:
    """Add the harness instruction to a question prompt.

    Returns:
        The complete harness prompt.

    Raises:
        AssertionError: If the harness has no question adapter.

    """
    if spec.harness == "codex":
        instruction = (
            "Use request_user_input exactly once. If its result contains a "
            "user_note: item, treat only the text after user_note: as the "
            "answer; never include user_note: or None of the above in your "
            "final reply."
        )
    elif spec.harness == "claude_code":
        instruction = "Use AskUserQuestion exactly once."
    elif spec.harness == "opencode2":
        instruction = "Use the native question tool exactly once."
    else:
        message = f"harness {spec.harness!r} has no question work adapter"
        raise AssertionError(message)
    return f"{instruction} {prompt}"


class QuestionWorkDriver:
    """Represent question work driver."""

    def __init__(self, work_driver: WorkDriver) -> None:
        """Initialize the object."""
        self._work_driver = work_driver

    def launch(self, spec: SessionSpec, request: WorkRequest) -> StartedWork:
        """Launch question work.

        Returns:
            The started work reference.

        """
        return self._work_driver.launch(
            spec,
            replace(request, prompt=native_question_prompt(spec, request.prompt)),
        )

    def assign(
        self,
        spec: SessionSpec,
        session: SessionRef,
        *,
        work_name: str,
        worker_kind: WorkerKind,
        prompt: str,
    ) -> WorkRef:
        """Assign question work.

        Returns:
            The assigned work reference.

        """
        return self._work_driver.assign(
            spec,
            session,
            WorkRequest(
                work_name,
                native_question_prompt(spec, prompt),
                worker_kind=worker_kind,
            ),
        )
