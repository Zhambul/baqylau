from audit.failures import CoalescingFailureRecorder
from audit.recorder import AuditRecorder


class RecordingAudit(AuditRecorder):
    def __init__(self) -> None:
        self.errors: list[tuple[str, str, object]] = []

    def error(self, session_or_log="", func="", context=None):
        self.errors.append((session_or_log, func, context))


def test_repeated_loop_failure_is_counted_instead_of_written_each_cycle():
    now = [0.0]
    audit = RecordingAudit()
    failures = CoalescingFailureRecorder(
        audit,
        "interpreter",
        clock=lambda: now[0],
        repeat_report_seconds=60.0,
    )

    def fail() -> None:
        try:
            raise ValueError("foreign record changed")
        except ValueError:
            failures.record("source read", {"session_id": "session-one"})

    fail()
    fail()
    fail()
    assert len(audit.errors) == 1

    now[0] = 60.0
    fail()

    assert len(audit.errors) == 2
    assert audit.errors[-1][2] == {
        "session_id": "session-one",
        "suppressed_repeats": 2,
    }


def test_changed_failure_shape_is_recorded_without_a_delay():
    audit = RecordingAudit()
    failures = CoalescingFailureRecorder(audit, "interpreter", clock=lambda: 0.0)

    for message in ("first drift", "second drift"):
        try:
            raise ValueError(message)
        except ValueError:
            failures.record("source read", {"session_id": "session-one"})

    assert len(audit.errors) == 2
