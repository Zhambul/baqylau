# noaudit.py — the ONE audit-import-degradation helper (formerly four identical
# inline `_NoAudit` copies in ops/slots/tail/copy, later six more in plugin
# modules). The audit trail must never break a producer/tailer/handler, so
# EVERY module that writes audit rows — core, plugins, dispatcher-resident and
# detached alike — gets its `A` via load_audit(): the real `core.audit` when it
# imports, otherwise an inert stub that swallows every call. One convention, no
# tiers: even modules that only ever run inside the hook dispatcher must guard,
# because their `from core import audit` executes at MODULE IMPORT time — before
# hookkit.run()'s audit-then-swallow harness is ever entered — so an unguarded
# import failure there would kill the whole hook process (the hooks-must-never-
# fail invariant). The sole sanctioned direct import of core.audit is
# bin/claude-audit.py, the audit CLI entry over the audit module itself; a grep
# test (test_l0_units.test_no_module_bypasses_load_audit) pins this. Stdlib-only leaf (like core.paths): its
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
