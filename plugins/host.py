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
# an audit row can be tied to the gesture that produced it.
#
# ROUTING, as of P2: EVERY host's POST handler dispatches through these methods.
# `_gesture_host(sid)` hands back a HostControl for every session (the DEFAULT
# host included — there is no inline fallback left), and the Claude bodies that
# used to sit in dashboard/http/post/*.py now live in
# plugins/claude_code/hostctl.py together with the five screen drivers they
# drive. A handler is guards + `host.<gesture>(…)` + the HTTP mapping.
#
# What a gesture OWNS, therefore: the terminal driving, its own `web-*`
# state_files rows and `A.error` diagnostics (row ORDER inside a gesture is
# load-bearing), and the host-specific decisions inside it. What the CALLER
# still owns: authentication, the caps guard, resolving the live window, tab-
# state refusals, the read model, and every HTTP status. `ctx` is the bag those
# two halves meet in (below).
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

# The QUICK-COMMAND vocabulary — the closed set of slash commands the header's
# leading action row types into a TUI, as {wire word: (method, the cap it rides)}.
# The WIRE word is what the client POSTs to /command and what the served
# `quick_commands` rows are keyed by; the METHOD is the HostControl body that
# runs it, and WHICH commands a host offers is DERIVED from those overrides
# (implements()), exactly as caps are — never an authored per-host list.
#
# `rename` is the ARGLESS auto-rename (the ✦ button): its method is `autoname`
# and its cap is `rename`, which is why the two names differ here at all. This
# table is the one owner of that mapping — dashboard/http/post/typing.py's
# _caps_guard reads its caps out of it rather than re-spelling them.
QUICK_COMMANDS = {
    "compact": ("compact", "compact"),
    "model": ("model", "model"),
    "effort": ("effort", "effort"),
    "rename": ("autoname", "rename"),
}

# The rest of the surface, declared HERE beside GESTURES rather than in the
# contract test — because a table the test owns can only ever agree with itself.
# It did: adding a HostControl method AND overriding it in a host landed with no
# failure at all, since the "declared surface" the coverage check compared
# against was the test file's own two tuples. Now the product declares its
# families and the test asserts the partition is TOTAL over the class's public
# callables, so a new method fails until it is filed under one of them.
#
#   SIBLINGS   — real gestures that share another gesture's cap (a cap must map
#                to exactly one method or the derivation stops being the source
#                of truth), so the caller gates them on that cap and the
#                gesture's own `unsupported` result is what turns "this host has
#                the cap but not this half" into a 409.
SIBLINGS = {"rewind_to": "rewind", "autoname": "rename",
            "plan_options": "plan", "deliver": "ask"}

#   VOCABULARY — not gestures at all: the WORDS, grammars and screen READS a
#                host declares so the control plane can refuse an unknown one by
#                name instead of typing a foreign command into its TUI.
VOCABULARY = ("mention", "clear_input", "turn_live", "ask_declines",
              "plan_decisions", "rewind_modes", "rewind_mode_label",
              "command_floor", "title_key", "title_sig",
              "input_box", "ask_region", "typed_input", "lifecycle_end")

#   PLUMBING   — the launch/lifecycle and model-vocabulary reads. Not user
#                buttons (nothing greys on them) and not a refusal vocabulary
#                either: they are how the control plane COMPOSES a launch and
#                spells a model id in the host's own words.
PLUMBING = ("model_choices", "effort_choices", "model_default",
            "effort_default", "model_short", "model_default_effort",
            "launch_words", "launch_cmd")

