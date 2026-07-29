# plugins/codex/hostctl.py — codex's HostControl adapter.
#
# codex as a first-class HOST tool (plugins.host.HostControl) — what
# plugins.host_of / host_for(sid) / host_caps("codex") hand back for a codex
# session, and what the dashboard reads to know which control buttons to offer.
#
# Named `hostctl`, not `host`, for the same reason claude_code's is: the `host`
# PROVIDER function in plugins/codex/__init__.py would shadow a `host` submodule.
#
# codex overrides NO control GESTURE, so its derived caps read all-False and the
# dashboard GREYS every control button for a LIVE codex session (interrupt/rename/
# compact/ask/…): correct until P5 wires codex's app-server-backed gestures
# (interrupt via `turn/interrupt`, rename via `thread/name/set`, ask via the
# request_user_input reply, …). But `launchable=True`, `resume_words` AND
# `launch_words`/`launch_cmd` are LAUNCH/lifecycle plumbing, NOT capability-gated,
# so they are live: P6 wired the web new-session picker + a `codex resume`
# relaunch to compose through them (a codex session launches and resumes from the
# dashboard now, even though no live gesture is driveable yet). Leaving the
# gestures inert is deliberate, not a stub gap: a False cap is the honest answer
# while the app-server transport is unwired (docs/codex.md).
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

    def launch_words(self, opts):
        """The `codex` "$@" tail for a web new-session launch (verified against
        codex-cli 0.144.1). Fresh: `codex -C <cwd> -m <model>
        -c model_reasoning_effort=<eff> "<prompt>"`; resume: the same with the
        `resume <sid>` subcommand+id FIRST (both positionals — SESSION_ID before
        PROMPT), so the prompt trails after the flags and auto-submits. codex has
        NO `--effort` flag (effort is a `-c` config override) and NO `--continue`
        (resuming the most-recent row IS continue). `opts` = {resume, cwd, model,
        effort, prompt}; each fragment is emitted only when set. The command word
        is `codex` (launch_cmd — the base default over `name`)."""
        opts = opts or {}
        resume = opts.get("resume") or ""
        cwd = opts.get("cwd") or ""
        model = opts.get("model") or ""
        effort = opts.get("effort") or ""
        prompt = opts.get("prompt") or ""
        return ((["resume", resume] if resume else [])
                + (["-C", cwd] if cwd else [])
                + (["-m", model] if model else [])
                + (["-c", "model_reasoning_effort=" + effort] if effort else [])
                + ([prompt] if prompt.strip() else []))


_HOST = None


def get():
    """The process singleton CodexHost (the `host` provider's return)."""
    global _HOST
    if _HOST is None:
        _HOST = CodexHost()
    return _HOST
