# noaudit.py — the ONE audit-import-degradation helper (formerly four identical
# inline `_NoAudit` copies in ops/slots/tail/copy). The audit trail must never
# break a producer/tailer/handler, so every module that writes audit rows gets
# its `A` via load_audit(): the real `core.audit` when it imports, otherwise an
# inert stub that swallows every call. Stdlib-only leaf (like core.paths): its
# own import can only fail if the package itself is broken — the same failure
# mode as the callers' other unguarded core imports — so the degradation
# guarantee (a broken *audit* import degrades, never raises) is preserved.


class NoAudit:
    """Inert audit stand-in: every attribute is a swallow-everything no-op."""

    def __getattr__(self, _):
        return lambda *a, **k: None


def load_audit():
    """Return core.audit, or a NoAudit stub if the audit trail can't import."""
    try:
        from core import audit
        return audit
    except Exception:
        return NoAudit()
