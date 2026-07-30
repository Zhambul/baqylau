# tests/test_l1i_host_contract.py — the HOST-ABSTRACTION contract.
#
# The end state this file ratchets toward: **dashboard/ reaches hosts ONLY
# through abstractions** — the `plugins` registry root's fan-outs for read
# facts, `plugins.host.HostControl` for control gestures — and every host's
# knowledge lives in its own `plugins/<tool>/` package. Three checks, each a
# DECLARED table that a change must edit:
#
#   1. the LITERAL allowlist — which host names may still appear as string
#      literals in dashboard/**/*.py, and why. Every later phase DELETES rows;
#      adding one needs an argument. (Sibling of test_l1_contracts.py's
#      DASHBOARD_PLUGIN_REACHES, which covers IMPORTS; this covers the other way
#      a tier learns a host's name.)
#   2. the PROVIDER-COVERAGE matrix — for every name in plugins.PROVIDERS, which
#      plugin implements it and which DECLINES it. A decline is a decision (see
#      the "NB codex exposes no …" comments in the plugin __init__ files) and
#      this is where it is written down; an absence nobody declared is a bug
#      that shows up as a silently-empty feature.
#   3. the REGISTER table is the single source — read/mirror.agent_scope's `src`
#      prefixes and opshtml/actclass's register→act map must be DERIVED from
#      core/agentblocks.REGISTERS, not re-spelled beside it.
#
# Why literals and not just imports: the layering rule is about who KNOWS a
# host. `plugins.owns_by(p) == "codex"` imports nothing and breaks silently the
# day that plugin is renamed or a second self-streaming host appears — which is
# exactly the class of bug P1 removed (read/mirror.is_codex_lead, four spellings
# of the default-host name).
#
# P7 — THE FINAL RATCHET AUDIT. Every row below was re-tested for shrinkage at
# the end of the host-abstraction work, and the ones that remain are at their
# floor. What that means per row, so a future reader does not re-derive it:
#
#   LITERAL_ALLOW (2 files, 3 literals)
#     ext/memory: `PRODUCER` is one of the extension descriptor's REQUIRED
#       constants (pinned by test_l1_contracts' conformance check beside NAME /
#       LABEL / TAB_AFTER / BADGE_SCOPED) — documentation that is never
#       imported. Removing it means deleting a contract field, not a coupling.
#     opshtml/actclass: the two parked-history sniffs. FROZEN by construction —
#       they match ops written BEFORE the `chrome` flag existed (25 sessions in
#       the parked corpus) and no restart can re-stamp a parked op. A live run
#       stamps the flag, so a new host neither needs nor may add a third.
#   JS_LITERAL_ALLOW (3 files, 4 rows) — all VOCABULARY, none a host branch:
#     the slot-KIND ribbon (core/slots.py's five kinds), the ACT token, and two
#     protocol constants named after the product (the X-Claude-Dash header, the
#     push tag pinned to notify/channels.push_tag). Each row says which.
#   DASHBOARD_PLUGIN_REACHES (test_l1_contracts.py, 4 rows) — also re-tested:
#     opshtml/tools.py cannot shrink without INVERTING the layering (a
#       `plugins.tool_html` fan-out would have the plugin emit the page's own
#       HTML/CSS classes and need `ansi_html`, which lives in the dashboard a
#       plugin may not import);
#     opshtml/ops.py keeps its `strip_reminders` reach on the argument P6 wrote
#       out in full at the call site (no PATH at op-render time to key a
#       path-fan-out with, and the alternative is a second regex);
#     opshtml/actclass.py's two glyph reaches are producer vocabulary read back
#       off a painted op, inside a per-op loop;
#     ext/memory/read.py is an extension reaching its OWN plugin, the sanctioned
#       shape.
#   The ratchet's remaining TEETH are the both-directions checks: a row that
#   outlives its code fails, and the JS rows carry EXACT counts so the list
#   cannot be widened by writing another literal in a file already on it.
import ast
import os
import re

from conftest import REPO

# ---------------------------------------------------------------- 1. literals

# The host names the dashboard tier must not learn by heart.
HOST_WORDS = ("claude_code", "codex")

# file -> {literal: why it is still there}. THE RATCHET: rows come OUT as later
# phases route each fact through an abstraction; a new row must be argued.
# Seeded with the true remaining set after P1 (six literals, four files).
LITERAL_ALLOW = {
    # The memory extension DECLARES its producer module as documentation (it is
    # never imported through this string). The extension is a claude_code
    # feature end to end — the sanctioned shape per docs/styleguide.md
    # *Layering*, and the same reach test_l1_contracts allowlists for its
    # read.py.
    "dashboard/ext/memory/__init__.py": {
        "plugins.claude_code.memory":
            "the ext's declared producer, documentation only — never imported",
    },
    # (P3 deleted this file's two rows: the host-NAMED /api/codex-usage endpoint
    # and its get_codex_usage handler are gone, folded into the one host-keyed
    # usage strip that /api/accounts now serves over plugins.usage_strip.)
    # The presenter's two remaining parked-history sniffs: `codex_chrome` matches
    # the run banner / footer of ops written BEFORE the `chrome` flag existed
    # (25 such sessions in the parked corpus), and no restart can re-stamp them.
    # FROZEN at these two literals — a live run stamps the flag, so a new host
    # neither needs nor may add a third.
    #
    # (P6 deleted this file's `codex` row: the ACT_CODEX token is no longer
    # spelled here at all. The act vocabulary moved to its owner core/ops.py —
    # the producers stamp from it — and actclass imports the tokens, so the
    # coincidence between the register name and the act token is now a
    # derivation rather than a matching pair of strings.)
    "dashboard/opshtml/actclass.py": {
        "codex ▶ ": "parked-history sniff for the pre-`chrome` run banner (frozen)",
        "■ codex ": "parked-history sniff for the pre-`chrome` run footer (frozen)",
    },
}


