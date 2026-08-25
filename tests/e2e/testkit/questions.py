"""Start real question work through one harness-neutral test interface."""

from __future__ import annotations

from sdk.client import SessionRef
from tests.e2e.testkit.references import SessionSpec, WorkerKind, WorkRef
from tests.e2e.testkit.work import StartedWork, WorkDriver


class QuestionWorkDriver:
    def __init__(self, work_driver: WorkDriver) -> None:
        self._work_driver = work_driver

    def launch(
        self,
        spec: SessionSpec,
        *,
        work_name: str,
        worker_kind: WorkerKind,
        prompt: str,
    ) -> StartedWork:
        return self._work_driver.launch(
            spec,
            work_name=work_name,
            worker_kind=worker_kind,
            prompt=self._native_prompt(spec, prompt),
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
        return self._work_driver.assign(
            spec,
            session,
            work_name=work_name,
            worker_kind=worker_kind,
            prompt=self._native_prompt(spec, prompt),
        )

    @staticmethod
    def _native_prompt(spec: SessionSpec, prompt: str) -> str:
        if spec.harness == "codex":
            instruction = "Use request_user_input exactly once."
        elif spec.harness == "claude_code":
            instruction = "Use AskUserQuestion exactly once."
        else:
            raise AssertionError(
                f"harness {spec.harness!r} has no question work adapter"
            )
        return f"{instruction} {prompt}"
