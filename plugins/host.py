# plugins/host.py — the HOST-tool control interface every agent tool implements.
#
# frontends/base.Frontend is the precedent this copies: ONE class whose base is
# also the inert "none" host — every gesture is a silent no-op returning the same
# rejected-shaped result a caller already handles — plus a declared surface and a
# contract test. A plugin that adapts a host tool (claude_code; codex, copilot,
# opencode as they arrive) subclasses HostControl and OVERRIDES the gestures it
# can drive; the ones it leaves inert are the ones it cannot, and the dashboard
# GREYS the buttons behind them rather than firing a command the tool ignores.
#
# The load-bearing rule (the "one source of truth" correction): a host's
# CAPABILITIES are DERIVED from which gestures its subclass overrode — never an
# authored {name: bool} dict that a new gesture can silently fall out of sync
# with. `caps()` compares each declared gesture method against HostControl's own,
# so "did this subclass replace the inert default" IS the capability bit.
# tests/test_l1_contracts.py pins the surface in both directions, exactly as it
# does for plugins.PROVIDERS.
#
# CONTROL gestures are WHOLE gestures, not keystroke atoms: `interrupt` /
# `compact` / `answer a dialog`, not "press Escape". A future app-server-backed
# host (codex's app server, an SDK transport) can implement them without
# pretending to synthesize key events — the whole reason the interface is
# gestures and not a second Frontend. Each returns a small result dict
# {"status": <acknowledged|rejected|indeterminate>, "cid": <correlation id>} so
# an audit row can be tied to the gesture that produced it. In THIS phase
# (P1a) the gestures are DECLARED and claude_code overrides them so its caps
# read all-True, but the dashboard's POST handlers are NOT yet routed through
# them (they keep their existing bodies, byte-identical) — only `caps` gating is
# wired. P5 routes codex (and re-routes claude_code) through the gestures.
import itertools

# The CAPABILITY surface: each name is BOTH the cap key the dashboard gates a
# button on AND the HostControl method that implements the gesture. There is
# deliberately no name→method indirection and no {name: bool} table — the caps
# map is derived from these names against the class below, so a gesture cannot
# exist without a cap and a cap cannot name a method that isn't there. The
# dashboard's _caps_guard keys are exactly these strings.
GESTURES = (
    "interrupt",   # stop the current turn in place (post_interrupt)
    "send",        # deliver a message into the session (post_message) — NOT gated
    "rename",      # rename the session (post_rename)
    "rewind",      # drive the checkpoint/rewind menu (post_rewind / post_rewind_to)
    "migrate",     # hand the session to another account (post_migrate)
    "compact",     # summarise the conversation (post_command cmd=compact)
    "model",       # switch model (post_command cmd=model)
    "effort",      # switch reasoning effort (post_command cmd=effort)
    "ask",         # answer an AskUserQuestion dialog (post_answer)
    "plan",        # decide an ExitPlanMode dialog (post_plan_*)
)

# Statuses a gesture result may carry — a small closed vocabulary so the audit
# and the client agree on what "it happened" means without a per-host dialect.
ACK = "acknowledged"          # the host confirmed it did the thing
REJECTED = "rejected"         # the host refused / cannot do it (the inert base)
INDETERMINATE = "indeterminate"   # fired, outcome unconfirmable from here

_CID = itertools.count(1)


def _cid():
    """A process-local correlation id for a gesture result — cheap, monotone,
    unique within a server's life (enough to tie an audit row to the gesture
    that produced it; a host that spans processes may stamp its own)."""
    return "g%d" % next(_CID)