#   INFRASTRUCTURE — the capability DERIVATION itself, which is the interface's
#                own machinery rather than anything a host implements. The only
#                public callables that are in no family.
INFRASTRUCTURE = ("implements", "caps")

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

    # (P6 deleted `lead_prose`. It declared that a host's own session stream
    # carries its prose TWICE — as paint ops and again as plugins.conversation
    # records — so the session view could drop one. It was already a trait rather
    # than a host name, but a DECLARATION is still the wrong tier for it: the
    # producer stamps `bubbled` on the very ops it double-emits (core/ops.py),
    # which is per-OP and cannot be out of date, where a per-HOST flag says
    # nothing about which of that host's ops are affected. opshtml.rebubbled now
    # honours the flag in every view and no host declares anything.)

    # --- capability derivation ------------------------------------------------
    def implements(self, name):
        """Did THIS subclass replace HostControl's inert `name`? The same
        derivation caps() applies to the GESTURES, exposed for the surface that
        has no cap: a caller about to PAY for something before asking (the ghost
        -suggestion probe resolves a frontend and a `kitten @ ls` window map
        first) can skip the whole errand for a host whose answer is the inert
        default. Never a host-name check — that is the thing this refactor
        deletes."""
        return (getattr(type(self), name, None)
                is not getattr(HostControl, name, None))

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
    # terminal (an app-server host ignores them); `ctx` is the per-request bag
    # the caller threads through. Its COMMON keys are:
    #
    #   sid / log / sdb  — the session and its audit targets (every gesture
    #                      writes its own `web-*` state_files row through them)
    #   action           — the KIND of that row ("web-send", "web-interrupt", …),
    #                      so the gesture's row and the caller's refusal rows
    #                      share one vocabulary
    #   verb             — the word an `A.error` phrase uses ("interrupt")
    #   tab              — the window's tab state at gesture time
    #   queueing         — bool: is this tab one where typed text lands in the
    #                      host's own message QUEUE rather than starting a turn
    #                      (the caller's policy table, not the host's)
    #   row              — the audit `sessions` row (transcript_path/cwd)
    #   transcript       — that row's transcript path, for a gesture that reads
    #                      the session's own file to VERIFY itself (codex's
    #                      interrupt watches its rollout for `turn_aborted`).
    #                      Host-NEUTRAL by name on purpose: it is one column of
    #                      the caller's row, and spelling it `rollout` put one
    #                      host's word for the file into every host's contract
    #   box              — the WEB's input-box stash: `.draft` (text we left in
    #                      the box), `.set_draft(text) -> bool`, `.note_send()`.
    #                      INJECTED like `fe`, for the same reason: the stash is
    #                      the DASHBOARD's memory of its own pastes (its terminal
    #                      draft sync writes it too) while only the host knows
    #                      WHEN the box changed under it.
    #
    # Per-gesture keys are named in each method's docstring. Each returns a
    # result dict: {"status", "cid", "ok": bool} plus whatever the caller needs
    # to shape its reply. The INERT base returns `_unsupported(<gesture>)` — a
    # REJECTED result carrying the gesture name, which is how a caller tells "this
    # host does not do that" (409, naming the capability) from "it tried and
    # failed" (502): a plain `_rejected()` means the latter, and hosts use it for
    # real failures.
    @staticmethod
    def _rejected():
        return {"status": REJECTED, "cid": ""}

    @staticmethod
    def _unsupported(gesture):
        """The inert default's result: this host does not implement `gesture`.
        Distinct from _rejected() (which a host returns when it TRIED and could
        not) — the `unsupported` key is what lets a caller answer 409 with the
        capability rather than 502 with a failure."""
        return {"status": REJECTED, "cid": "", "unsupported": gesture}

    @staticmethod
    def _ack():
        return {"status": ACK, "cid": _cid()}

    def interrupt(self, fe, win, ctx):
        """Stop the current turn in place (the session stays up)."""
        return self._unsupported("interrupt")

    def send(self, fe, win, text, ctx):
        """Deliver `text` into the session as a user message. NOT caps-gated (the
        composer is always reachable), but the BODY is host-routed: the mention
        grammar, the input clear, whether a paste grabs the clipboard image and
        how a turn's liveness is probed all differ per host."""
        return self._unsupported("send")

    def rename(self, sid, name, ctx):
        """Rename the session to `name` (through whatever channel owns the
        name — sid-keyed, since a parked session has no window; `fe`/`win` ride
        in ctx for the LIVE half)."""
        return self._unsupported("rename")

    def rewind(self, fe, win, ctx):
        """Open the checkpoint-rewind menu (for the TERMINAL user to drive)."""
        return self._unsupported("rewind")

    def migrate(self, sid, ctx):
        """Hand the session to another subscription account."""
        return self._unsupported("migrate")

    def compact(self, fe, win, ctx):
        """Compact (summarise) the conversation."""
        return self._unsupported("compact")

    def model(self, fe, win, arg, ctx):
        """Switch the session's model to `arg`."""
        return self._unsupported("model")

    def effort(self, fe, win, arg, ctx):
        """Switch the session's reasoning effort to `arg`."""
        return self._unsupported("effort")

    def ask(self, fe, win, answer, ctx):
        """Answer the session's open question dialog."""
        return self._unsupported("ask")

    def plan(self, fe, win, decision, ctx):
        """Decide the session's open plan dialog — `decision` is one of the shapes
        plan_decisions() names."""
        return self._unsupported("plan")

    # --- gesture SIBLINGS that share another gesture's cap --------------------
    # Not in GESTURES (a cap must map to exactly one method, or the derivation
    # stops being the source of truth), but real gestures with real bodies. Each
    # names the cap it rides so the caller gates it on the same bit.

    def rewind_to(self, fe, win, target, mode, ctx):
        """Restore the session to the checkpoint of a SPECIFIC prompt — the
        web's own end-to-end rewind, where `rewind` merely opens the menu. Rides
        the `rewind` cap. `target` is the prompt's full text, `mode` one of
        rewind_modes(), `ctx['ups']` a jump hint the driver's verify corrects."""
        return self._unsupported("rewind_to")

    def autoname(self, fe, win, ctx):
        """Let the host NAME THE SESSION ITSELF (no name supplied) — the ✦ auto
        button. Rides the `rename` cap: being told a name and inventing one are
        the same capability from the button's point of view, and a host may
        implement one without the other (which is exactly what the 409 says)."""
        return self._unsupported("autoname")

    def plan_options(self, fe, win, ctx):
        """The decision options the host's OPEN plan dialog offers, as
        {ok, options: [{digit, label}, …]}. Rides the `plan` cap. A separate
        method because a host may have to READ THEM OFF THE SCREEN (Claude Code's
        labels vary with the session's permission mode) where another simply
        knows them."""
        return self._unsupported("plan_options")

    def deliver(self, fe, win, text, ctx):
        """Put `text` into the session as a message with NO draft/liveness
        machinery — the follow-up a dialog decline hands over. Rides the `ask`
        cap (its one caller is the ask card's typed-answer route)."""
        return self._unsupported("deliver")

    # --- per-host VOCABULARY the control plane validates against --------------
    # Not gestures (no caps): these are the WORDS and grammars a host accepts, so
    # the dashboard can refuse an unknown one with a 409 that NAMES the host's
    # own vocabulary instead of typing a foreign command into its TUI.

    # Does a bracketed paste into this host's TUI grab whatever IMAGE the
    # clipboard holds? Claude Code's does (there is no opt-out, so its gestures
    # empty an image clipboard first); codex's does not, and must not pay the
    # osascript round-trip. A DECLARATION, so the guard is applied by the host
    # that needs it rather than to everyone (docs/dashboard.md *Clipboard-image
    # guard*).
    paste_grabs_clipboard_image = False

    def mention(self, path):
        """How this host's input names an ATTACHED FILE inline (Claude Code:
        `@path`, which its TUI resolves and attaches). "" means the host has no
        mention grammar — the caller then delivers the bare PATH, which is
        strictly better than typing another tool's sigil as literal text."""
        return ""

    def clear_input(self, fe, win, prev_text=""):
        """Kill whatever is in the input box so a paste REPLACES rather than
        appends; returns the number of lines killed. The key repertoire is the
        host's (Claude Code: Ctrl+U/Ctrl+K per line with a backspace between).
        The inert default does nothing and says so: a host whose input model is
        unknown must not be sent line-kill keystrokes on spec."""
        return 0

    def turn_live(self, fe, win, ctx=None):
        """Is a turn ACTUALLY running? True / False / None (can't tell). The
        caller uses it to decide whether a message will QUEUE — a promise the tab
        colour alone cannot make (a turn cancelled at the terminal can leave the
        colour frozen). None is the honest default: the caller then trusts the
        tab, exactly as it did before any host had a probe."""
        return None

    def ask_declines(self):
        """The words that DECLINE this host's question dialog rather than
        answering it (Claude Code: "chat", its 'Chat about this' row). ()
        means the host has no decline — the caller 409s naming the vocabulary
        instead of silently dropping the flag and answering the question."""
        return ()

    def plan_decisions(self):
        """The decision shapes this host's plan dialog accepts, in the order a
        "need one of …" message should name them: "decide" (a numbered row),
        "feedback" (free text), "dismiss" (keep planning). () for a host with no
        plan dialog."""
        return ()

    def rewind_modes(self):
        """The restore modes this host's rewind offers (Claude Code:
        both/conversation/code). () when it cannot rewind."""
        return ()

    def rewind_mode_label(self, mode):
        """The web menu's row text for one rewind_modes() mode ("restore code
        and conversation"). The words are the HOST's — they name what ITS
        checkpoint menu restores — so the client stops carrying a copy of
        Claude Code's three. "" for a mode this host does not offer."""
        return ""

    def command_floor(self, cmd):
        """How many of YOUR prompts a conversation needs before this host will
        accept quick command `cmd` (a QUICK_COMMANDS wire word) — the REFUSAL
        FLOOR the dashboard greys the button below, so it stops typing a command
        the TUI is going to bounce. 0 = no floor (the honest default: a host that
        has not measured a refusal must not invent one).

        Claude Code's two are measured, not guessed: `/compact` bounces with
        "Not enough messages to compact" on a barely-started chat, and a bare
        `/rename` with "Could not generate a name: no conversation context yet"
        on an empty one. They were client constants (COMPACT_MIN_PROMPTS = 2 and
        an inline `prompts < 1`) applied to EVERY host."""
        return 0

    def title_key(self, tpath):
        """The durable rename-override key for one of this host's transcripts
        (its filename STEM), or "" when the path isn't one / the host has no such
        key. Both sides of the override — the parked rename's write and the read
        model's lookup — derive it here, so the filename convention stays the
        OWNING host's fact."""
        return ""

    def title_sig(self, tpath):
        """A cheap FRESHNESS STAMP for this transcript's session title — any
        string that CHANGES when the title could have changed. "" (the default)
        means "the transcript file is the whole story", which is exactly true
        for a host that writes the name into the transcript itself.

        It exists because that is NOT true of every host: codex keeps the name
        in its own state index (`threads.title`), so a codex rename leaves the
        rollout byte-identical — and the read model's title memo is keyed on
        (path, SIZE). The list page therefore served the pre-rename title
        forever, which is the bug this closes. Asking the owning host is the
        only way the caching tier can know; sniffing what makes one host's
        title stale is the thing this interface deletes.

        Cheap is part of the contract: it is read on every title lookup (a slow
        SSE tick, per session card), where the memo it guards exists precisely
        so the expensive read is not. A stat is the right order of magnitude."""
        return ""

    # --- screen READS the web MIRRORS (no keys pressed) -----------------------
    # Read-only probes of a live window. They are host methods, not providers,
    # because they need the frontend and are pure TUI geometry; the inert
    # defaults are what make "no probe for a host we can't read" the default
    # rather than a host-name check in the read model.

    def input_box(self, fe, win, ctx=None):
        """(ghost, typed) — the host's own pre-filled SUGGESTION in its input
        box and the REAL text the user has typed there. (None, None) when the
        host has no such geometry, which is also "we could not read it": both
        mean no news, and the callers already treat it that way."""
        return None, None

    def ask_region(self, fe, win):
        """The open question dialog's REGION text, isolated from the rest of the
        screen so a ticking status line isn't mistaken for activity — the
        notifier diffs it to tell "you are answering at the terminal" from "the
        question is unread". "" = no such dialog on screen; None = no reading."""
        return None

    def typed_input(self, fe, win):
        """The REAL (non-ghost) text in the input box — the 'still composing at
        the terminal' signal on a settled tab. None when unreadable / no such
        geometry."""
        return None

    # --- launch / lifecycle plumbing (NOT capability-gated) -------------------
    # These aren't user buttons the dashboard greys — they are the argv/lifecycle
    # words the control plane composes, so they stay off GESTURES (a host may
    # provide them regardless of which gestures it drives). `launchable` above is
    # what the new-session picker reads; these are the how.
    def model_choices(self):
        """The model ids this host's ✦ menu offers — its OWN vocabulary, served
        both per-session (the quick-command picker) and per-host (/api/hosts, the
        new-session form). [] means the host declares none, and the menu is then
        EMPTY: a client default here used to mean "an unknown host is offered
        Claude's models", which is the thing this interface exists to delete.
        Not a gesture (no cap) — a READ."""
        return []

    def effort_choices(self):
        """The reasoning-effort tokens this host's ✧ menu offers — [] = an empty
        menu, never another host's levels. The effort twin of model_choices."""
        return []

    def model_default(self):
        """The model a FRESH launch of this host picks when nothing is
        remembered — the new-session form's first-ever selection. "" = let the
        tool's own config decide (the launch omits the flag)."""
        return ""

    def effort_default(self):
        """The reasoning effort a FRESH launch picks when nothing is remembered.
        The effort twin of model_default; "" = the tool's own default."""
        return ""

    # How a menu ROW matches the model a session is actually RUNNING, so the ✦
    # picker can mark the current one and a just-clicked switch can clear its
    # optimistic label. Two shapes exist and neither can be sniffed off an id:
    #
    #   "exact"  — the rows ARE full model ids (codex: `gpt-5.6-terra`), so the
    #              running id matches a row verbatim.
    #   "family" — the rows are FAMILY words (claude_code: `opus`), so the
    #              running id's leading word is what matches.
    #
    # The client used to decide by prefix-sniffing the id (`startsWith("gpt-")`),
    # which mis-reads every id neither host coined AND silently mis-matches a
    # real one: `gpt-5.4-codex` is not in codex's menu, and a family compare
    # would have marked `gpt-5.4` as the current row. "exact" is the base because
    # it never claims a row the host did not name.
    model_match = "exact"

    # --- model VOCABULARY (a host reading its own model ids) ------------------
    # Not gestures either: these turn a raw model id into the words this host
    # uses for it. They live on HostControl rather than behind a model-id-keyed
    # registry fan-out because a model id carries no reliable ownership claim —
    # answering "whose id is `gpt-5.4-codex`?" by grammar is the sniffing this
    # whole refactor deletes. The CALLER always knows the owning host already: it
    # holds the file the id came out of (an agent row's transcript), so it
    # resolves plugins.host_of(path) and asks THAT host. Base returns the honest
    # pass-through/empty, so an unclaimed file degrades to the raw id and no
    # effort rather than another tool's vocabulary.
    def model_short(self, model_id):
        """`model_id` in this host's own DISPLAY spelling (Claude Code:
        "claude-opus-4-8" → "opus-4.8"). The base is the identity — a host whose
        ids are already display-ready (codex's `gpt-5.4-codex`) needs no
        override, and passing an id through unchanged is always safe where
        mangling it through a foreign grammar is not."""
        return model_id or ""

    def model_default_effort(self, model_id):
        """The reasoning-effort level `model_id` runs at when the session names
        none — this host's own model→default table ("" when the model has no
        adaptive reasoning, or the host has no such notion). Read only as the
        LAST fallback, after the session's own effort."""
        return ""

    # (No `resume_words`. It declared "the argv words that RESUME session
    # <sid>", three hosts implemented it, and NOTHING ever called it: the one
    # place a resume argv is composed is `launch_words`, whose `opts["resume"]`
    # emits the flag inline — which is also where a host that spells resumption
    # differently would say so. A second seam for the same word could only
    # disagree with the first, and a declared method with no caller reads as a
    # contract the code honours.)

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
        """Best-effort teardown after the WEB closed a session's tab (the ■ stop
        button), called by post_stop once the close returns.

        For both hosts today this is genuinely a no-op and BOTH say so by
        overriding it: Claude Code fires SessionEnd on exit and codex's watcher
        notices its host pid is gone, and each routes into core.hostpane.host_end
        on its own — the tab close is the whole gesture. It stays declared (and
        called) because "the web closed your tab" is the one lifecycle event no
        hook of the host's own describes, so a future host that needs to park
        something has a place to do it; the inert default is the honest answer
        for a host that hasn't thought about it."""
        return None


def caps_of(host):
    """The DERIVED caps of a host object, or {} for None — the one door callers
    use so a missing host degrades to an empty map (every cap absent)."""
    return host.caps() if host is not None else {}