def _docstring_nodes(tree):
    """The ast.Constant nodes that are DOCSTRINGS — excluded from the scan.
    Prose may name a host freely (it is how the design is explained); what must
    not appear is a host name the CODE acts on."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            body = getattr(n, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _host_literals():
    """{rel path: {literal, …}} — every non-docstring string literal under
    dashboard/ that names a host."""
    found = {}
    root = os.path.join(REPO, "dashboard")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, REPO)
            with open(p, encoding="utf-8") as fh:
                src = fh.read()
            tree = ast.parse(src, filename=p)
            docs = _docstring_nodes(tree)
            for n in ast.walk(tree):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and id(n) not in docs
                        and any(w in n.value.lower() for w in HOST_WORDS)):
                    found.setdefault(rel, set()).add(n.value)
    return found


def test_no_host_name_literal_in_the_dashboard_off_the_allowlist():
    """No dashboard module spells a HOST NAME in code outside LITERAL_ALLOW.

    This is what makes "the dashboard knows no host" checkable. Before P1 the
    default host's name alone had four independent spellings across three
    dashboard modules plus the registry, and `read/mirror` decided how to render
    a whole session by comparing an owner name to "codex"."""
    found = _host_literals()
    offenders = []
    for rel, lits in sorted(found.items()):
        allowed = set(LITERAL_ALLOW.get(rel, {}))
        for lit in sorted(lits - allowed):
            offenders.append("%s: %r" % (rel, lit))
    assert not offenders, (
        "host-name literal(s) in the dashboard tier — route the fact through a "
        "registry fan-out / HostControl, or add it to LITERAL_ALLOW with a "
        "reason:\n" + "\n".join(offenders))


def test_the_host_literal_allowlist_has_no_stale_rows():
    """Every allowlisted literal is still there. A row that outlives the code it
    excused makes the list look bigger than the debt — the same both-directions
    enforcement DASHBOARD_PLUGIN_REACHES has."""
    found = _host_literals()
    stale = []
    for rel, rows in sorted(LITERAL_ALLOW.items()):
        have = found.get(rel, set())
        if not have:
            stale.append("%s: file has no host literal at all" % rel)
            continue
        for lit in sorted(set(rows) - have):
            stale.append("%s: %r no longer present" % (rel, lit))
    assert not stale, ("stale LITERAL_ALLOW row(s) — delete them (the ratchet "
                       "only counts if it tightens):\n" + "\n".join(stale))


def test_every_allowlisted_literal_states_a_reason():
    """A row with an empty reason is a row nobody has to justify."""
    bad = [("%s: %r" % (rel, lit))
           for rel, rows in LITERAL_ALLOW.items()
           for lit, why in rows.items() if not (why or "").strip()]
    assert not bad, "LITERAL_ALLOW rows need a reason:\n" + "\n".join(bad)


# ------------------------------------------------- 1b. the same, for the PAGE

# The PYTHON tier's ratchet above has a twin here because the browser is the
# other place a host gets known by heart — and the leak was bigger there: the
# new-session form carried four host-NAME-keyed tables (every model and effort
# level of BOTH tools, their defaults) read through a fallback that handed an
# unknown host CLAUDE's, and `shortModel` branched two hosts' model-id grammars
# inline. All of it is served now (`/api/hosts` + the session payload's host
# vocabulary), so what remains is countable.
#
# `gpt-`/`claude-` join the two plugin names because a MODEL-ID grammar is the
# same bug wearing a different string: `startsWith("gpt-")` names a host as
# surely as `=== "codex"` does.
JS_WORDS = ("claude_code", "codex", "gpt-", "claude-")

# {file: {word: (count, why)}} — an EXACT count, both directions: a new
# occurrence in an already-listed file fails just as a deleted one does, so the
# ratchet cannot be widened by writing another `=== "codex"` in a file that
# happens to be on the list. Line numbers are printed on failure.
JS_LITERAL_ALLOW = {
    # The `live`-table SLOT KIND vocabulary, not a host branch: `codex` is one
    # of the five kinds a running-slot row can carry (fg/bg/monitor/sub.pid/
    # codex — core/slots.py's palettes), and these two tables give each its
    # ribbon glyph and its place in the order. A row here says "how to draw a
    # codex RUN", which stays true no matter which tool hosts the session; the
    # unknown-kind path beside them (app.11-chrome.js) is already generic.
    "app.00-core.js": {
        "codex": (2, "the slot-KIND ribbon glyph + order (core/slots.py's "
                     "kinds), not a host check"),
        "claude-": (1, "the X-Claude-Dash request header — a protocol constant "
                       "named after the product, matched by http/base.py"),
    },
    # The presenter's ACTIVITY-CLASS vocabulary (view-mode folds, fragments,
    # counters, subjects) — `codex` here is the ACT TOKEN, whose vocabulary
    # core/ops.py owns (ACT_CODEX) and every producer stamps. The page needs a
    # phrase and a kind PER TOKEN, and one of the fourteen tokens happens to be
    # spelled like a host.
    #
    # P7 re-verified this row and it is at its floor. The only way to remove it
    # is to rename the token itself, which P6 considered and rejected: the token
    # rides the wire in each item's `act` field (renaming it changes rendered
    # output, which the corpus gate forbids) and it exists precisely so the
    # default summary can say "ran N codex runs" instead of folding a codex run
    # into "ran N agents" — the user-visible distinction the act field was added
    # for. A fourth host adds a token here the same way, and that is the
    # designed cost of a client-side phrase table.
    "app.05-session.js": {
        "codex": (5, "the act/view-mode vocabulary token (the ACT_CODEX twin) "
                     "— core owns the token, the page owns the phrase; not a "
                     "host branch"),
    },
    # The Web Push notification TAG, which must agree byte for byte with
    # notify/channels.push_tag — a retraction deletes the banner by tag, so the
    # service worker and the server have to spell it the same way. Product name,
    # not a host lookup.
    "sw.js": {
        "claude-": (1, "the push-notification tag prefix, pinned to "
                       "notify/channels.push_tag"),
    },
}

# /* … */ blocks and // line comments are stripped before the scan: PROSE may
# name a host freely (it is how the design is explained), the same rule the
# Python scan applies to docstrings. Line comments are cut only where the `//`
# is not preceded by `:` — enough to keep a `https://…` inside a string from
# eating the rest of its line. The failure mode of a bad strip is a MISSED
# literal, never a false one.
_JS_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_JS_LINE = re.compile(r"(^|[^:])//.*$")


def _js_uncommented(src):
    """`src` with its comments blanked, LINE NUMBERS preserved."""
    src = _JS_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), src)
    return [_JS_LINE.sub(r"\1", ln) for ln in src.split("\n")]


def _js_host_literals():
    """{file: {word: [line numbers]}} — every host name / model-id grammar left
    in the page's CODE."""
    found = {}
    root = os.path.join(REPO, "dashboard", "static")
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".js"):
            continue
        with open(os.path.join(root, fn), encoding="utf-8") as fh:
            lines = _js_uncommented(fh.read())
        for i, ln in enumerate(lines, 1):
            low = ln.lower()
            for w in JS_WORDS:
                if w in low:
                    found.setdefault(fn, {}).setdefault(w, []).append(i)
    return found


