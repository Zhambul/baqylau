# harness/impl/codex/canonical/vocabulary.py — codex's SYNTHETIC vocabulary.
#
# Telling codex MACHINERY from a real conversation turn — STRUCTURAL, not an
# ever-growing allowlist (the ONE owner of codex's synthetic vocabulary,
# styleguide table; a presenter must not re-encode it). Two structural facts +
# one tiny supplement:
#
#   1. ROLE. A `response_item/message` with role developer/system is the SYSTEM
#      CHANNEL — never a conversation turn (the context codex re-injects, the
#      multi-agent/permissions/skills scaffolding). Caught by role alone, so a new
#      developer-role block needs no list entry.
#   2. `<tag>` WRAPPER. Every codex role=user system injection is a
#      `<lower_or spaced tag>…` block (<recommended_plugins>, <environment_context>,
#      <turn_aborted>, …); a real prompt is free prose. So a role=user `<tag>` block
#      is synthetic BY DEFAULT — robust to new tags — EXCEPT an INPUT wrapper.
#
# Both rollout registers read this module: the event_msg one (events.py) to
# unwrap a `<task>` prompt, the response_item one (items.py) for the whole test.
import re

# INPUT_WRAPPERS: a role=user `<tag>` that IS a real turn, not scaffolding —
# codex delivers a subagent's task as `<task>…</task>`. Kept AND unwrapped to its
# inner text (strip_input_wrapper) so the bubble reads as the prompt, not markup.
INPUT_WRAPPERS = ("task",)

# The ASSISTANT wrapper that is a PLAN. codex's plan mode has no tool call and
# no event of its own: the proposal arrives as an ordinary role=assistant
# response_item whose text is wrapped in `<proposed_plan>…</proposed_plan>`, and
# it is the ONLY register it appears in (that turn writes no `agent_message`).
# So the structural synthetic rule — "a wrapper tag we don't know is codex
# machinery" — swallowed it, and a codex plan session showed the plan NOWHERE on
# the web while every other bubble in the thread rendered (the reported bug).
PLAN_WRAPPER = "proposed_plan"

# The NON-tag synthetic prefixes the structural rule can't see (codex machinery
# that is neither role-marked nor `<tag>`-wrapped). The `<…>` entries the old list
# carried are now caught structurally by fact 2 above.
SYNTHETIC_PREFIXES = (
    "Approved command prefix saved:",
    "# AGENTS.md instructions",
)

_WRAP_RE = re.compile(r"^<([A-Za-z][A-Za-z0-9_ -]*)>")


def _wrapper_tag(text):
    """The leading `<tag>` name of a wrapper block (lowercased, inner spaces kept
    — `<permissions instructions>` → 'permissions instructions'), or "". codex
    wraps every system injection AND the subagent task in one such tag."""
    m = _WRAP_RE.match((text or "").lstrip())
    return m.group(1).strip().lower() if m else ""


def plan_body(text):
    """The PLAN markdown inside a `<proposed_plan>…</proposed_plan>` assistant
    message, or "" when this text is not one. The one reader of PLAN_WRAPPER, so
    the parser and any later consumer agree on where the plan starts."""
    s = (text or "").lstrip()
    if _wrapper_tag(s) != PLAN_WRAPPER:
        return ""
    inner = s[len("<%s>" % PLAN_WRAPPER):]
    close = "</%s>" % PLAN_WRAPPER
    if inner.rstrip().endswith(close):
        inner = inner.rstrip()[:-len(close)]
    return inner.strip()


def strip_input_wrapper(text):
    """A role=user INPUT wrapper (`<task>…</task>`) reduced to its inner text — the
    real prompt a subagent is spawned with; any other text is returned unchanged.
    The ONE owner of the unwrap, so both registers (event_msg + response_item) that
    a prompt can arrive in de-double to the same bubble."""
    s = (text or "").strip()
    tag = _wrapper_tag(s)
    if tag not in INPUT_WRAPPERS:
        return text
    inner = s[len("<%s>" % tag):]
    close = "</%s>" % tag
    if inner.rstrip().endswith(close):
        inner = inner.rstrip()[:-len(close)]
    return inner.strip()


def is_synthetic(text, role=""):
    """Is this `chat` text codex MACHINERY rather than a conversation turn?
    Structural (see the vocabulary block above), not an allowlist:
      * role developer/system      -> the system channel, always synthetic.
      * role user (or unknown)     -> a `<tag>` wrapper is a system injection
                                      UNLESS it is an INPUT wrapper (`<task>`);
                                      free prose is a real prompt.
      * the non-tag SYNTHETIC_PREFIXES supplement.
    The one reader of that vocabulary."""
    r = (role or "").strip().lower()
    if r in ("developer", "system"):
        return True
    s = (text or "").lstrip()
    if s.startswith(SYNTHETIC_PREFIXES):
        return True
    tag = _wrapper_tag(s)
    return bool(tag) and tag not in INPUT_WRAPPERS
