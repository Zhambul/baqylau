# plugins/codex/rollout.py — codex ROLLOUT-record parsing.
#
# The parse half of the codex stream's parse/paint split — the same shape as
# plugins/claude_code/transcript.py (docs/sessionapi.md). This module is the
# ONE owner of the codex rollout record shapes (styleguide single-owner
# table): the `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` event grammar —
# turn_context / event_msg / response_item / top-level discrimination, the
# exec-arguments decode, the patch-change line counts, the exec-output exit
# extraction, the synthetic-message vocabulary, and the cumulative
# total_token_usage field mapping (usage_split).
#
# PRESENTERS (a record's consumers — there may be MORE THAN ONE; the grep
# contract in tests/test_l1f_codex_rollout.py pins only that no presenter
# re-walks the raw grammar, not that a single one exists):
#
#   plugins/codex/stream.py Renderer.feed_rollout — the mirror's CAPPED,
#       styled paint (byte-identical to the pre-split renderer; the e2e
#       codex suite is the equivalence pin). It dispatches on a `kind`
#       TABLE and silently ignores every kind it has no handler for, so a
#       record added here for another presenter never changes the mirror.
#   a dashboard `conversation` provider (a later phase) — the codex run's
#       web bubbles, off the `chat`/`think` register below.
#
# There was a third — an uncapped drill-down timeline behind plugins.activity()
# — and it is gone with that fan-out: a codex run's web view is the mirror it
# already paints, scoped (docs/dashboard.md *Agent scope*).
#
# TWO REGISTERS, deliberately not unified (docs/codex.md *Two registers*): a
# codex rollout says most things TWICE — once as an `event_msg` (codex's own
# digested UI stream) and once as a `response_item` (the model-API record the
# conversation is rebuilt from on resume). The MIRROR paints the event_msg
# register (`prompt`/`message`/`reasoning`); a CONVERSATION presenter reads the
# response_item register (`chat`/`think`), which is the complete, in-order,
# resume-restored one and the ONLY source of a post-abort / queued prompt.
# Giving the second register its own kinds is what keeps the mirror from
# painting every message and every think twice.
#
# parse(o) takes one DECODED rollout object and returns a typed record
# (None = nothing renderable — unknown types fall through silently, exactly
# as the pre-split renderer did; the grammar is VERSION-FRAGILE — verified
# drift across codex 0.95 → 0.144 — so an unknown type/payload.type must
# always be None, never an exception):
#   {"kind": "turn_context", "model": str, "effort": str}
#   {"kind": "usage", "usage": dict,      cumulative total_token_usage snapshot
#    "last": dict|None, "window": int|None}   last turn's usage + ctx window
#   {"kind": "patch", "success": bool,
#    "files": [{"path", "change", "added", "removed", "diff"?/"content"?}, …]}
#   {"kind": "compact"} | {"kind": "task_started", "at": …, "ts": …}
#   {"kind": "task_complete", "at": …, "ts": …} | {"kind": "turn_aborted"}
#   {"kind": "prompt" | "reasoning" | "message", "text": str}   (never empty)
#   {"kind": "search", "query": str}
#   {"kind": "exec", "cmd": str, "call_id": str, "ts": str|None}
#   {"kind": "tool", "name": str, "args": str, "call_id": str}   a NON-shell
#    tool call through the same `exec` custom tool (`tools.web__run({…})`)
#   {"kind": "exec_result", "exit": str|int|None, "output": str,
#    "call_id": str, "process_id": str|None, "running": bool, "ts": str|None}
#   {"kind": "stdin", "text": str, "call_id": str, "process_id": str}
#   {"kind": "command_completed", "process_id": str, "output": str,
#    "exit": int|None}
#   {"kind": "chat", "role": str, "text": str, "synthetic": bool}
#   {"kind": "think", "text": str}                      (never empty)
#   {"kind": "patch_call", "patch": str, "call_id": str}
#   {"kind": "ask", "call_id": str, "questions": [{"id", "header", "question",
#                                  "options": [{"label", "description"}]}]}
#   {"kind": "compact_boundary", "message": str, "replaced": int,
#    "window_id": …, "previous_window_id": …}
# parse_line(s) wraps json.loads: {"kind": "bad", "raw": s} for a complete
# line that isn't JSON. parse_line/parse are pure (no I/O, no state), and so is
# owns() (a filename/layout test — the codex twin of transcript.owns). The ONLY
# functions here that touch a file are the three SUBAGENT head-readers at the
# bottom (subagent_fork_epoch / subagent_body_offset / subagent_brief): a
# subagent rollout's replayed-parent PREFIX is a fact about the file's shape, not
# about one record, so it cannot be answered from a parsed line — each is bounded
# and fails open.
import ast
import json
import os
import re
from datetime import datetime

# The exec output's exit-status head line ("Exit code: 2" / "Process exited
# with code 2") — scanned only in the head window: the status line leads the
# output, and a multi-MB output must not be regex-walked whole.
EXIT_RE = re.compile(r"(?:^|\n)(?:Exit code|Process exited with code)[: ]+(\d+)")
EXIT_SCAN_B = 300
CITATION_RE = re.compile(r"cite[^]+\s*")

