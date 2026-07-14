# plugins/claude_code/model.py — model / effort / context-window resolution
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
import json
import os
import time

# How much of a transcript's tail session_model() scans for the last assistant
# turn: the latest turn is near the end, so a bounded read stays cheap even on
# long sessions.
TAIL_SCAN_BYTES = 256 * 1024


def config_dir():
    """The USER-level Claude config dir: $CLAUDE_CONFIG_DIR when set (Claude's own
    override — settings/agents live THERE, not in ~/.claude, when it's in effect),
    else ~/.claude. The one place this default is encoded."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def claude_dirs(start=None, nearest_only=False):
    """Every `.claude` directory to consult for project-level config (agents, settings),
    NEAREST-FIRST, always ending with the user config dir (config_dir()). Used instead
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
    main repo's defs correctly."""
    dirs = []
    env = (os.environ.get("CLAUDE_PROJECT_DIR") or "").strip()
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
    home_claude = config_dir()
    if home_claude not in dirs:
        dirs.append(home_claude)
    return dirs


def int_env(name, default):
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except Exception:
        return default


DISABLE_1M = bool(int_env("CLAUDE_CODE_DISABLE_1M_CONTEXT", 0))
KNOWN_1M = ("fable-5", "sonnet-5", "opus-4-6", "opus-4-7", "opus-4-8", "sonnet-4-6")


def window(model):
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


def context_window(*models):
    """The context window for the first of `models` that resolves (a precedence
    list, best-known-first); 200k when none do or the 1M kill-switch is set."""
    if DISABLE_1M:
        return 200_000
    for m in models:
        w = window(m)
        if w:
            return w
    return 200_000