def test_no_host_name_literal_in_the_page_off_the_allowlist():
    """The static SPA names no host in code outside JS_LITERAL_ALLOW.

    The client half of "the dashboard knows no host". Before P5 the new-session
    form decided a whole form row, both option menus and their defaults by
    host NAME — and its fallback (`tbl[t] || tbl.claude_code`) meant a host
    nobody had written yet would be OFFERED Claude Code's models and launched
    with Claude Code's defaults, silently."""
    found = _js_host_literals()
    offenders = []
    for fn, words in sorted(found.items()):
        allowed = JS_LITERAL_ALLOW.get(fn, {})
        for w, lines in sorted(words.items()):
            want = (allowed.get(w) or (0, ""))[0]
            if len(lines) != want:
                offenders.append("%s: %r ×%d (allowed %d) at line(s) %s"
                                 % (fn, w, len(lines), want,
                                    ", ".join(str(n) for n in lines)))
    assert not offenders, (
        "host-name / model-grammar literal(s) in the page — read the fact off "
        "the served host vocabulary (/api/hosts, meta.*), or adjust "
        "JS_LITERAL_ALLOW with a reason:\n" + "\n".join(offenders))


def test_the_page_literal_allowlist_has_no_stale_rows():
    """Every allowlisted JS row still describes something that is there — the
    both-directions half, as for the Python list."""
    found = _js_host_literals()
    stale = []
    for fn, rows in sorted(JS_LITERAL_ALLOW.items()):
        have = found.get(fn, {})
        for w, (n, why) in sorted(rows.items()):
            if not (why or "").strip():
                stale.append("%s: %r has no reason" % (fn, w))
            if w not in have:
                stale.append("%s: %r no longer present (allowed %d)"
                             % (fn, w, n))
    assert not stale, ("stale JS_LITERAL_ALLOW row(s) — delete them (the "
                       "ratchet only counts if it tightens):\n"
                       + "\n".join(stale))


# ------------------------------------------------------- 2. provider coverage

IMPL = "impl"          # this plugin provides it
DECLINED = "declined"  # …and deliberately does not (see the plugin's own NB)

