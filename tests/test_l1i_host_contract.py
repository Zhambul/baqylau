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
import ast
import os

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
    # A host-NAMED endpoint over a generic fan-out: /api/codex-usage +
    # get_codex_usage serve plugins.usage_windows(), which is first-plugin-wins
    # and therefore single-host by construction. P3 replaces both with one
    # host-keyed usage surface and these two rows go.
    "dashboard/http/get.py": {
        "codex-usage": "P3: the host-named rate-limit endpoint, becomes /api/usage",
        "get_codex_usage": "P3: its handler name, deleted with the route",
    },
    # The presenter's parked-history sniffers. ACT_CODEX is the dashboard's own
    # activity-class token (the page's ACT_PHRASE table is keyed by it) and only
    # COINCIDES with the register name — check 3 below pins that coincidence
    # rather than leaving it to luck. The two banner/footer string compares are
    # text sniffs over ops written before the `chrome` flag existed; P6 demotes
    # them to explicitly-parked-only.
    "dashboard/opshtml/actclass.py": {
        "codex": "ACT_CODEX, the presenter's own act token (pinned to the "
                 "register table by test_register_table_is_the_single_source)",
        "codex ": "P6: parked-history sniff for the pre-`chrome` run banner",
        "■ codex ": "P6: parked-history sniff for the pre-`chrome` run footer",
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
    "usage_windows":     {"claude_code": DECLINED, "codex": IMPL,     "otel": DECLINED},
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


# --------------------------------------------------- 3. one register table

def test_register_table_is_the_single_source():
    """`read/mirror.agent_scope`'s prefixes and `opshtml/actclass`'s register→act
    map are DERIVED from core/agentblocks.REGISTERS, not re-spelled beside it.

    Three independent copies of one closed vocabulary is what this replaced, and
    the failure mode of missing one when a host is added is SILENT: an
    unrecognised `src` prefix matches no op, so that agent's mirror renders
    blank, and an unrecognised register falls through to the palette test and
    folds every block into "ran N agents"."""
    from core import agentblocks as AB
    from dashboard.opshtml import actclass
    from dashboard.read import mirror

    # the scope filter's prefix set IS the table's, applied to one id
    assert mirror.agent_scope("s", "aid42") == {p + ":aid42"
                                               for p in AB.src_prefixes()}
    assert mirror.agent_scope("s", "") is None          # the session view

    # the presenter's register->act map IS the table's
    assert actclass._SRC_ACT == AB.src_acts()
    # …and the tokens it yields are the presenter's OWN act vocabulary — the
    # coincidence the actclass literal allowlist row leans on, pinned here
    assert set(actclass._SRC_ACT.values()) <= set(actclass.ACTS)
    assert actclass._SRC_ACT[AB.REGISTERS[AB.REG_AGENT]["src"]] == actclass.ACT_AGENT
    assert actclass._SRC_ACT[AB.REGISTERS[AB.REG_TEAM]["src"]] == actclass.ACT_TEAM
    assert actclass._SRC_ACT[AB.REGISTERS[AB.REG_CODEX]["src"]] == actclass.ACT_CODEX

    # …and as_lead's recolour prefixes are the table's `lead` field, colon
    # included. The codex register is deliberately absent — an ASYMMETRY that is
    # now a field rather than an omission from an inline tuple.
    assert actclass._LEAD_SRC == AB.lead_src_prefixes()
    assert actclass._LEAD_SRC == ("sub:", "team:")
    assert AB.REGISTERS[AB.REG_CODEX]["lead"] is False


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


# --------------------------------------------------- the lead_prose host trait

def test_lead_prose_is_a_trait_not_a_host_name():
    """A host whose OWN session stream carries its prose twice declares it
    (`HostControl.lead_prose`); read/mirror.host_lead reads the trait off the
    owning host instead of comparing its name to "codex".

    The old `owns_by(tpath) == "codex"` broke silently in the worst direction:
    the wrong answer either DOUBLES every message in the mirror or folds a whole
    session into one summary line, and nothing raises."""
    import plugins
    from plugins.host import HostControl

    assert HostControl.lead_prose is False              # the honest default
    assert plugins.host_named("codex").lead_prose is True
    assert plugins.host_named(plugins.default_host()).lead_prose is False


def test_host_lead_is_false_in_agent_scope_and_for_the_default_host(monkeypatch):
    """host_lead is a SESSION-view question: a sidecar run IS a sub-run, so any
    agent scope answers False regardless of host."""
    import plugins
    from dashboard.read import mirror

    monkeypatch.setattr(mirror.API, "session_row",
                        lambda sid: {"transcript_path": "/x/rollout.jsonl"})
    monkeypatch.setattr(plugins, "host_of",
                        lambda p: plugins.host_named("codex"))
    assert mirror.host_lead("s", None) is True
    assert mirror.host_lead("s", "some-agent") is False   # agent scope: never
    monkeypatch.setattr(plugins, "host_of", lambda p: None)
    assert mirror.host_lead("s", None) is False           # unclaimed path