# codex has TWO exec channels across versions (docs/codex.md, the
# custom_tool_call exec channel), both funnelled to the same
# `exec`/`exec_result` records:
#   - OLDER: a `function_call` named exec_command/shell, arguments a JSON
#     `{cmd:[…]}`; its `function_call_output` is a plain string.
#   - 0.14x+: a `custom_tool_call` named "exec" whose `input` is a JS snippet
#     `tools.exec_command({cmd:"ls",…})`, and a `custom_tool_call_output` whose
#     `output` is a list of parts led by a "Script completed…\nOutput:\n"
#     preamble. This is what a real `run ls` produced (verified 0.144.1) — the
#     reason a codex command showed NO block before: the parser knew only the
#     function_call channel.
# The command is pulled from the JS with a targeted match (never by executing
# it): a list joins on spaces, a string is taken verbatim. The `cmd` key comes
# BOTH unquoted (`{cmd:"ls"}` — a JS object literal) AND double-quoted
# (`{"cmd":"pwd"}` — valid JSON), across codex models/versions, so the optional
# quotes around the key are load-bearing: without them the quoted form matched
# nothing and the command silently vanished (verified live — a gpt-5.6-terra run's
# `pwd` showed no block at all).
_JS_CMD = re.compile(
    r"""["']?cmd["']?\s*:\s*(\[[^\]]*\]|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
# The custom-exec output preamble ends in this marker; the block body wants only
# what follows it (the exit is still read from the whole head window).
_OUTPUT_MARK = "Output:\n"


_JS_TOOL = re.compile(r"tools\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
# The quote characters the argument scan below must not read structure inside.
_JS_QUOTES = "\"'`"
_JS_PLAN_STEP = re.compile(
    r'''["']?step["']?\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
)
_JS_PLAN_STATUS = re.compile(
    r'''["']?status["']?\s*:\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
)


def js_tool_call(js):
    """(name, args) of the `tools.<fn>(…)` call in a `custom_tool_call` name=exec
    JS input — ("", "") when there is none.

    codex ≥ 0.146 runs MANY tools through the SAME `exec` custom tool: a shell
    command is `tools.exec_command({cmd:…})` (handled by _exec_cmd_from_js
    above), but a web/MCP lookup is
    `const r = await tools.web__run({…}); text(JSON.stringify(r))`. The NAME is
    the function (`web__run`) and the ARGS are what it was called with, so a
    presenter can paint the same quiet `· <name>` block every other tool call in
    this repo gets, with the arguments behind the click.

    The args end at the call's MATCHING close paren, found by a depth count that
    skips quoted text. A fixed suffix list ("; text(r)", …) was the previous
    approach and matched NONE of the five real calls in the measured child
    rollout — the wrapper's tail varies per call (`text(JSON.stringify(r))`,
    `text(r.content.map(x=>x.text||"").join("\\n"))`), so the whole `; text(…)`
    tail was landing in the rendered command. An unbalanced (truncated) input
    falls open to the rest of the string rather than raising."""
    m = _JS_TOOL.search(js or "")
    if not m:
        return "", ""
    i, depth, quote, esc = m.end(), 1, "", False
    while i < len(js) and depth:
        ch = js[i]
        if esc:
            esc = False
        elif quote:
            if ch == "\\":
                esc = True
            elif ch == quote:
                quote = ""
        elif ch in _JS_QUOTES:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    args = js[m.end():i - 1] if not depth else js[m.end():]
    return m.group(1), args.strip()


def _plan_tasks(arguments):
    if isinstance(arguments, dict):
        plan = arguments.get("plan")
        return plan if isinstance(plan, list) else None
    try:
        decoded = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, dict) and isinstance(decoded.get("plan"), list):
        return decoded["plan"]
    matches = list(_JS_PLAN_STEP.finditer(arguments or ""))
    if not matches:
        return None
    tasks = []
    for index, step_match in enumerate(matches):
        item_end = matches[index + 1].start() if index + 1 < len(matches) else len(arguments)
        status_match = _JS_PLAN_STATUS.search(arguments, step_match.end(), item_end)
        if status_match is None:
            return None
        try:
            step = ast.literal_eval(step_match.group(1))
            status = ast.literal_eval(status_match.group(1))
        except (SyntaxError, ValueError):
            return None
        tasks.append({"step": step, "status": status})
    return tasks


def _exec_cmd_from_js(js):
    """The SHELL command out of a `custom_tool_call` name=exec JS `input`, or ''
    when the call is not a shell one — `tools.exec_command({cmd:…})` yields its
    cmd, anything else is a different tool and belongs to js_tool_call above (the
    `tool` record), not to a command block."""
    m = _JS_CMD.search(js or "")
    if not m:
        return ""
    raw = m.group(1)
    try:
        v = json.loads(raw)                 # "ls" or ["bash","-lc","…"] (double-quoted)
    except Exception:
        raw = raw.strip()                   # single-quoted / unquoted — light cleanup
        return raw[1:-1] if raw[:1] in "\"'" else raw
    return " ".join(str(x) for x in v) if isinstance(v, list) else str(v)


def _exec_output_body(txt):
    """A custom-exec output stripped of codex's `…Output:\\n` status preamble, so
    the block body is the command's real output (uniform with a Claude command);
    the whole text is still what the exit is scanned from."""
    i = txt.find(_OUTPUT_MARK)
    return txt[i + len(_OUTPUT_MARK):].lstrip("\n") if i >= 0 else txt

# The canonical codex ROLLOUT path layout (docs/codex.md): a `rollout-*.jsonl`
# file under a `.../sessions/YYYY/MM/DD/` tree (`~/.codex/sessions/…` in
# production; the `sessions` ancestor is the stable, non-$HOME-pinned part a
# fixture reproduces). `owns()` recognises one by that FILENAME PREFIX + one
# ancestor dir — the single-owner codex path recogniser (docs/styleguide.md
# single-owner table), the codex twin of plugins/claude_code/transcript.owns.
# Deliberately a PURE filename/layout test: a rollout's records are the grammar
# above, but ownership must be answerable once per session per poll WITHOUT
# opening the file, and the `rollout-` stem is codex-specific (a Claude transcript
# is a bare `<uuid>.jsonl`, an agent sidecar `agent-<id>.jsonl`), so the two
# vocabularies cannot collide. The `sessions/` ancestor keeps a stray
# `rollout-*.jsonl` elsewhere from being claimed.


def owns(path):
    """Is `path` a codex rollout this plugin SPEAKS — the `owns` provider behind
    plugins._first_path (the ownership gate on every path-keyed read fan-out) and
    plugins.owns_by / host_of (the dashboard's host attribution)? True for a
    `rollout-*.jsonl` under a `sessions/` tree, False for everything else — a
    Claude transcript above all (its parsers fail OPEN on a file they never fully
    read, so an ungated fan-out would hand a codex rollout to a Claude parser; the
    same reason claude_code grew `owns`). An empty path (codex sent no
    transcript_path in its SessionStart payload) is not ours — session_caps then
    keeps the session on the empty-path default rather than attributing it here."""
    if not path or not path.endswith(".jsonl"):
        return False
    if not os.path.basename(path).startswith("rollout-"):
        return False
    return "sessions" in os.path.normpath(path).split(os.sep)[:-1]

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


def _patch_delta(ch):
    """(added, removed) line counts for one patch_apply_end change entry."""
    t = ch.get("type")
    if t == "add":
        return len((ch.get("content") or "").splitlines()), 0
    if t == "delete":
        return 0, len((ch.get("content") or "").splitlines())
    add = rem = 0
    for ln in (ch.get("unified_diff") or "").splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            add += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            rem += 1
    return add, rem


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


def _content_text(c):
    """A response_item content list -> its text. The items are
    {"type": "input_text"|"output_text", "text": …}; older versions (and the
    custom-tool outputs) sometimes hand a bare string instead."""
    if isinstance(c, str):
        return c.strip()
    parts = []
    for it in (c or ()):
        if isinstance(it, dict) and isinstance(it.get("text"), str):
            parts.append(it["text"])
        elif isinstance(it, str):
            parts.append(it)
    return "\n".join(parts).strip()


def _args(p):
    """A function_call's `arguments` (a JSON *string*) -> a dict; {} when the
    version at hand wrote something else or the line was truncated."""
    try:
        a = json.loads(p.get("arguments") or "{}")
    except Exception:
        return {}
    return a if isinstance(a, dict) else {}


# --- one parser per record shape (the _EVENT/_RESP/_CALL/_TOP registries) ---

def _turn_context(p):
    # `reasoning_effort` moved under collaboration_mode.settings in 0.14x; the
    # bare top-level `effort` is the older (and still emitted) spelling.
    eff = (((p.get("collaboration_mode") or {}).get("settings") or {})
           .get("reasoning_effort") or p.get("effort") or "").strip()
    return {"kind": "turn_context", "model": (p.get("model") or "").strip(),
            "effort": eff}


def _ev_token_count(p):
    # Cumulative usage snapshot (info is null on rate-limit-only events).
    # `last_token_usage` + `model_context_window` ride along: the CUMULATIVE
    # total never resets across a compaction, so only the last turn's total
    # over the window measures ctx saturation.
    info = p.get("info") if isinstance(p.get("info"), dict) else {}
    u = info.get("total_token_usage")
    if not isinstance(u, dict):
        return None
    last = info.get("last_token_usage")
    win = info.get("model_context_window")
    return {"kind": "usage", "usage": u,
            "last": last if isinstance(last, dict) else None,
            "window": win if isinstance(win, int) else None}


def _ev_thread_goal_updated(p):
    goal = p.get("goal")
    if not isinstance(goal, dict):
        return None
    return {
        "kind": "goal",
        "objective": goal.get("objective"),
        "status": goal.get("status"),
        "reason": goal.get("reason"),
    }


def _ev_thread_goal_cleared(p):
    return {"kind": "goal", "objective": None, "status": "cleared", "reason": None}


def rate_limits(p):
    """A `token_count` payload's `rate_limits` block, normalized to codex's
    windows shape — {"planType", "windows": [{used_pct, window_mins,
    resets_at}]} — or None when the event carries none (the field is NULLABLE)
    or names no window.

    Deliberately NOT part of the `usage` record and NOT in the `_EVENT` table:
    codex emits a token_count with `info: null` on a RATE-LIMIT-ONLY event, which
    _ev_token_count drops entirely because it has no total_token_usage to report.
    The limits ride a different, independently-nullable field of the same event,
    so they are read on their own (plugins/codex/read.usage — the bounded tail
    probe for the last event that HAS them).

    Measured shape (rollout 019fb363, 2026-07-30): snake_case `used_percent` /
    `window_minutes` / `resets_at` (epoch seconds) / `plan_type`, `secondary`
    null on a plan with one window. That is the same information the app server
    returns in camelCase (plugins/codex/usage._normalize), and both are mapped
    here to ONE codex-internal shape so a single strip mapper serves both."""
    rl = p.get("rate_limits")
    if not isinstance(rl, dict):
        return None
    wins = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        wins.append({"used_pct": w.get("used_percent"),
                     "window_mins": w.get("window_minutes"),
                     "resets_at": w.get("resets_at")})
    if not wins:
        return None
    return {"planType": rl.get("plan_type") or "", "windows": wins}


def _file_change(item):
    """Normalize Codex's authoritative completed FileChange item."""
    files = []
    for path, ch in (item.get("changes") or {}).items():
        if not isinstance(ch, dict):
            continue
        add, rem = _patch_delta(ch)
        change = ch.get("type")
        move_path = ch.get("move_path")
        row = {"path": path, "change": change,
               "added": add, "removed": rem}
        if move_path:
            row.update(path=move_path, previous_path=path, change="move")
        if change in ("update", "move"):
            row["diff"] = ch.get("unified_diff") or ""
        elif change in ("add", "delete"):
            row["content"] = ch.get("content") or ""
        files.append(row)
    return {
        "kind": "patch",
        "success": item.get("status") == "completed",
        "files": files,
    }


def _ev_context_compacted(p):
    return {"kind": "compact"}


def _ev_task_started(p):
    # `turn_id` is codex's identity for the TURN this task is — the fact the
    # actor-assignment model is built on (core/childtask.py). A child rollout opens by
    # replaying the parent thread, so the task_started records BEFORE the child's
    # own bootstrap carry the PARENT's turn id, which is how a child learns the
    # turn it was spawned in (plugins/codex/stream.py). Absent on older rollouts:
    # "" then, and every consumer degrades to its pre-turn behaviour.
    return {"kind": "task_started", "at": p.get("started_at"),
            "turn": p.get("turn_id") or ""}


def _ev_task_complete(p):
    # …and the same turn id closing it, plus `last_agent_message`: codex's own
    # statement of what the turn ANSWERED. Kept because it is the FALLBACK result
    # text (plugins/codex/stream._ro_task_complete) for a run whose messages
    # carried no `phase` — a pre-phase rollout, or a tailer that joined mid-run
    # and never saw the message record. Never the primary: the `final_answer`
    # phase says which message IS the result, where this only repeats text.
    return {"kind": "task_complete", "at": p.get("completed_at"),
            "turn": p.get("turn_id") or "",
            "last": (p.get("last_agent_message") or "").strip()}


def _ev_thread_settings_applied(p):
    """codex's PICKER state: a `thread_settings_applied` fires on EVERY /model
    change (model or reasoning level) — so it is FRESHER than `turn_context`,
    which is written only per-TURN. Its `thread_settings.model` +
    `reasoning_effort` are the current model/effort even before the next turn, so
    the ctx/effort reads take the NEWEST of this and turn_context (else the header
    lagged behind picker changes — a `terra high` run reading a stale `sol high`
    from the last turn_context, docs/codex.md *token_count keeps three things*)."""
    ts = p.get("thread_settings") or {}
    return {"kind": "settings", "model": ts.get("model") or "",
            "effort": (ts.get("reasoning_effort") or "").strip()}


def _ev_item_completed(p):
    """codex's PLAN-mode plan: an `item_completed` whose `item.type == "Plan"`
    carries the full plan as markdown (`item.text`) with a stable id. This is
    the structured plan the dashboard renders as a plan card (docs/codex.md
    *Plan mode*) — the codex analog of Claude's ExitPlanMode plan text, and the
    signal the pending-plan read keys on. Every OTHER item_completed kind (codex
    also completes messages/reasoning as items) is already covered by its own
    event/response record, so only Plan produces a record here."""
    item = p.get("item") or {}
    if item.get("type") == "FileChange":
        return _file_change(item)
    if item.get("type") == "CommandExecution":
        process_id = item.get("process_id")
        if process_id is None:
            return None
        output = item.get("aggregated_output")
        if not isinstance(output, str):
            output = item.get("formatted_output")
        if not isinstance(output, str):
            stdout = item.get("stdout") if isinstance(item.get("stdout"), str) else ""
            stderr = item.get("stderr") if isinstance(item.get("stderr"), str) else ""
            output = stdout + stderr
        return {
            "kind": "command_completed",
            "process_id": str(process_id),
            "output": output,
            "exit": item.get("exit_code"),
            "item_id": item.get("id") or "",
        }
    if item.get("type") == "SubAgentActivity":
        actor_id = item.get("agent_thread_id") or ""
        agent_path = str(item.get("agent_path") or "")
        if not actor_id:
            return None
        return {
            "kind": "actor_activity",
            "activity": item.get("kind") or "",
            "actor_id": actor_id,
            "actor_path": agent_path,
            "call_id": item.get("id") or "",
            "turn": p.get("turn_id") or "",
            "at": (p.get("started_at_ms") or 0) / 1000 or None,
        }
    if item.get("type") != "Plan":
        return None
    text = (item.get("text") or "").strip()
    return {"kind": "plan", "text": text, "id": item.get("id") or ""} if text \
        else None


def _ev_turn_aborted(p):
    return {"kind": "turn_aborted", "turn": p.get("turn_id") or ""}


def _ev_user_message(p):
    # Unwrap an INPUT wrapper here too so a `<task>` that also lands in the
    # event_msg register de-doubles with the response_item one to a single bubble.
    msg = strip_input_wrapper((p.get("message") or "").strip())
    return {"kind": "prompt", "text": msg} if msg else None


def _ev_agent_reasoning(p):
    txt = (p.get("text") or "").strip()
    return {"kind": "reasoning", "text": txt} if txt else None


# The PHASE codex stamps on an assistant message. `final_answer` is the one that
# matters: it is codex SAYING this message is the turn's answer, which is what
# tells a child's stream that this message is its RESULT rather than one more
# intermediate note (`commentary` is the other measured value). Absent on older
# rollouts — "" then, and the result falls back to the pre-phase inference.
PHASE_FINAL = "final_answer"


def _ev_agent_message(p):
    msg = (p.get("message") or "").strip()
    return {"kind": "message", "text": msg,
            "phase": (p.get("phase") or "").strip()} if msg else None


def _ev_web_search_end(p):
    """codex's web SEARCH in the event_msg register — and on cli 0.146 the ONLY
    place a search appears at all: the measured child rollout (019fb363-4028…)
    carries five `web_search_end` events and ZERO `web_search_call`
    response_items, so without this handler a codex web search rendered nothing.

    Only a search that NAMES a query yields a record. The same event ALSO fires
    for the web tool's non-search actions (`action.type == "other"` — an
    open/fetch of a previously-found result), where `query` is "" and there is
    nothing to show; four of the five measured events are exactly that. Same
    guard as the response_item twin below, which is why both can return the one
    `search` kind.

    If some codex build emits BOTH registers for one search, two records would
    reach the renderer for it; the RENDERER collapses an immediately-repeated
    query (plugins/codex/stream.py `_ro_search`) rather than the parser dropping
    one — a parser stays a faithful reader of what the file actually says."""
    q = ((p.get("query") or "").strip()
         or ((p.get("action") or {}).get("query") or "").strip())
    return {"kind": "search", "query": q} if q else None


def _rsp_web_search_call(p):
    q = (p.get("action") or {}).get("query") or ""
    return {"kind": "search", "query": q} if q else None


def _rsp_function_call_output(p):
    # The OLDER exec channel's output. Same normalisation as the custom-tool one:
    # the exit is scanned from the FULL head (the `Chunk ID…\nWall time…\nProcess
    # exited with code N\n…Output:\n` preamble codex 0.14x prints leads it), THEN
    # the body is stripped of that preamble so a standalone block shows the real
    # output, not codex's status noise (verified live: a `run ls` used THIS
    # channel with exactly that preamble).
    out = p.get("output") or ""
    if not isinstance(out, str):
        out = _content_text(out)
    if not out:
        return None
    m = EXIT_RE.search(out[:EXIT_SCAN_B])
    return {"kind": "exec_result", "exit": m.group(1) if m else None,
            "output": _exec_output_body(out), "call_id": p.get("call_id") or ""}


def _rsp_message(p):
    # The response_item register (module header): the conversation as the
    # model API records it — assistant/user/developer, and the ONLY place a
    # post-abort or queued prompt appears. Deliberately NOT kind "message"/
    # "prompt": those are the event_msg register the mirror paints, and one
    # turn shows up in both.
    txt = _content_text(p.get("content"))
    if not txt:
        return None
    role = (p.get("role") or "").strip()
    # A PLAN before anything else: it is an assistant turn wearing a wrapper tag,
    # so the structural synthetic rule below would drop it as machinery (see
    # PLAN_WRAPPER). Its own kind, not a `chat`, because it is a different KIND of
    # turn — the web renders it as a plan bubble, exactly as a Claude
    # ExitPlanMode plan is (docs/codex.md *Plan mode in the conversation*).
    if role == "assistant":
        plan = plan_body(txt)
        if plan:
            return {"kind": "plan", "role": role, "text": plan}
    # role-aware synthetic on the RAW text (the `<tag>` is the signal), THEN unwrap
    # an INPUT wrapper so a kept `<task>` prompt reads as its inner text.
    synth = is_synthetic(txt, role)
    # …carrying the assistant PHASE too (see PHASE_FINAL): this register is the
    # twin of the event_msg one, and the web's conversation read takes whichever
    # arrives first — so the fact that a reply is the turn's FINAL ANSWER has to
    # survive both spellings or it survives neither.
    metadata = p.get("internal_chat_message_metadata_passthrough") or {}
    return {"kind": "chat", "role": role,
            "text": strip_input_wrapper(txt), "synthetic": synth,
            "phase": (p.get("phase") or "").strip(),
            "turn": metadata.get("turn_id") or ""}


def _rsp_reasoning(p):
    # summary is a list of {"type": "summary_text", "text": …}; it is empty
    # whenever the think was stored as `encrypted_content` instead.
    txt = _content_text(p.get("summary"))
    return {"kind": "think", "text": txt} if txt else None


def _rsp_custom_tool_call(p):
    # codex ≥ 0.13x runs BOTH apply_patch and exec through custom tools:
    #   name="exec"       -> an exec record (cmd out of the JS input) — the
    #                        0.14x+ command channel (see _JS_CMD above).
    #   name="apply_patch"-> a lightweight "patch call started" marker; the
    #                        resolved file ops come from patch_apply_end
    #                        (_ev_patch_apply_end), counting both would double.
    # Any other custom tool degrades to None (forward-compatible).
    name = p.get("name")
    if name == "exec":
        js = p.get("input") or ""
        cmd = _exec_cmd_from_js(js)
        if cmd:
            return {"kind": "exec", "cmd": cmd, "call_id": p.get("call_id") or ""}
        # …not a shell command: any OTHER `tools.<fn>(…)` through the same exec
        # tool is a TOOL CALL and gets its own record — structured (name + args)
        # rather than laundered into the exec/command shape, which painted a
        # subagent's web lookups as a `▶ cmd` block of raw JS.
        fn, args = js_tool_call(js)
        if fn:
            if fn == "apply_patch":
                return None
            if fn == "write_stdin":
                return _stdin_record(p.get("call_id") or "", args)
            if fn == "update_plan":
                plan = _plan_tasks(args)
                if not isinstance(plan, list):
                    return {"kind": "unmapped_tool", "name": "update_plan"}
                return {
                    "kind": "task_list",
                    "tasks": plan,
                    "call_id": p.get("call_id") or "",
                }
            if fn in ("create_goal", "get_goal", "update_goal"):
                return {"kind": "goal_tool", "call_id": p.get("call_id") or ""}
            return {"kind": "tool", "name": fn, "args": args,
                    "call_id": p.get("call_id") or ""}
        return None
    if name == "apply_patch":
        inp = p.get("input")
        return {"kind": "patch_call",
                "patch": inp if isinstance(inp, str) else _content_text(inp),
                "call_id": p.get("call_id") or ""}
    return None


def _rsp_custom_tool_call_output(p):
    # The output carries no tool name, so this is the exec/patch OUTPUT for
    # whatever `custom_tool_call` opened this call_id — an `exec_result` in both
    # cases, paired by call_id in the renderer: an exec's closes its command
    # block, an apply_patch's is an orphan (its file ops come from
    # patch_apply_end) that shows only a FAILED exit, never a stray block. Same
    # record shape the function_call_output (older channel) yields, so one
    # renderer path handles both.
    out = p.get("output")
    txt = out if isinstance(out, str) else _content_text(out)
    body = CITATION_RE.sub("", _exec_output_body(txt))
    try:
        combined_result = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        combined_result = None
    # An apply_patch-only wrapper returns `{}`; the authoritative FileChange
    # item carries the immutable patch. A combined patch + command wrapper
    # returns both results, so retain only the command result that its matching
    # custom_tool_call opened.
    if combined_result == {}:
        return None
    if isinstance(combined_result, dict) and "patch" in combined_result:
        command_result = combined_result.get("test")
        if not isinstance(command_result, dict):
            return None
        return {
            "kind": "exec_result",
            "exit": command_result.get("exit_code"),
            "output": command_result.get("output") or "",
            "process_id": command_result.get("session_id"),
            "running": command_result.get("session_id") is not None
                       and command_result.get("exit_code") is None,
            "call_id": p.get("call_id") or "",
        }
    if isinstance(combined_result, dict) and any(
        field in combined_result for field in ("output", "session_id", "exit_code")
    ):
        process_id = combined_result.get("session_id")
        exit_code = combined_result.get("exit_code")
        return {
            "kind": "exec_result",
            "exit": exit_code,
            "output": combined_result.get("output") or "",
            "process_id": str(process_id) if process_id is not None else None,
            "running": process_id is not None and exit_code is None,
            "call_id": p.get("call_id") or "",
        }
    m = EXIT_RE.search(txt[:EXIT_SCAN_B])
    return {"kind": "exec_result", "exit": m.group(1) if m else None,
            "output": body, "call_id": p.get("call_id") or ""}


def _call_exec(p, args):
    cmd = args.get("cmd") or args.get("command") or ""
    if isinstance(cmd, list):
        cmd = " ".join(str(x) for x in cmd)
    if not cmd:
        return None
    return {"kind": "exec", "cmd": cmd, "call_id": p.get("call_id") or ""}


def _call_stdin(p, args):
    # The backgrounded-exec continuation poll: codex writes into a running
    # exec session and reads more of its output. Its function_call_output is
    # an ordinary `exec_result` — this record exists so that output is not
    # orphaned (a presenter pairs the two by call_id).
    return _stdin_record(p.get("call_id") or "", args)


def _stdin_record(call_id, arguments):
    """Normalize only the measured write_stdin argument shape.

    Current custom-tool rollouts contain either JSON or a JavaScript object
    literal with unquoted keys. This parser does not interpret JavaScript; it
    extracts the two fields that define the continuation.
    """
    if isinstance(arguments, dict):
        fields = arguments
    else:
        try:
            fields = json.loads(arguments)
        except (TypeError, json.JSONDecodeError):
            process_match = re.search(r'(?:^|[,{])\s*["\']?session_id["\']?\s*:\s*(\d+)', arguments or "")
            chars_match = re.search(
                r'(?:^|[,{])\s*["\']?chars["\']?\s*:\s*("(?:[^"\\]|\\.)*")',
                arguments or "",
            )
            if process_match is None or chars_match is None:
                return {"kind": "stdin", "text": "", "call_id": call_id, "process_id": ""}
            fields = {
                "session_id": process_match.group(1),
                "chars": json.loads(chars_match.group(1)),
            }
    process_id = fields.get("session_id") if isinstance(fields, dict) else None
    chars = fields.get("chars") if isinstance(fields, dict) else None
    return {
        "kind": "stdin",
        "text": chars if isinstance(chars, str) else "",
        "call_id": call_id,
        "process_id": str(process_id) if process_id is not None else "",
    }


def _call_ask(p, args):
    # codex's EXPERIMENTAL question tool (plan mode in practice) — the schema
    # is Claude's AskUserQuestion in codex spelling.
    out = []
    for q in (args.get("questions") or ()):
        if not isinstance(q, dict):
            continue
        opts = [{"label": (o.get("label") or ""),
                 "description": (o.get("description") or "")}
                for o in (q.get("options") or ()) if isinstance(o, dict)]
        out.append({"id": q.get("id") or "", "header": q.get("header") or "",
                    "question": q.get("question") or "", "options": opts})
    # call_id rides along so a presenter can pair the ask with its
    # function_call_output ANSWER without re-reading the raw payload (the web
    # question card's pending_dialog read — plugins/codex/read.py).
    return {"kind": "ask", "call_id": p.get("call_id") or "",
            "questions": out} if out else None


# function_call `name` → its argument grammar. `shell` is the pre-0.1x
# spelling of `exec_command` (same {command: [...]} shape) and still turns up
# in older rollouts; an unlisted name is None, so a new codex tool degrades to
# "not rendered" rather than to an exception.
_CALL = {"exec_command": _call_exec, "shell": _call_exec,
         "write_stdin": _call_stdin, "request_user_input": _call_ask}


def _rsp_function_call(p):
    h = _CALL.get(p.get("name"))
    if h:
        return h(p, _args(p))
    if p.get("name") in {
        "spawn_agent",
        "wait_agent",
        "send_message",
        "followup_task",
        "interrupt_agent",
        "list_agents",
    }:
        return {
            "kind": "collaboration_call",
            "name": p.get("name"),
            "args": _args(p),
            "call_id": p.get("call_id") or "",
        }
    return {"kind": "unmapped_tool", "name": p.get("name") or ""}


def _top_compacted(p):
    # The TOP-LEVEL compaction record (distinct from the event_msg
    # `context_compacted` notice the mirror paints as ⟳): it is the boundary
    # itself, and `message` is usually "" because the summary is encrypted.
    # `replacement_history` — the entire rewritten conversation — is
    # deliberately NOT carried, only its length: a record shape must not be a
    # megabyte.
    hist = p.get("replacement_history")
    return {"kind": "compact_boundary", "message": p.get("message") or "",
            "replaced": len(hist) if isinstance(hist, list) else 0,
            "window_id": p.get("window_id"),
            "previous_window_id": p.get("previous_window_id")}


def _top_world_state(p):
    # A large periodic state snapshot (open files, shell sessions, todos).
    # Explicitly ignored: nothing in it is renderable and it would otherwise
    # look like an unhandled type to the next reader of this table.
    return None


_EVENT = {"token_count": _ev_token_count,
          "thread_goal_updated": _ev_thread_goal_updated,
          "thread_goal_cleared": _ev_thread_goal_cleared,
          "context_compacted": _ev_context_compacted,
          "task_started": _ev_task_started, "task_complete": _ev_task_complete,
          "thread_settings_applied": _ev_thread_settings_applied,
          "item_completed": _ev_item_completed,
          "turn_aborted": _ev_turn_aborted, "user_message": _ev_user_message,
          "agent_reasoning": _ev_agent_reasoning,
          "agent_message": _ev_agent_message,
          "web_search_end": _ev_web_search_end}
# Two event_msg types stay DELIBERATELY unparsed (they fall through to None like
# any unknown type, and so have no KINDS entry): `sub_agent_activity` (a
# `{kind:"interacted"}` ping about a child thread — the child has its own rollout
# and its own stream, so this would only duplicate) and
# `inter_agent_communication_metadata` (`{trigger_turn:true}` — pure plumbing).
# Both were measured in the real child rollout; noted here so the next reader
# knows they were considered rather than missed.
_RESP = {"web_search_call": _rsp_web_search_call,
         "function_call_output": _rsp_function_call_output,
         "function_call": _rsp_function_call,
         "message": _rsp_message, "reasoning": _rsp_reasoning,
         "custom_tool_call": _rsp_custom_tool_call,
         "custom_tool_call_output": _rsp_custom_tool_call_output}
_TOP = {"turn_context": _turn_context, "compacted": _top_compacted,
        "world_state": _top_world_state}

# Record kinds that carry the ENVELOPE's `timestamp` as a separate `ts` string.
# Three families: the task lifecycle records whose OWN timestamp field is absent
# in many codex versions (task_started/task_complete), the exec pair — a codex
# exec record carries no duration of its own, so the standalone command block
# times itself from the exec's `ts` to its exec_result's `ts` (the elapsed on
# `■ finished · Ns`, plugins/codex/stream.py) — and an assistant `message`, whose
# clock a child's RESULT card needs: a `final_answer` message ENDS the task
# (plugins/codex/stream.py paints the ⇠ card there, ~100ms before task_complete),
# so without it the card's `· 23.0s` would be measured to `time.time()` and a
# rollout being replayed from disk would report the age of the file. `ts` is
# always the ISO envelope string, never folded into the numeric `at` a task
# duration subtracts.
_ENVELOPE_TS = ("task_started", "task_complete", "exec", "exec_result",
                "message")


def _stamp(rec, o):
    if rec is not None and rec["kind"] in _ENVELOPE_TS:
        rec["ts"] = o.get("timestamp")
    return rec


# The COMPLETE set of record kinds parse()/parse_line() can return — the ONE
# owner of the codex rollout KIND vocabulary (docs/styleguide.md single-owner
# table; docs/codex.md *Kind drift contract*). Hand-maintained rather than
# derived: the kind a handler returns is NOT its registry key
# (_ev_user_message → "prompt", _ev_patch_apply_end → "patch",
# _ev_context_compacted → "compact"), so it can't be read off the
# _EVENT/_RESP/_CALL/_TOP tables. But a new or renamed kind can never drift past
# a renderer SILENTLY: tests/test_l1f_codex_rollout.py pins every kind here to
# be EITHER rendered (stream.Renderer._RO) OR explicitly ignored
# (stream.IGNORE_KINDS), and every rendered/ignored kind to be a real member
# here — so adding a parser kind fails the suite until someone decides
# render-vs-ignore. `bad` is parse_line's non-JSON record.
KINDS = frozenset({
    "turn_context", "usage", "patch", "compact", "task_started",
    "task_complete", "turn_aborted", "prompt", "reasoning", "message",
    "search", "exec", "exec_result", "stdin", "command_completed", "chat", "think", "patch_call",
    "ask", "plan", "settings", "compact_boundary", "tool",
    "actor_activity", "collaboration_call", "task_list", "goal", "goal_tool",
    "unmapped_tool", "bad",
})


def parse(o):
    """One decoded rollout object -> a typed record (module header) or None."""
    t = o.get("type")
    p = o.get("payload") or {}
    if t == "event_msg":
        h = _EVENT.get(p.get("type"))
        return _stamp(h(p), o) if h else None
    if t == "response_item":
        h = _RESP.get(p.get("type"))
        # response_item too: exec / exec_result carry the envelope timestamp so a
        # standalone exec block can time itself (_stamp is a no-op for the rest).
        return _stamp(h(p), o) if h else None
    h = _TOP.get(t)
    # A top-level record's fields sit under `payload` in the enveloped
    # spelling and at the top level in the older bare-item one — hand the
    # handler whichever mapping actually holds them.
    return h(o.get("payload") or o) if h else None


def parse_line(s):
    """One rollout JSONL line -> a typed record; {"kind": "bad", "raw": s}
    when the line isn't JSON at all (the stream keeps its own json.loads so
    its malformed-line audit contract stays where it was)."""
    try:
        o = json.loads(s)
    except Exception:
        return {"kind": "bad", "raw": s}
    return parse(o)


# --- subagent rollout: skip the replayed-parent PREFIX ---------------------------
# A codex SUBAGENT run (cli 0.146+, `collaboration.spawn_agent`) writes its OWN
# rollout that OPENS with a burst replaying the PARENT thread's history as of the
# fork — two `session_meta` records (the child's `thread_source=="subagent"`, then
# the parent's), the parent's replayed turn(s), then the child's own work. Left
# in, that prefix DOUBLES the parent's prose/exec into the subagent's scoped
# mirror + bubbles (docs/codex.md *Sidecar → subagent parity*). The reliable
# boundary (verified on cli 0.146): a parent's replayed `task_started` carries a
# `started_at` from BEFORE the fork, while the CHILD's OWN bootstrap `task_started`
# carries `started_at >= the fork` (the child `session_meta`'s own timestamp).
# The bootstrap task_started ITSELF is the child's first own record — it is the
# child's turn/assignment start, and classifying it as replay eats the canonical
# `actor.assignment_started` (measured, session 01a00a31-3a90: the started card
# never painted). Everything before it is the replayed prefix.

def subagent_fork_epoch(path):
    """int(the child `session_meta` timestamp) for a SUBAGENT rollout, else None
    (a normal rollout / unreadable head). A subagent rollout's first session_meta
    has `thread_source == "subagent"` (or a `source.subagent.thread_spawn`)."""
    try:
        with open(path, encoding="utf-8") as fh:
            o = json.loads(fh.readline())
        if o.get("type") != "session_meta":
            return None
        p = o.get("payload") or {}
        source = p.get("source")
        spawn = (
            ((source.get("subagent") or {}).get("thread_spawn") or {})
            if isinstance(source, dict)
            else {}
        )
        if p.get("thread_source") != "subagent" and not spawn:
            return None
        ts = p.get("timestamp") or o.get("timestamp") or ""
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def is_child_bootstrap(rec, fork_epoch):
    """True for the child's OWN bootstrap `task_started` (`at >= fork_epoch`) —
    the FIRST child-own record; the replayed-parent prefix is everything before
    it. `fork_epoch` None => never."""
    return (fork_epoch is not None and bool(rec)
            and rec.get("kind") == "task_started"
            and (rec.get("at") or 0) >= fork_epoch)


# How far into a subagent rollout's HEAD subagent_brief will read before giving
# up. The replayed-parent prefix is short (13 records in the measured run), but a
# fork of a long conversation replays more, and this runs in a tailer's startup
# path — so both a line and a byte ceiling, generous enough that only a
# pathological file hits one, and hitting one just means no brief.
BRIEF_MAX_LINES = 500
BRIEF_MAX_B = 4 << 20


def subagent_body_offset(path):
    """Byte offset of the first CHILD-OWN record in a subagent rollout — the
    child's bootstrap task_started itself (its turn/assignment start), skipping
    the replayed-parent prefix before it. 0 for a normal rollout OR when the
    boundary isn't found (fail-open: show everything, never an empty scope)."""
    fork_epoch = subagent_fork_epoch(path)
    if fork_epoch is None:
        return 0
    try:
        off = 0
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    rec = parse(json.loads(raw.decode("utf-8", "replace")))
                except Exception:
                    off += len(raw)
                    continue
                if is_child_bootstrap(rec, fork_epoch):
                    return off          # the child's turns begin HERE
                off += len(raw)
    except Exception:
        pass
    return 0
