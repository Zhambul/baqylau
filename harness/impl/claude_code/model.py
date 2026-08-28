# harness/impl/claude_code/model.py — model / effort / context-window resolution
# for agents (historical name: claude_model.py — that compat shim is deleted).
#
# Extracted from claude-substream.py, where ~250 lines of config-dir walking,
# frontmatter parsing, and window tables had accreted (CLAUDE.md had always
# described this responsibility as shared-module territory). Everything here is
# a PURE function of its arguments plus the environment — no per-agent globals —
# so the substream (and anything else that needs to answer "what model/effort/
# window is this agent actually running?") composes these.
#
# Background, in one place:
# - There is NO context-size frontmatter field (docs): the window follows the
#   resolved MODEL, which an agent can pin explicitly (e.g. `model: opus[1m]`).
#   Sonnet 5 / Fable 5 / Opus 4.6-4.8 run 1M by default (no suffix), older
#   models are 200k unless [1m], and CLAUDE_CODE_DISABLE_1M_CONTEXT caps all.
# - Effort is NOT recorded in any transcript — it's config-only, resolved
#   env > agent-def frontmatter `effort` > session `effortLevel` > the model's
#   own default (docs: high on Opus 4.8/4.6 / Sonnet 5 / Sonnet 4.6 / Fable 5,
#   xhigh on Opus 4.7). A session-only `/effort` isn't persisted, so it can't
#   be seen here.
import os
import time
from collections.abc import Mapping
from enum import StrEnum

from pydantic import ValidationError

from core import env as EV
from domain.ids import ActorId
from domain.values import ModelReference
from harness.impl.claude_code.canonical import records

# How much of a transcript's tail session_model() scans for the last assistant
# turn: the latest turn is near the end, so a bounded read stays cheap even on
# long sessions.
TAIL_SCAN_BYTES = 256 * 1024


class ClaudeCodeModel(StrEnum):
    FABLE = "fable"
    OPUS = "opus"
    SONNET = "sonnet"
    HAIKU = "haiku"
    CLAUDE_FABLE_5 = "claude-fable-5"
    CLAUDE_OPUS_5 = "claude-opus-5"
    CLAUDE_OPUS_4_8 = "claude-opus-4-8"
    CLAUDE_SONNET_5 = "claude-sonnet-5"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5"
    CLAUDE_HAIKU_4_5_20251001 = "claude-haiku-4-5-20251001"


class ClaudeCodeEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


def claude_dirs(
    config: str,
    start: str | None = None,
    nearest_only: bool = False,
    env_pin: bool = True,
) -> list[str]:
    """Every `.claude` directory to consult for project-level config (agents, settings),
    NEAREST-FIRST, always ending with the configured user directory. Used instead
    of a bare os.getcwd() lookup, because a subagent/teammate frequently runs in a
    SUBDIRECTORY (a task's `.zhambyl/tasks/<t>/db`, or a git worktree under
    `.zhambyl/parallel/<wt>`) where `<cwd>/.claude` lacks the def/field we need.

    Resolution:
      - $CLAUDE_PROJECT_DIR (the harness's own project override; same as claude-split.py)
        pins the single project `.claude` when set;
      - otherwise walk UP from `start`, collecting EVERY ancestor `.claude` (stopping at
        `/` or $HOME) — or, with nearest_only=True, only the NEAREST one (split.py's
        historical semantics for the mirror-width env settings: the project is "the
        nearest .claude above cwd", full stop — it never fell through an intermediate
        `.claude` to an outer repo's, and a width preference must not start doing so).
    Collecting *all* of them — not just the nearest — is deliberate for agents/settings
    resolution: an intermediate dir may hold its own `.claude/` that is missing `agents/`
    or the field we want (e.g. a task's `db/.claude`), and we must still fall through to
    the repo-root `.claude` above it. Nearest-first means a more-specific dir still
    overrides a parent. Since the agent-defs here are UNTRACKED (present only in the main
    working tree, absent from worktree checkouts), a nested worktree resolves up to the
    main repo's defs correctly.

    env_pin=False ignores $CLAUDE_PROJECT_DIR entirely: a caller resolving an
    ARBITRARY directory's config (the dashboard's slash-command discovery walks
    OTHER sessions' cwds) must not have every lookup pinned to whatever project
    happened to spawn the calling process.

    `config` is the trailing user directory. An out-of-process reader must use
    the directory from application startup, not the calling process environment."""
    dirs = []
    env = (os.environ.get("CLAUDE_PROJECT_DIR") or "").strip() if env_pin else ""
    if env:
        c = os.path.join(env, ".claude")
        if os.path.isdir(c):
            dirs.append(c)
    else:
        d = os.path.abspath(start or os.getcwd())
        home = os.path.expanduser("~")
        while d not in ("/", home):
            c = os.path.join(d, ".claude")
            if os.path.isdir(c):
                dirs.append(c)
                if nearest_only:
                    break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    home_claude = config
    if home_claude not in dirs:
        dirs.append(home_claude)
    return dirs


DISABLE_1M = bool(EV.env_int("CLAUDE_CODE_DISABLE_1M_CONTEXT", 0))
# Substrings of real model ids. Opus 5 (like Sonnet 5 / Fable 5) has NO 200k
# variant — 1M is both its default and its maximum — so a PINNED `claude-opus-5`
# must resolve here; only the bare `opus` alias below covered it before, and the
# id is what the transcript records (the ctx bars read 5× over on a 200k window).
KNOWN_1M = ("fable-5", "sonnet-5", "opus-5", "opus-4-6", "opus-4-7", "opus-4-8",
            "sonnet-4-6")