class HostControl:
    """The interface every host tool's control adapter implements, and its own
    inert "none" implementation (every gesture rejects). Subclass it, set
    `name`/`label`/`launchable`, and override the gestures you can drive."""

    name = ""             # the plugin's short name (matches plugins.owns_by)
    label = ""            # a human label for the new-session picker
    launchable = False    # can the dashboard launch a fresh session of this tool

    # --- capability derivation ------------------------------------------------
    def caps(self):
        """This host's capability map: {gesture: bool} over GESTURES, each bit
        DERIVED from whether this subclass replaced HostControl's inert default.
        Never an authored dict — that is the whole point (a new gesture can't
        drift out of sync with a hand-written table). The dashboard serves this
        as `data["caps"]` and gates every button on it; the server's
        _caps_guard reads the same map, so client and server can't disagree."""
        base = HostControl
        return {g: getattr(type(self), g) is not getattr(base, g)
                for g in GESTURES}

    # --- CONTROL gestures (whole gestures, not keystrokes) --------------------
    # `fe`/`win` are the frontend + its window id when the gesture drives a
    # terminal (an app-server host ignores them); `ctx` is an opaque per-request
    # bag (sid/log/sdb/tab) the caller threads through for audit. Each returns a
    # result dict via _rejected() in the base — a subclass returns ACK/
    # INDETERMINATE. None of these are CALLED by the dashboard in P1a; their
    # existence (as overrides) is what flips a subclass's caps True.
    @staticmethod
    def _rejected():
        return {"status": REJECTED, "cid": ""}

    @staticmethod
    def _ack():
        return {"status": ACK, "cid": _cid()}

    def interrupt(self, fe, win, ctx):
        """Stop the current turn in place (the session stays up)."""
        return self._rejected()

    def send(self, fe, win, text, ctx):
        """Deliver `text` into the session as a user message."""
        return self._rejected()

    def rename(self, sid, name, ctx):
        """Rename the session to `name` (through whatever channel owns the
        name — sid-keyed, since a parked session has no window)."""
        return self._rejected()

    def rewind(self, fe, win, ctx):
        """Open / drive the checkpoint-rewind menu."""
        return self._rejected()

    def migrate(self, sid, ctx):
        """Hand the session to another subscription account."""
        return self._rejected()

    def compact(self, fe, win, ctx):
        """Compact (summarise) the conversation."""
        return self._rejected()

    def model(self, fe, win, arg, ctx):
        """Switch the session's model to `arg`."""
        return self._rejected()

    def effort(self, fe, win, arg, ctx):
        """Switch the session's reasoning effort to `arg`."""
        return self._rejected()

    def ask(self, fe, win, answer, ctx):
        """Answer the session's open AskUserQuestion dialog."""
        return self._rejected()

    def plan(self, fe, win, decision, ctx):
        """Decide the session's open ExitPlanMode dialog."""
        return self._rejected()

    # --- launch / lifecycle plumbing (NOT capability-gated) -------------------
    # These aren't user buttons the dashboard greys — they are the argv/lifecycle
    # words the control plane composes, so they stay off GESTURES (a host may
    # provide them regardless of which gestures it drives). `launchable` above is
    # what the new-session picker reads; these are the how.
    def model_choices(self):
        """The model ids this host's ✦ menu offers — [] means the client uses
        its own default list (claude_code's are client-hardcoded; codex's differ,
        so it supplies them here). Not a gesture (no cap): a READ the session
        payload serves so the menu is the owning host's own vocabulary."""
        return []

    def effort_choices(self):
        """The reasoning-effort tokens this host's ✧ menu offers — [] means the
        client default. The effort twin of model_choices."""
        return []

    def resume_words(self, sid):
        """The argv words that RESUME session `sid` for this tool (claude:
        ['--resume', sid]); [] when the tool can't resume."""
        return []

    def launch_words(self, opts):
        """The argv words that LAUNCH a fresh session with `opts`
        (model/effort/prompt/…); [] when unsupported."""
        return []

    def launch_cmd(self, account_alias=""):
        """The login-shell command WORD plugins.launch_argv fixes for this host
        (`codex` for codex; `claude`/`c1`/`c2` for claude_code, which varies by
        the account switcher — hence the pre-validated `account_alias` a caller
        resolved). A host with no account switcher IGNORES the alias. This is the
        launch/lifecycle twin of launch_words: launch_words is the "$@" tail,
        launch_cmd is the fixed bareword ahead of it. Default: the host's own
        `name` (right for a host whose command IS its name, e.g. codex)."""
        return self.name

    def lifecycle_end(self, sid, log, reason):
        """Best-effort teardown when a session ends (park/close bookkeeping).
        The inert default does nothing."""
        return None


def caps_of(host):
    """The DERIVED caps of a host object, or {} for None — the one door callers
    use so a missing host degrades to an empty map (every cap absent)."""
    return host.caps() if host is not None else {}
