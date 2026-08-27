"""Start real plan work through one harness-neutral test interface."""

from __future__ import annotations

from api.sessiondata.models.entry import MessageBodyResponse
from sdk.client import ActionReceipt, BaqylauClient, SessionRef
from sdk.state import PlanState, SessionSnapshot
from tests.e2e.testkit.references import PlanRef, SessionSpec, TurnRef
from tests.e2e.testkit.turns import matches_final_answer


def plan_state(snapshot: SessionSnapshot, reference: PlanRef) -> PlanState:
    found = [
        item
        for item in snapshot.plans()
        if item.attention_id == reference.attention_id
    ]
    if len(found) != 1:
        raise AssertionError(
            f"plan attention {reference.attention_id!r} has {len(found)} matches"
        )
    return found[0]


def wait_for_plan_answer(
    client: BaqylauClient,
    reference: PlanRef,
    *,
    after_cursor: int,
    text: str,
    name: str,
    timeout: float,
) -> None:
    def exact_answer(snapshot: SessionSnapshot) -> bool | None:
        state = plan_state(snapshot, reference)
        found = [
            entry
            for entry in snapshot.entries
            if entry.cursor > after_cursor
            and entry.actor_id == state.actor_id
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "assistant"
            and entry.body.phase == "end_turn"
            and matches_final_answer(entry.body.content.text, text)
        ]
        if len(found) > 1:
            raise AssertionError(
                f"plan {name!r} has {len(found)} final answers equal to {text!r}"
            )
        return True if len(found) == 1 else None

    client.sessions.watch(reference.session).wait(
        f"plan {name!r} to be followed by final answer {text!r}",
        exact_answer,
        timeout=timeout,
    )


class PlanWorkDriver:
    def __init__(self, client: BaqylauClient) -> None:
        self._client = client

    def start(
        self,
        spec: SessionSpec,
        session: SessionRef,
        prompt: str,
    ) -> TurnRef:
        native_prompt = prompt
        if spec.harness == "codex":
            # A canonical turn finish can precede the native composer by a few
            # frames. Verify that the TUI is ready before entering plan mode,
            # then verify it again before submitting the actual plan prompt.
            # Without these checks, both accepted writes can land during the
            # transition and Codex silently drops the second one.
            mode = self._client.sessions.send(
                session,
                "/plan",
                replace_terminal_draft=True,
            )
            self._require_acknowledged(mode, "enter Codex plan mode")
        elif spec.harness == "claude_code":
            native_prompt = (
                "Your first action must be an EnterPlanMode tool call. Do not "
                "send assistant text before it. "
                f"{prompt} "
                "Your final action must be exactly one ExitPlanMode tool call "
                "that proposes the plan; do not answer in prose instead."
            )
        else:
            raise AssertionError(f"harness {spec.harness!r} has no plan work adapter")

        before = self._client.sessions.snapshot(session)
        lead = before.lead()
        receipt = self._client.sessions.send(
            session,
            native_prompt,
            replace_terminal_draft=spec.harness == "codex",
        )
        self._require_acknowledged(receipt, "start plan work")
        return TurnRef(
            session,
            native_prompt,
            receipt.cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )

    @staticmethod
    def _require_acknowledged(receipt: ActionReceipt, action: str) -> None:
        if receipt.status_code != 200 or receipt.outcome.status not in ("sent", "queued"):
            raise AssertionError(
                f"{action} action {receipt.request_id!r} was not accepted: "
                f"{receipt.outcome}"
            )
