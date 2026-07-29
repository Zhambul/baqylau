# plugins/codex/hostctl.py — codex's HostControl adapter.
#
# codex as a first-class HOST tool (plugins.host.HostControl) — what
# plugins.host_of / host_for(sid) / host_caps("codex") hand back for a codex
# session, and what the dashboard reads to know which control buttons to offer.
#
# Named `hostctl`, not `host`, for the same reason claude_code's is: the `host`
# PROVIDER function in plugins/codex/__init__.py would shadow a `host` submodule.
#
# THIS PHASE (P3) is READ-side only. codex overrides NO control gesture, so its
# derived caps read all-False and the dashboard GREYS every control button for a
# codex session (interrupt/rename/compact/ask/…): correct until P5 wires codex's
# app-server-backed gestures (interrupt via `turn/interrupt`, rename via
# `thread/name/set`, ask via the request_user_input reply, …). `launchable=True`
# and `resume_words` are lifecycle plumbing, NOT capability-gated, so they can be
# declared now — the new-session picker and a `codex resume` relaunch compose
# through them without any gesture being driveable yet. Leaving the gestures inert
# is deliberate, not a stub gap: a False cap is the honest answer while the
# app-server transport is unwired (docs/codex.md).
from plugins.host import HostControl


class CodexHost(HostControl):
    name = "codex"
    label = "Codex"
    launchable = True

    # No gesture overrides in P3 (caps all False — see the module header). P5 adds
    # them over the codex app-server transport.

    def resume_words(self, sid):
        """`codex resume <sid>` — codex's own conversation-resume argv (a codex
        session id IS its rollout uuid). The new-session/resume-&-send path
        composes a relaunch from this; [] when there is no sid."""
        return ["resume", sid] if sid else []


_HOST = None


def get():
    """The process singleton CodexHost (the `host` provider's return)."""
    global _HOST
    if _HOST is None:
        _HOST = CodexHost()
    return _HOST
