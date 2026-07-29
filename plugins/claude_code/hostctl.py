# plugins/claude_code/hostctl.py — Claude Code's HostControl adapter.
#
# The host tool's control surface (plugins.host.HostControl). Claude Code drives
# EVERY gesture, so it overrides every one and its derived caps read all-True —
# which is precisely what keeps the dashboard's _caps_guard a no-op for a Claude
# session (byte-identical: the guard never fires when the cap is True).
#
# Named `hostctl`, not `host`, ON PURPOSE: the `host` PROVIDER function in
# plugins/claude_code/__init__.py would shadow a submodule named `host` (a
# package attribute defined in __init__ wins over a same-named submodule for
# `from plugins.claude_code import host`), so the module carries a distinct name.
#
# The gesture bodies are DECLARED but NOT YET ROUTED (P1a): the dashboard's POST
# handlers keep their existing bodies and do not call these — the class exists in
# this phase to (a) flip the caps and (b) be the seam a later phase routes both
# claude_code and codex through. Each override is a distinct function object
# (that identity is what caps() reads as "overridden"), so they are written out
# rather than generated in a loop, which would share ONE object and read as
# not-overridden.
from plugins.host import HostControl


class ClaudeCodeHost(HostControl):
    name = "claude_code"
    label = "Claude Code"
    launchable = True

    # Every gesture is DECLARED (caps => all True) but unrouted in P1a; if one is
    # called before it is wired, _deferred returns a harmless INDETERMINATE the
    # caller can audit rather than a silent no-op. The dashboard does not call
    # them yet.
    def _deferred(self, gesture):
        r = self._ack()
        r["status"] = "indeterminate"
        r["deferred"] = gesture
        return r

    def interrupt(self, fe, win, ctx):
        return self._deferred("interrupt")

    def send(self, fe, win, text, ctx):
        return self._deferred("send")

    def rename(self, sid, name, ctx):
        return self._deferred("rename")

    def rewind(self, fe, win, ctx):
        return self._deferred("rewind")

    def migrate(self, sid, ctx):
        return self._deferred("migrate")

    def compact(self, fe, win, ctx):
        return self._deferred("compact")

    def model(self, fe, win, arg, ctx):
        return self._deferred("model")

    def effort(self, fe, win, arg, ctx):
        return self._deferred("effort")

    def ask(self, fe, win, answer, ctx):
        return self._deferred("ask")

    def plan(self, fe, win, decision, ctx):
        return self._deferred("plan")

    def resume_words(self, sid):
        """`claude --resume <sid>` — the argv the dashboard's resume-&-send and
        the relimit migrator already compose (plugins.owns_by names claude_code
        as the ONE tool that can pick a conversation up this way)."""
        return ["--resume", sid] if sid else []


_HOST = None


def get():
    """The process singleton ClaudeCodeHost (the `host` provider's return)."""
    global _HOST
    if _HOST is None:
        _HOST = ClaudeCodeHost()
    return _HOST
