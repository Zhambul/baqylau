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

    def model_short(self, model_id):
        """Claude's display spelling of a model id ("claude-opus-4-8" →
        "opus-4.8") — model.short_model, the owner. Overridden here so the read
        model can ask the OWNING host instead of importing this plugin: the same
        grammar was being applied to every host's ids, so a codex agent card's
        model went through Claude's `claude-`-stripping version parser."""
        from plugins.claude_code import model
        return model.short_model(model_id)

    def model_default_effort(self, model_id):
        """Claude's model→default-effort table (model.model_default_effort, the
        owner): opus-4-7 → xhigh, the adaptive-reasoning families → high, else
        "". The twin of model_short, and the other half of the read model's old
        `plugins.claude_code.model` reach."""
        from plugins.claude_code import model
        return model.model_default_effort(model_id)

    def resume_words(self, sid):
        """`claude --resume <sid>` — the argv the dashboard's resume-&-send and
        the relimit migrator already compose (plugins.owns_by names claude_code
        as the ONE tool that can pick a conversation up this way)."""
        return ["--resume", sid] if sid else []

    def launch_words(self, opts):
        """The `claude` "$@" tail for a web new-session launch: `--resume`/
        `--continue` and `--model`/`--effort` riding as positional words ahead of
        the prompt (docs/dashboard.md *Resume & send*). This IS the word-builder
        that used to live inline in dashboard.http.post.session.post_new_session —
        moved here byte-identically so both hosts compose their launch through the
        one HostControl seam. `opts` = {resume, cont, model, effort, prompt}; each
        flag is emitted only when its value is set (`cont` is claude-only — codex
        has no --continue)."""
        opts = opts or {}
        resume = opts.get("resume") or ""
        cont = opts.get("cont")
        model = opts.get("model") or ""
        effort = opts.get("effort") or ""
        prompt = opts.get("prompt") or ""
        return ((["--resume", resume] if resume else [])
                + (["--continue"] if cont else [])
                + (["--model", model] if model else [])
                + (["--effort", effort] if effort else [])
                + ([prompt] if prompt.strip() else []))

    def launch_cmd(self, account_alias=""):
        """claude_code's login-shell command word: the account switcher's alias
        (`c1`/`c2`) or the plain `claude` default. `account_alias` is what the
        dashboard already resolved through plugins.account_alias (a registry-
        vetted bareword); this host varies by account, unlike codex."""
        return account_alias or "claude"


_HOST = None


def get():
    """The process singleton ClaudeCodeHost (the `host` provider's return)."""
    global _HOST
    if _HOST is None:
        _HOST = ClaudeCodeHost()
    return _HOST