# {provider name: {plugin short name: IMPL | DECLINED}} — the second half of the
# PROVIDERS contract. plugins.PROVIDERS declares WHAT may be asked; this
# declares WHO answers. Adding a provider, or a host, fails here until the table
# says what the new cell is — which is the point: a provider nobody implements
# is a feature that degrades to "no plugin answered", the one failure mode a
# duck-typed registry cannot report on its own.
COVERAGE = {
    "on_session_start":  {"claude_code": DECLINED, "codex": IMPL,     "otel": IMPL},
    "census":            {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "agent_usage":       {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "runs":              {"claude_code": DECLINED, "codex": IMPL,     "otel": DECLINED},
    "nested_owners":     {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "monitors":          {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "owns":              {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "host":              {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "session_title":     {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "title_and_rename":  {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "renameable":        {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "set_session_title": {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "accounts":          {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "account_alias":     {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "model_windows":     {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "migration_target":  {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "launch_argv":       {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "slash_commands":    {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "config_dirs":       {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "effort_default":    {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "effort":            {"claude_code": DECLINED, "codex": IMPL,     "otel": DECLINED},
    "context":           {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "goal":              {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "model_fallback":    {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "prompts":           {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "conversation":      {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "ask_preamble":      {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "pending_dialog":    {"claude_code": DECLINED, "codex": IMPL,     "otel": DECLINED},
    # P3 — limits/accounts/costs, one vocabulary, BOTH hosts. `usage_strip`
    # replaced the old single-host `usage_windows` (first-plugin-wins, so codex
    # was the only possible answer and Claude's accounts rode a separate,
    # un-abstracted payload); the three session_* facets replaced core reads that
    # asked no host at all and therefore answered Claude's kv/OTEL shapes for
    # everyone. otel declines all four: it is a cross-cutting receiver, not a
    # host — the telemetry it collects is reported by the host that produced it.
    "usage_strip":       {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "session_usage":     {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "session_account":   {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "session_costs":     {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    # P4 — the three SESSION-STATE FACETS the dashboard used to read as raw kv /
    # hand-off rows by NAME, asking no host at all (and so answering Claude's
    # shapes for everyone, silently None for codex). `tasks` is the one honest
    # DECLINE of the three: an 80-rollout codex corpus holds no task-list tool,
    # so the card stays presence-hidden rather than being faked.
    "tasks":             {"claude_code": IMPL,     "codex": DECLINED, "otel": DECLINED},
    "compacting":        {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
    "fg_running":        {"claude_code": IMPL,     "codex": IMPL,     "otel": DECLINED},
}


def _plugin_names():
    import plugins
    return [p.__name__.rsplit(".", 1)[-1] for p in plugins.all_plugins()]


def test_provider_coverage_matrix_matches_reality():
    """COVERAGE says which plugin answers which provider; reality must agree.

    Both directions, because both mislead: a cell saying IMPL where the function
    is gone means a feature that silently degrades, and a cell saying DECLINED
    where one appeared means a decline nobody re-argued."""
    import plugins

    wrong = []
    for method in sorted(plugins.PROVIDERS):
        row = COVERAGE.get(method)
        assert row is not None, (
            "provider %r is declared in plugins.PROVIDERS but has no COVERAGE "
            "row — say per host whether it is implemented or declined" % method)
        for p in plugins.all_plugins():
            name = p.__name__.rsplit(".", 1)[-1]
            want = row.get(name)
            assert want is not None, (
                "COVERAGE[%r] has no cell for plugin %r" % (method, name))
            got = IMPL if plugins.provider(p, method) is not None else DECLINED
            if got != want:
                wrong.append("%s / %s: table says %s, reality is %s"
                             % (method, name, want, got))
    assert not wrong, ("provider-coverage table is out of date:\n"
                       + "\n".join(wrong))


def test_provider_coverage_matrix_has_no_extra_rows():
    """No COVERAGE row for a provider PROVIDERS doesn't declare, and no cell for
    a plugin that isn't registered — a stale row is a claim about code that no
    longer exists."""
    import plugins

    names = set(_plugin_names())
    extra = sorted(set(COVERAGE) - set(plugins.PROVIDERS))
    assert not extra, ("COVERAGE names undeclared provider(s): %s" % extra)
    for method, row in sorted(COVERAGE.items()):
        unknown = sorted(set(row) - names)
        assert not unknown, ("COVERAGE[%r] names unregistered plugin(s): %s"
                             % (method, unknown))
        bad = sorted(v for v in row.values() if v not in (IMPL, DECLINED))
        assert not bad, ("COVERAGE[%r] has non-vocabulary value(s): %s"
                         % (method, bad))


def test_every_host_declares_a_default_and_the_registry_owns_its_name():
    """plugins.default_host() names a real, launchable host — the ONE owner of
    that name, derived from the registry rather than authored in four places."""
    import plugins

    name = plugins.default_host()
    assert name in _plugin_names()
    h = plugins.host_named(name)
    assert h is not None and h.launchable and h.name == name
    # and it is what an unprovable session resolves to (the fail-OPEN rule)
    from dashboard.read import session as rsession
    assert rsession.session_caps("")[0] == name


# -------------------------------------- 2b. the one usage-window LABEL table

def test_every_hosts_window_label_comes_from_the_one_duration_table():
    """A rate-limit window's short LABEL is a fact about its DURATION, not about
    who reported it — `plugins.WINDOW_LABELS` / `plugins.window_label(mins)` is
    the one owner, and every host's strip builder routes through it.

    This is a real ratchet, not a tautology. The labels used to be per HOST by
    DESIGN ("each host says it the way its own UI does"): claude_code spelled
    10080 minutes "7d" and codex spelled the same 10080 "1w". But the two rows
    share the list strip, whose columns are keyed by duration so that codex's
    weekly bar sits directly under Claude's (docs/dashboard.md *Row alignment*)
    — so that was never two vocabularies, it was ONE COLUMN with two names,
    which is the thing a stack cannot survive. What stays per-host is only what
    the shared table does not know: a duration it has no word for (codex's own
    `1d`/`2w` ladder) and a SUFFIX on the shared word (Claude's per-model
    `7d fable`, a cap no other host reports)."""
    import plugins
    from plugins.claude_code import usage as CU
    from plugins.codex import usage as XU

    # the table itself, and the fallback contract: an unknown duration yields
    # the CALLER's word, never a fabricated one
    assert plugins.WINDOW_LABELS == {300: "5h", 10080: "7d"}
    assert plugins.window_label(1440) == ""
    assert plugins.window_label(1440, fallback="1d") == "1d"
    assert plugins.window_label(None, fallback="primary") == "primary"

    # BOTH hosts agree, window for window, on every duration the table names —
    # asked through each host's OWN public label builder (Claude's is keyed by
    # its window KEY, codex's by minutes; that difference is the point)
    for key, mins in (("five_hour", 300), ("seven_day", 10080)):
        shared = plugins.window_label(mins)
        assert CU.window_label(key) == shared, key
        assert XU.window_label(mins) == shared, mins

    # and the per-host remainder is exactly the two sanctioned kinds
    assert CU.window_label("seven_day_fable") == "7d fable"   # shared + suffix
    assert XU.window_label(1440) == "1d"                      # unnamed duration


# --------------------------------------------------- 3. one register table

def test_register_table_is_the_single_source():
    """`opshtml/actclass`'s register→act map and its wording fallback are DERIVED
    from core/agentblocks.REGISTERS, not re-spelled beside it.

    Independent copies of one closed vocabulary is what this replaced, and the
    failure mode of missing one when a host is added is SILENT: an unrecognised
    register falls through to the palette test and folds every block into "ran N
    agents", and its child gets named in the wrong register's word.

    The scope filter is no longer one of the readers at all: `agent_scope` is the
    bare AGENT ID and `in_scope` matches the id, so the prefix vocabulary cannot
    be missed there because it is not consulted."""
    from core import agentblocks as AB
    from core import ops as O
    from dashboard.opshtml import actclass
    from dashboard.read import mirror

    # the scope is the id itself — no table, nothing to keep in step
    assert mirror.agent_scope("s", "aid42") == "aid42"
    assert mirror.agent_scope("s", "") is None          # the session view

    # the presenter's register->act map IS the table's
    assert actclass._SRC_ACT == AB.src_acts()
    # …and the tokens it yields are the ACT vocabulary — which core owns, so the
    # producer stamping `act` and the presenter classifying into it cannot
    # disagree about a token
    assert actclass.ACTS is O.ACTS
    assert set(actclass._SRC_ACT.values()) <= set(actclass.ACTS)
    assert actclass._SRC_ACT[AB.REGISTERS[AB.REG_AGENT]["src"]] == actclass.ACT_AGENT
    assert actclass._SRC_ACT[AB.REGISTERS[AB.REG_TEAM]["src"]] == actclass.ACT_TEAM
    assert actclass._SRC_ACT[AB.REGISTERS[AB.REG_CODEX]["src"]] == actclass.ACT_CODEX

    # every row carries the WHOLE per-register vocabulary — a new host adds a
    # row, and this is the list of what a row has to answer
    for name, row in AB.REGISTERS.items():
        assert set(row) == {"src", "act", "word", "palette"}, name
        assert row["act"] in O.ACTS and row["word"].count("%s") == 1
        assert row["palette"] and all(len(c) == 3 for c in row["palette"])
    # the display WORD is data, not a branch: the codex register's is the generic
    # host shape with its own name bound (there is no CODEX_WORD constant left)
    from core import streamfmt as SF
    assert AB.register_word(AB.REG_CODEX) == SF.host_word("Codex")
    assert AB.register_word(AB.REG_AGENT) == 'Agent "%s"'
    assert not hasattr(SF, "CODEX_WORD") and not hasattr(SF, "codex_note")
    # …and the read side resolves a `src` stamp back to its register
    assert AB.register_of_src("team:a1") == AB.REG_TEAM
    assert AB.register_of_src("codex:x") == AB.REG_CODEX
    assert AB.register_of_src("gem:x") is None and AB.register_of_src("") is None
    # as_lead's colour fallback is EVERY register's palette, from the same table
    # (it used to be the two Claude ones plus a `lead` flag naming which
    # registers may recolour — an enumeration of known hosts)
    assert actclass._CHILD_RGB == frozenset(AB.stream_palettes())
    assert not hasattr(AB, "lead_src_prefixes")


def test_the_register_prefixes_are_not_re_spelled_in_their_readers():
    """Equality above could be a coincidence; this is the half that makes it
    derivation. Neither reader may contain a `<prefix>:` string literal."""
    stamps = {p + ":" for p in
              __import__("core.agentblocks", fromlist=["x"]).src_prefixes()}
    for rel in ("dashboard/read/mirror.py", "dashboard/opshtml/actclass.py"):
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=rel)
        docs = _docstring_nodes(tree)
        hits = sorted({n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant)
                       and isinstance(n.value, str)
                       and id(n) not in docs and n.value in stamps})
        assert not hits, ("%s re-spells register prefix(es) %s — derive them "
                          "from core/agentblocks.REGISTERS" % (rel, hits))


# ------------------------------------------------------ 4. the gesture surface

# The FAMILIES of HostControl method are declared by the PRODUCT
# (plugins/host.py: GESTURES / SIBLINGS / VOCABULARY / PLUMBING, plus the
# INFRASTRUCTURE that is the interface's own machinery). They used to be tuples
# in THIS file, which made the coverage check below a tautology for 17 of its 28
# rows: the "declared surface" it compared HOST_SURFACE against was a list the
# test itself owned, so a new HostControl method — overridden by a host, live in
# production — landed with no failure at all. Demonstrated, then fixed by moving
# the tables to the class they describe and asserting the partition is TOTAL.
#
# {method: {host: IMPL | DECLINED}} — the gesture-side twin of COVERAGE. A
# DECLINE here is a DECISION with a written reason in the host's own module (see
# the "NOT overridden" notes in plugins/codex/hostctl.py), and the table is what
# makes it visible: without it, "codex has no `send` body" and "somebody forgot
# to write one" look identical from the outside, and the difference is exactly
# what the 409-vs-502 split turns on.
HOST_SURFACE = {
    # the ten capability gestures
    "interrupt": {"claude_code": IMPL,     "codex": IMPL},
    "send":      {"claude_code": IMPL,     "codex": IMPL},
    "rename":    {"claude_code": IMPL,     "codex": IMPL},
    "rewind":    {"claude_code": IMPL,     "codex": DECLINED},
    "migrate":   {"claude_code": IMPL,     "codex": DECLINED},
    "compact":   {"claude_code": IMPL,     "codex": IMPL},
    "model":     {"claude_code": IMPL,     "codex": IMPL},
    "effort":    {"claude_code": IMPL,     "codex": IMPL},
    "ask":       {"claude_code": IMPL,     "codex": IMPL},
    "plan":      {"claude_code": IMPL,     "codex": IMPL},
    # the cap SHARERS
    "rewind_to":    {"claude_code": IMPL, "codex": DECLINED},
    "autoname":     {"claude_code": IMPL, "codex": DECLINED},
    "plan_options": {"claude_code": IMPL, "codex": IMPL},
    "deliver":      {"claude_code": IMPL, "codex": DECLINED},
    # the vocabulary / screen reads
    "mention":        {"claude_code": IMPL, "codex": DECLINED},
    "clear_input":    {"claude_code": IMPL, "codex": DECLINED},
    "turn_live":      {"claude_code": IMPL, "codex": DECLINED},
    "ask_declines":   {"claude_code": IMPL, "codex": DECLINED},
    "plan_decisions": {"claude_code": IMPL, "codex": IMPL},
    "rewind_modes":   {"claude_code": IMPL, "codex": DECLINED},
    "rewind_mode_label": {"claude_code": IMPL, "codex": DECLINED},
    "command_floor":  {"claude_code": IMPL, "codex": DECLINED},
    "title_key":      {"claude_code": IMPL, "codex": IMPL},
    # the ONE row where claude_code is the decline: a title-freshness stamp is
    # only needed by a host that keeps the name OUTSIDE the transcript, and
    # Claude Code writes it in (docs/codex.md *A rename the read model can SEE*)
    "title_sig":      {"claude_code": DECLINED, "codex": IMPL},
    "input_box":      {"claude_code": IMPL, "codex": DECLINED},
    "ask_region":     {"claude_code": IMPL, "codex": DECLINED},
    "typed_input":    {"claude_code": IMPL, "codex": DECLINED},
    "lifecycle_end":  {"claude_code": IMPL, "codex": IMPL},
    # the LAUNCH/model plumbing (plugins.host.PLUMBING) — no caps, but the same
    # both-directions pin: these compose a launch and spell a model id, and an
    # override that appears or disappears here changes what the new-session form
    # offers. They were outside the table entirely until P7's audit.
    "model_choices":  {"claude_code": IMPL, "codex": IMPL},
    "effort_choices": {"claude_code": IMPL, "codex": IMPL},
    "model_default":  {"claude_code": IMPL, "codex": IMPL},
    "effort_default": {"claude_code": IMPL, "codex": IMPL},
    "model_short":    {"claude_code": IMPL, "codex": DECLINED},
    "model_default_effort": {"claude_code": IMPL, "codex": DECLINED},
    "launch_words":   {"claude_code": IMPL, "codex": IMPL},
    # codex's launch command word IS its host name, which is the base's own
    # answer — a DECLINE that is the right one, not a gap
    "launch_cmd":     {"claude_code": IMPL, "codex": DECLINED},
}


def _host_objs():
    import plugins
    return {h["name"]: plugins.host_named(h["name"]) for h in plugins.hosts()}


def test_the_gesture_surface_table_matches_reality():
    """HOST_SURFACE says which host overrides which method; reality must agree.

    Both directions, like COVERAGE. A cell that says IMPL where the override is
    gone means a button that silently answers `unsupported`; one that says
    DECLINED where a body appeared means a gesture nobody argued for — and for
    the SIBLINGS in particular the difference is a 409 the client renders as
    "your tool can't do that"."""
    from plugins.host import HostControl

    wrong = []
    for name, row in sorted(HOST_SURFACE.items()):
        assert hasattr(HostControl, name), (
            "HOST_SURFACE names %r, which is not on HostControl" % name)
        for host_name, host in sorted(_host_objs().items()):
            want = row.get(host_name)
            assert want is not None, (
                "HOST_SURFACE[%r] has no cell for host %r" % (name, host_name))
            got = (IMPL if getattr(type(host), name)
                   is not getattr(HostControl, name) else DECLINED)
            if got != want:
                wrong.append("%s / %s: table says %s, reality is %s"
                             % (name, host_name, want, got))
    assert not wrong, ("host-gesture surface table is out of date:\n"
                       + "\n".join(wrong))


def _public_methods():
    """Every PUBLIC callable on HostControl — the class's real surface, read off
    the class rather than off any list describing it."""
    from plugins.host import HostControl
    return {n for n in dir(HostControl)
            if not n.startswith("_") and callable(getattr(HostControl, n))}


def test_the_declared_families_partition_the_whole_class():
    """THE anti-tautology check: the product's four families plus its
    infrastructure account for EVERY public callable on HostControl, and name
    nothing that isn't one.

    Without this, "the declared surface" was whatever the tables listed, so a
    new method — overridden by a host and live in production — was covered by
    definition and pinned by nothing. Adding one now fails here until it is
    filed under a family, which is the moment to decide what KIND of surface it
    is: a capability gesture, a cap sharer, a vocabulary declaration, or launch
    plumbing."""
    from plugins import host as H

    families = {"GESTURES": set(H.GESTURES), "SIBLINGS": set(H.SIBLINGS),
                "VOCABULARY": set(H.VOCABULARY), "PLUMBING": set(H.PLUMBING),
                "INFRASTRUCTURE": set(H.INFRASTRUCTURE)}
    filed = set()
    for name, fam in sorted(families.items()):
        dupe = sorted(fam & filed)
        assert not dupe, "%s re-files %s (a method has exactly one family)" % (
            name, dupe)
        filed |= fam
    have = _public_methods()
    assert not (filed - have), (
        "the declared families name method(s) HostControl does not have: %s"
        % sorted(filed - have))
    assert not (have - filed), (
        "HostControl method(s) in no declared family — file each under "
        "GESTURES / SIBLINGS / VOCABULARY / PLUMBING in plugins/host.py (or "
        "INFRASTRUCTURE if it is the interface's own machinery): %s"
        % sorted(have - filed))


def test_the_gesture_surface_table_covers_every_declared_method():
    """Every method a host can implement has a per-host row, and no row names
    something that isn't one. A method absent from the table is a surface
    nobody has said, per host, whether it is answered.

    Together with the partition above this is no longer circular: the families
    come from the product, the partition proves they cover the class, and this
    proves the table covers the families."""
    from plugins import host as H

    declared = (set(H.GESTURES) | set(H.SIBLINGS) | set(H.VOCABULARY)
                | set(H.PLUMBING))
    assert set(HOST_SURFACE) == declared, (
        "HOST_SURFACE and the declared surface disagree: missing %s, extra %s"
        % (sorted(declared - set(HOST_SURFACE)),
           sorted(set(HOST_SURFACE) - declared)))


def test_caps_are_derived_from_the_gestures_alone():
    """A cap exists for exactly one gesture, and the siblings do NOT get one:
    `caps` is what the client greys buttons on and what `_caps_guard` reads, so a
    second method under one cap would make the two disagree about which body
    answers. The sibling's own `unsupported` result is the finer-grained refusal."""
    import plugins
    from plugins import host as H

    for name, host in sorted(_host_objs().items()):
        assert set(host.caps()) == set(H.GESTURES), name
    assert not (set(H.SIBLINGS) & set(H.GESTURES))
    # …and every sibling names a REAL cap to ride
    assert set(H.SIBLINGS.values()) <= set(H.GESTURES)
    # the derivation itself: caps == the overrides, for every registered host
    for name, host in sorted(_host_objs().items()):
        for g in H.GESTURES:
            assert host.caps()[g] is (HOST_SURFACE[g][name] == IMPL), (name, g)
    assert plugins.host_caps("nope") == {}


def test_an_unimplemented_gesture_says_unsupported_not_failed():
    """The inert base's result carries `unsupported`, and a host's own FAILURE
    result does not. That one key is how a caller answers 409 ("your tool does
    not do this", naming the capability) where it would otherwise answer 502
    ("it tried and it broke") — the two used to be indistinguishable, which is
    how a renameable-but-uncapped host surfaced its refusal as a malfunction."""
    from plugins.host import REJECTED, HostControl

    base = HostControl()
    calls = {
        "interrupt": lambda: base.interrupt(None, "", {}),
        "send": lambda: base.send(None, "", "hi", {}),
        "rename": lambda: base.rename("s", "n", {}),
        "rewind": lambda: base.rewind(None, "", {}),
        "migrate": lambda: base.migrate("s", {}),
        "compact": lambda: base.compact(None, "", {}),
        "model": lambda: base.model(None, "", "m", {}),
        "effort": lambda: base.effort(None, "", "high", {}),
        "ask": lambda: base.ask(None, "", [], {}),
        "plan": lambda: base.plan(None, "", {}, {}),
        "rewind_to": lambda: base.rewind_to(None, "", "t", "both", {}),
        "autoname": lambda: base.autoname(None, "", {}),
        "plan_options": lambda: base.plan_options(None, "", {}),
        "deliver": lambda: base.deliver(None, "", "hi", {}),
    }
    for g, call in sorted(calls.items()):
        res = call()
        assert res["status"] == REJECTED and res["unsupported"] == g, g
    assert "unsupported" not in HostControl._rejected()
    # …and the inert VOCABULARY is empty rather than another host's words
    assert base.mention("/p") == "" and base.title_key("/x.jsonl") == ""
    assert base.ask_declines() == () and base.plan_decisions() == ()
    assert base.rewind_modes() == () and base.turn_live(None, "") is None
    assert base.input_box(None, "") == (None, None)
    assert base.paste_grabs_clipboard_image is False


def test_every_hosts_vocabulary_is_a_closed_word_list():
    """The decline/decision vocabularies are TUPLES of words the handler
    validates a request against — not booleans, and not free text. A host that
    accepts nothing says so with an empty one (which is what produces the 409
    naming "none"), and every word a host DOES name must be one the plan handler
    knows how to build a body for."""
    known = {"decide", "feedback", "dismiss"}
    for name, host in sorted(_host_objs().items()):
        assert isinstance(host.ask_declines(), tuple), name
        assert isinstance(host.plan_decisions(), tuple), name
        assert isinstance(host.rewind_modes(), tuple), name
        assert set(host.plan_decisions()) <= known, name
    hosts = _host_objs()
    # today's per-host truth, pinned so a change is deliberate
    assert hosts["claude_code"].ask_declines() == ("chat",)
    assert hosts["codex"].ask_declines() == ()
    assert hosts["claude_code"].plan_decisions() == ("decide", "feedback",
                                                     "dismiss")
    assert hosts["codex"].plan_decisions() == ("decide", "dismiss")


def test_every_host_serves_its_whole_new_session_vocabulary(tmp_path, monkeypatch):
    """`/api/hosts` carries everything the page needs to BUILD ITS FORM for a
    host it has never heard of: the picker row, both option menus with their
    first-ever defaults, how a menu row matches a running model, whether the
    tool has an account switcher or a file-mention grammar, its rewind modes
    WITH their words, and its quick commands with their refusal floors.

    The values are pinned because they are a MIGRATION: each one was a literal
    in dashboard/static/app.09-newsession.js (TOOL_MODELS / TOOL_EFFORTS /
    TOOL_MODEL_DEF / TOOL_EFFORT_DEF) or app.10-control.js (MODEL_CHOICES /
    EFFORT_CHOICES / RW_MODES / COMPACT_MIN_PROMPTS), and P5's whole claim is
    that only their SOURCE moved."""
    import plugins

    rows = {h["name"]: h for h in plugins.hosts()}
    keys = {"name", "label", "launchable", "default", "accounts", "attach",
            "model_choices", "effort_choices", "model_default",
            "effort_default", "model_match", "rewind_modes", "quick_commands"}
    for name, row in sorted(rows.items()):
        assert set(row) == keys, name
    cc, cx = rows[plugins.default_host()], rows["codex"]
    # exactly one DEFAULT, and it is the registry's own answer
    assert [h["name"] for h in plugins.hosts() if h["default"]] == [cc["name"]]
    # the two menus, verbatim from the client tables they replaced
    assert cc["model_choices"] == ["fable", "opus", "sonnet", "haiku"]
    assert cc["effort_choices"] == ["low", "medium", "high", "xhigh", "max"]
    assert cc["model_default"] == "fable" and cc["effort_default"] == "high"
    assert cx["model_choices"][0] == "gpt-5.6-sol"
    assert cx["model_default"] == "gpt-5.6-sol" and cx["effort_default"] == "low"
    # `ultra` is codex's level and is deliberately NOT in Claude's menu, though
    # the arg validator's EFFORTS (the UNION over hosts) accepts it
    assert "ultra" in cx["effort_choices"] and "ultra" not in cc["effort_choices"]
    from dashboard import config
    assert set(cc["effort_choices"]) | set(cx["effort_choices"]) \
        <= set(config.EFFORTS)
    # the match rule: family rows for the default host, full ids for codex
    assert cc["model_match"] == "family" and cx["model_match"] == "exact"
    # the account switcher is DERIVED from the plugin providing that registry
    assert cc["accounts"] is True and cx["accounts"] is False
    # …and the registry itself is MACHINE state (the switcher's accounts.tsv),
    # so seed a fake one instead of asserting on this machine's — the original
    # bare `bool(plugins.accounts())` was green locally and red on CI, which
    # has no switcher (the hermeticity rule in docs/styleguide.md).
    from plugins.claude_code import account as ACC
    tsv = tmp_path / "accounts.tsv"
    tsv.write_text("c1\toboard\tsvc\n", encoding="utf-8")
    monkeypatch.setattr(ACC, "ACCOUNTS_TSV", str(tsv))
    assert bool(plugins.accounts())
    assert plugins.host_named("codex").mention("/p") == ""
    # …and the mention grammar from HostControl.mention
    assert cc["attach"] is True and cx["attach"] is False
    # the rewind menu, modes AND the words for them
    assert cc["rewind_modes"] == [
        {"mode": "both", "label": "restore code and conversation"},
        {"mode": "conversation", "label": "restore conversation"},
        {"mode": "code", "label": "restore code"}]
    assert cx["rewind_modes"] == []          # codex cannot rewind at all
    # the quick commands a host OFFERS are derived from its overrides (codex
    # renames but has no argless autoname, so it has no `rename` row), and each
    # carries the floor that host measured
    assert cc["quick_commands"] == [
        {"cmd": "compact", "min_prompts": 2}, {"cmd": "model", "min_prompts": 0},
        {"cmd": "effort", "min_prompts": 0}, {"cmd": "rename", "min_prompts": 1}]
    assert [c["cmd"] for c in cx["quick_commands"]] == ["compact", "model",
                                                        "effort"]
    assert all(c["min_prompts"] == 0 for c in cx["quick_commands"])
    # every wire word names a real method + a real cap, and the /command guard
    # reads its caps out of that ONE table rather than a second copy
    from dashboard.http.post import typing as T
    from plugins import host as H
    for cmd, (method, cap) in H.QUICK_COMMANDS.items():
        assert hasattr(H.HostControl, method), cmd
        assert cap in H.GESTURES, cmd
        assert method in set(H.GESTURES) | set(H.SIBLINGS), cmd
    assert T.CAP_BY_CMD == plugins.quick_command_caps()
    assert T.CAP_BY_CMD == {"compact": "compact", "model": "model",
                            "effort": "effort", "rename": "rename"}


def test_one_builder_serves_both_wire_surfaces():
    """The SAME vocabulary rides the session payload as rides /api/hosts — one
    builder (plugins.host_vocabulary), so the per-tool answer and the
    per-session answer cannot disagree about a host, and the session payload's
    keys ARE that builder's (dashboard/read/session.py spreads it)."""
    import plugins

    vocab = plugins.host_vocabulary(plugins.host_named("codex"))
    row = next(h for h in plugins.hosts() if h["name"] == "codex")
    for k, v in vocab.items():
        assert row[k] == v, k
    # …and the inert host answers with EMPTY everything rather than the default
    # host's words (the "no host at all" answer must not be another tool's)
    inert = plugins.host_vocabulary(plugins.inert_host())
    assert inert["model_choices"] == [] and inert["effort_choices"] == []
    assert inert["rewind_modes"] == [] and inert["quick_commands"] == []
    assert inert["model_default"] == "" and inert["model_match"] == "exact"


def test_rewind_modes_comes_from_the_menu_table_not_a_second_list():
    """`rewind_modes()` IS rewindmenu.MODE_LABELS' key set — one owner. That
    table maps each mode to the label matched on the real menu, so a second list
    would be a set of modes with no menu row behind them."""
    import plugins
    from plugins.claude_code import rewindmenu

    host = plugins.host_named(plugins.default_host())
    assert host.rewind_modes() == tuple(rewindmenu.MODE_LABELS)
    assert plugins.host_named("codex").rewind_modes() == ()


def test_the_claude_screen_drivers_live_in_the_plugin():
    """The five Claude Code SCREEN DRIVERS are the plugin's, not the
    dashboard's — the symmetry plugins/codex/hostctl.py declares for its own
    (docs/architecture.md). Only the tool-agnostic skeleton is shared, and it
    sits in CORE because a plugin may not import the dashboard."""
    for mod in ("askdialog", "plandialog", "rewindmenu", "confirmdialog",
                "suggestion"):
        assert os.path.isfile(os.path.join(REPO, "plugins/claude_code",
                                           mod + ".py")), mod
        assert not os.path.isfile(os.path.join(REPO, "dashboard",
                                               mod + ".py")), mod
    assert os.path.isfile(os.path.join(REPO, "core/screendrive.py"))


def test_no_plugin_imports_the_dashboard():
    """The dependency rule, in the direction the driver move could have broken:
    plugins import core + frontends, never the consumer tier above them."""
    bad = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, "plugins")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for n in ast.walk(tree):
                mod = ((n.module or "") if isinstance(n, ast.ImportFrom)
                       else "")
                names = ([a.name for a in n.names]
                         if isinstance(n, ast.Import) else [])
                if (mod == "dashboard" or mod.startswith("dashboard.")
                        or any(a == "dashboard" or a.startswith("dashboard.")
                               for a in names)):
                    bad.append("%s:%d" % (os.path.relpath(path, REPO),
                                          n.lineno))
    assert bad == [], "plugin module(s) importing the dashboard: %s" % bad


# ------------------------------------------------- 3b. the producer-stamped act

# {module: the act tokens its emit sites must stamp} — the DECLARED table, one
# row per producer that paints a block header or a one-liner. A grep rather than
# a drive, deliberately: most of these are hook handlers that need a whole
# payload + a live state DB to reach their emit (the e2e suites do that), and
# what this guards is narrower and never worth a fixture — that the site KEEPS
# saying what it paints. A producer that stops stamping does not fail: it
# silently falls back to the glyph tables, which answer for two hosts.
ACT_PRODUCERS = {
    "core/agentblocks.py": ("ACT_TOOL", "ACT_BASH", "ACT_BG"),
    "core/streamfmt.py": ("ACT_BASH",),
    "core/errwatch.py": ("ACT_WARN",),
    "plugins/claude_code/cmd_pre.py": ("ACT_BASH",),
    "plugins/claude_code/cmd_fmt.py": ("ACT_BG", "ACT_READ"),
    "plugins/claude_code/monitor_fmt.py": ("ACT_MONITOR",),
    "plugins/claude_code/task_fmt.py": ("ACT_TASK",),
    "plugins/claude_code/skill_fmt.py": ("ACT_SKILL",),
    "plugins/claude_code/msgs.py": ("ACT_MAIL",),
    "plugins/codex/stream.py": ("ACT_TOOL", "ACT_BASH"),
}


def test_every_producer_stamps_the_activity_class():
    """Each producer names the `act` it paints, from the core vocabulary.

    The presenter reads this FIRST and keeps its glyph/palette tables only for
    ops already on disk — so a producer that stops stamping degrades into the
    host-blind sniff instead of failing, which is precisely the kind of
    regression a table has to catch."""
    from core import ops as O

    missing = []
    for rel, tokens in sorted(ACT_PRODUCERS.items()):
        with open(os.path.join(REPO, rel), encoding="utf-8") as fh:
            src = fh.read()
        assert "act=" in src, "%s stamps no act at all" % rel
        for tok in tokens:
            assert hasattr(O, tok), tok          # the token is core's
            if ("O." + tok) not in src and ("ops." + tok) not in src:
                missing.append("%s: %s" % (rel, tok))
    assert not missing, ("producer(s) no longer stamping their activity class "
                         "— they would fall back to the glyph sniff:\n"
                         + "\n".join(missing))


def test_the_act_vocabulary_is_cores_and_the_field_is_validated():
    """core/ops.py owns the tokens (a producer may not import the dashboard) and
    REFUSES one it doesn't know: an op stamped out of vocabulary would reach a
    page that has no phrase for it, so the field is dropped and the reader falls
    back to its own derivation — a worse answer, never a wrong one."""
    from core import ops as O
    from dashboard.opshtml import actclass

    assert actclass.ACTS is O.ACTS and len(set(O.ACTS)) == len(O.ACTS)
    assert O.label("h", (1, 2, 3), act=O.ACT_BASH)["act"] == "bash"
    assert "act" not in O.label("h", (1, 2, 3), act="nonsense")
    assert "act" not in O.label("h", (1, 2, 3))
    assert O.line("x", act=O.ACT_READ)["act"] == "read"
    assert O.gut("b", (1, 2, 3), act=O.ACT_EDIT)["act"] == "edit"
    # …and the presenter honours a valid hint over its own tables: this chip
    # opens with the MONITOR glyph and would classify as one
    op = O.label("◉ monitor · x", (1, 2, 3), act=O.ACT_MAIL)
    assert actclass.classify(op) == (O.ACT_MAIL, False)
    # an unknown token never reaches the op, so the derivation answers
    assert actclass.classify(O.label("◉ monitor · x", (1, 2, 3),
                                     act="nonsense"))[0] == O.ACT_MONITOR


# ------------------------------- no host declares how its own prose is dropped

def test_the_prose_drop_asks_the_op_not_the_host():
    """A host's own re-bubbled prose is dropped by the PRODUCER-set `bubbled`
    flag in every view — no per-host declaration, and nothing left in the read
    model to resolve one.

    The predecessors, in order: `is_codex_lead` (body `owns_by(tpath) ==
    "codex"`), then `HostControl.lead_prose` read through `mirror.host_lead` and
    threaded as `host_lead=` through seven call sites. The trait was already a
    fact rather than a name — but it is per-HOST, and the question is per-OP:
    only some of a host's ops are re-bubbled, and the producer of each one knows
    which. A flag on the op cannot be out of date about itself.

    Both wrong answers are silent, which is why this is pinned: the mirror either
    doubles every message or folds a whole session into one summary line."""
    import plugins
    from dashboard import opshtml
    from dashboard.read import mirror
    from plugins.host import HostControl

    assert not hasattr(mirror, "host_lead")
    assert not hasattr(HostControl, "lead_prose")
    assert not hasattr(plugins.host_named("codex"), "lead_prose")
    # …and no caller can pass one: the parameter is gone from the presenter
    import inspect
    assert "host_lead" not in inspect.signature(opshtml.op_items).parameters
    for fn in (mirror.merge_live, mirror._render_window):
        assert "host_lead" not in inspect.signature(fn).parameters

    # the flag itself, in BOTH views — with the one structural exemption
    op = {"t": "label", "s": "✎ message", "c": [1, 2, 3], "bubbled": 1}
    assert opshtml.op_items([dict(op)], "k") == []              # session view
    assert opshtml.op_items([dict(op, src="sub:a1")], "k", scope="a1") == []
    web = dict(op, src="sub:a1", web=1)                # a child's endpoint card
    assert opshtml.op_items([web], "k")                # …kept in the LEAD's view
    assert opshtml.op_items([web], "k", scope="a1") == []   # …dropped in its own