def window(model: str | None) -> int | None:
    """A model alias / id (with or without [1m]) -> its context window; None if
    empty (so a caller can fall through a precedence list)."""
    if not model:
        return None
    m = model.lower().strip()
    if "haiku" in m:
        return 200_000
    if "[1m]" in m:
        return 1_000_000
    if any(tok in m for tok in KNOWN_1M):
        return 1_000_000
    if m in ("opus", "sonnet", "fable"):     # current aliases -> latest gen -> 1M
        return 1_000_000
    return 200_000                           # older / unknown pinned versions


def context_window(*models: str | None) -> int:
    """The context window for the first of `models` that resolves (a precedence
    list, best-known-first); 200k when none do or the 1M kill-switch is set."""
    if DISABLE_1M:
        return 200_000
    for m in models:
        w = window(m)
        if w:
            return w
    return 200_000


def context_used(usage: records.MessageUsage) -> int:
    """The occupied context window from ONE assistant message's usage dict:
    every input token the model saw — fresh + just-cached + replayed-from-cache.
    output_tokens is excluded (what the model produced back, not context). 0
    when usage is absent/malformed. The ONE owner of this arithmetic
    (styleguide table) — the substream's ctx tag/footer and
    transcript.context_probe (the dashboard's saturation chips) both call it."""
    def _tokens(value: int | float | None) -> int:
        return int(value) if isinstance(value, (int, float)) else 0

    return (
        _tokens(usage.input_tokens)
        + _tokens(usage.cache_creation_input_tokens)
        + _tokens(usage.cache_read_input_tokens)
    )


def agent_meta(
    tpath: str,
    agent_id: ActorId,
) -> records.AgentMetaFile:
    """The agent's meta.json sidecar (present at SubagentStart for teammates; may
    lag a beat for ordinary subagents, so retry briefly). Carries
    `customAgentType` — the DEFINITION's name, which for a teammate differs from
    its short display type (agentType "container" vs def "task-container") — and
    its configured `model`. An empty AgentMetaFile when it never appears."""
    base = tpath[:-6] if tpath.endswith(".jsonl") else tpath
    p = os.path.join(base, "subagents", f"agent-{agent_id}.meta.json")
    for _ in range(6):
        try:
            with open(p, encoding="utf-8") as fh:
                return records.AgentMetaFile.model_validate_json(fh.read())
        except FileNotFoundError:
            # Missing OR mid-write (a partial file json-fails) — both are the same
            # "not there yet" race, so both retry.
            time.sleep(0.05)
        except ValidationError as error:
            if any(detail["type"] != "json_invalid" for detail in error.errors()):
                raise
            time.sleep(0.05)
        except OSError:
            # A read failure that is not "not there yet" (permissions, a
            # vanished mount) — degrade the same way a missing file does.
            # NOT `except Exception`: a `pydantic.ValidationError` off a real,
            # readable meta.json that does not match AgentMetaFile is schema
            # drift, not a race, and must propagate rather than be read as
            # "no sidecar" — the same distinction records.py's module header
            # draws for every other foreign shape in this package.
            break
    return records.AgentMetaFile()


def short_model(model: str | None) -> str:
    """"claude-opus-4-8" -> "opus-4.8", "claude-haiku-4-5-20251001" -> "haiku-4.5",
    "claude-sonnet-5" -> "sonnet-5", alias "opus" -> "opus". [1m] is dropped (the
    window already shows in the ctx line)."""
    if not model:
        return ""
    s = model.lower().replace("[1m]", "").strip()
    if s.startswith("claude-"):
        s = s[7:]
    parts = s.split("-")
    ver = []
    for p in parts[1:]:
        if p.isdigit() and len(p) <= 2:      # version component; skip 8-digit dates
            ver.append(p)
        else:
            break
    return parts[0] + ("-" + ".".join(ver) if ver else "")


# The rate-limit downgrade ladder (docs/relimit.md, *Model-downgrade ladder*):
# when a model's quota is exhausted on EVERY account, fall to the next rung —
# stepwise, never skipping one (fable→opus→sonnet, never fable→sonnet). Haiku is
# deliberately NOT a rung: the floor is Sonnet. The ONE owner of the
# model-downgrade order (docs/styleguide.md single-owner table).
MODEL_LADDER = ("fable", "opus", "sonnet")


# The picker aliases -> the generation they mean TODAY. The same drift profile
# as MODEL_LADDER above (a new generation updates both), and the reason it is a
# table here rather than knowledge scattered across the catalog, the writers
# and the frontend: display_model() below is the ONE answer to "what is this
# model called", and every surface asks it.
ALIAS_DISPLAY: Mapping[str, str] = {
    "fable": "fable-5", "opus": "opus-5",
    "sonnet": "sonnet-5", "haiku": "haiku-4.5",
}


def alias_display(model_name: str) -> str:
    return ALIAS_DISPLAY.get(model_name, model_name)


def display_model(model_reference: ModelReference) -> str:
    """The one name a person sees for this model, on every surface.

    A resolved id shortens ("claude-sonnet-5" -> "sonnet-5"). A bare picker
    alias ("sonnet") names the generation it launches today, so an actor whose
    harness has not yet reported the resolved id still shows the same name it
    will show after the report."""
    short = short_model(model_reference.name)
    return alias_display(short) or model_reference.name


def family(model: str | None) -> str | None:
    """The model FAMILY word of a model id or alias ("claude-opus-4-8" → "opus",
    "sonnet[1m]" → "sonnet", alias "fable" → "fable"), or None when empty /
    unrecognised. The vocabulary matches the /model picker aliases AND
    relimit.limit_model's scope word, so a limit-hit's `model` scope and a
    resolved version collapse to the same key the ladder is indexed by."""
    if not model:
        return None
    m = model.lower()
    for fam in ("fable", "opus", "sonnet", "haiku"):
        if fam in m:
            return fam
    return None