def fm_field(path, field):
    """Scalar field from a markdown file's YAML frontmatter (the first
    --- ... --- block); None when absent/unreadable."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            if fh.readline().strip() != "---":
                return None
            for line in fh:
                if line.strip() == "---":
                    break
                k, sep, v = line.partition(":")
                if sep and k.strip() == field:
                    return v.strip().strip('"\'') or None
    except Exception:
        return None
    return None


def agent_def_file(atype):
    """The DEFINITION file for an agent type, if any. Identity is the frontmatter
    `name:` (docs); fall back to the filename stem. Project defs shadow user defs.
    Searches agents across ALL ancestor .claude dirs (claude_dirs), not just
    os.getcwd()/.claude: a teammate/subagent frequently runs in a subdirectory or
    a git worktree where <cwd>/.claude is absent OR is a stub without agents/
    (e.g. a task's db/.claude), which would otherwise miss the def and drop
    `effort:`/`model:` to the session/user default. Nearest-first, ~/.claude last."""
    roots = [os.path.join(c, "agents") for c in claude_dirs()]
    stem_hit = None
    for r in roots:
        if not os.path.isdir(r):
            continue
        for dp, _dirs, files in os.walk(r):
            for f in files:
                if not f.endswith(".md"):
                    continue
                p = os.path.join(dp, f)
                if fm_field(p, "name") == atype:
                    return p
                if os.path.splitext(f)[0] == atype and stem_hit is None:
                    stem_hit = p
    return stem_hit


def def_field(def_file, field):
    """A frontmatter field from an agent definition; "inherit"/unset -> None so
    resolution falls through to what the agent actually ran / the session default."""
    v = fm_field(def_file, field) if def_file else None
    return None if (not v or v == "inherit") else v


def settings_field(field):
    """A field from the merged settings (project overriding global). Layered
    across ALL ancestor .claude dirs (claude_dirs, nearest-first) for the same
    subdir/worktree reason as agent_def_file — else a teammate in a subdirectory
    skips the project settings and falls straight through to ~/.claude. First
    non-empty wins; settings.local.json shadows settings.json per dir."""
    paths = []
    for c in claude_dirs():
        paths += [os.path.join(c, "settings.local.json"),
                  os.path.join(c, "settings.json")]
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                v = json.load(fh).get(field)
            if v:
                return v
        except Exception:
            pass
    return None


def settings_env(key, nearest_only=False):
    """The merged value of `key` from the `env` block of the settings files, or "".
    Per .claude dir (claude_dirs, nearest-first — nearest_only limits the walk to the
    nearest project .claude, see claude_dirs), settings.local.json shadows
    settings.json; the FIRST dir that defines the key wins (== project overrides
    global). The user config dir contributes only settings.json (there is no
    user-level settings.local.json layering — and split.py never read one).
    A present-but-falsy JSON value (0, "") still WINS: presence is `is not None`,
    so e.g. CLAUDE_MIRROR_BIAS: 0 yields "0", not the default."""
    cfg = config_dir()
    for c in claude_dirs(nearest_only=nearest_only):
        names = ("settings.json",) if c == cfg else ("settings.local.json",
                                                     "settings.json")
        for name in names:
            try:
                with open(os.path.join(c, name), encoding="utf-8") as fh:
                    v = json.load(fh).get("env", {}).get(key)
                if v is not None:
                    return str(v)
            except Exception:
                pass
    return ""


def session_model(tpath):
    """The model VERSION the parent session runs (e.g. "claude-opus-4-8"), from
    the last assistant turn in its transcript. Gives a precise version for agents
    that INHERIT, before the agent's own first turn reveals it. Tail-scan only
    (TAIL_SCAN_BYTES — see its comment)."""
    try:
        with open(tpath, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 262144))
            chunk = fh.read().decode("utf-8", "replace")
        last = None
        for line in chunk.splitlines():
            if '"assistant"' in line and '"model"' in line:
                try:
                    m = (json.loads(line).get("message") or {}).get("model")
                except Exception:
                    continue
                if m:
                    last = m
        return last
    except Exception:
        return None


def parent_resolved_model(tpath, agent_id):
    """The authoritative resolved model (carrying [1m]) is recorded in the PARENT
    transcript on the agent's Task result — but only at completion. Best-effort:
    scans tpath for the agentId; None if not written yet (callers fall back)."""
    try:
        hit = None
        with open(tpath, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if agent_id not in line or "resolvedModel" not in line:
                    continue
                try:
                    tur = (json.loads(line).get("toolUseResult") or {})
                except Exception:
                    continue
                if tur.get("agentId") == agent_id and tur.get("resolvedModel"):
                    hit = tur["resolvedModel"]
        return hit
    except Exception:
        return None


def parent_tool_result(line, tool_use_id):
    """Whether this raw PARENT-transcript JSONL line carries the tool_result that
    resolves `tool_use_id` (the agent's Task/Agent call, from its meta.json
    `toolUseId`) — and if so, its is_error flag: True == the user REJECTED /
    cancelled the call ("The user doesn't want to proceed with this tool use").
    Returns None when the line isn't that result.

    This is the authoritative "the subagent is done" signal for the cases the
    hooks miss: a rejected or otherwise-abandoned Task fires NO SubagentStop and
    leaves meta.json WITHOUT `stoppedByUser`, so the substream's usual end signals
    never come. The parent transcript still records the Task's tool_result the
    instant the call resolves (completed, rejected, or cancelled) — an EVENT, not
    an idle timeout, so watching for it recovers the gap without the backstop that
    false-positived on long thinks.

    EXCEPTION — the async-launch ack: an ASYNC (background) agent's Task resolves
    IMMEDIATELY with a synthetic "Async agent launched successfully" tool_result
    (is_error absent) that means "launched", NOT "finished" — the agent then runs
    for minutes producing its whole transcript. Treating that ack as resolution
    ended the streamer ~2s in with 0 lines rendered (the agent's work never
    reached the mirror). So the ack is NOT a resolution: return None for it and
    let the streamer tail on to the authoritative SubagentStop sentinel."""
    if not tool_use_id or tool_use_id not in line:
        return None
    try:
        content = (json.loads(line).get("message") or {}).get("content")
    except Exception:
        return None
    if not isinstance(content, list):
        return None
    for b in content:
        if (isinstance(b, dict) and b.get("type") == "tool_result"
                and b.get("tool_use_id") == tool_use_id):
            if not b.get("is_error"):
                txt = b.get("content")
                if isinstance(txt, list):
                    txt = " ".join(x.get("text", "") for x in txt
                                   if isinstance(x, dict))
                if isinstance(txt, str) and "launched successfully" in txt:
                    return None   # async launch ack — not a real resolution
            return bool(b.get("is_error"))
    return None


def agent_meta(tpath, agent_id):
    """The agent's meta.json sidecar (present at SubagentStart for teammates; may
    lag a beat for ordinary subagents, so retry briefly). Carries
    `customAgentType` — the DEFINITION's name, which for a teammate differs from
    its short display type (agentType "container" vs def "task-container") — and
    its configured `model`. {} when it never appears."""
    base = tpath[:-6] if tpath.endswith(".jsonl") else tpath
    p = os.path.join(base, "subagents", f"agent-{agent_id}.meta.json")
    for _ in range(6):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            # Missing OR mid-write (a partial file json-fails) — both are the same
            # "not there yet" race, so both retry.
            time.sleep(0.05)
        except Exception:
            break
    return {}


def effort_config(def_file):
    """Configured effort in the documented precedence (model-config docs: "The
    environment variable takes precedence over all other methods … Frontmatter
    effort … overriding the session level but not the environment variable"):
    env > agent-def frontmatter `effort` > session `effortLevel`. "" when none —
    callers fall to model_default_effort on the model actually running."""
    return ((os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or "").strip()
            or def_field(def_file, "effort") or settings_field("effortLevel") or "")


def model_default_effort(model):
    if not model:
        return ""
    m = model.lower()
    if "opus-4-7" in m:
        return "xhigh"
    if any(t in m for t in ("opus-4-8", "opus-4-6", "sonnet-5", "sonnet-4-6", "fable-5")):
        return "high"
    return ""                                # models without adaptive reasoning


def short_model(model):
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
