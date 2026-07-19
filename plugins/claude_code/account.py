# plugins/claude_code/account.py — the account/subscription vocabulary owner.
#
# Multiple Claude subscriptions are juggled by the `claude-subscription` wrapper
# (github.com/leegunwoo98/claude-code-account-switcher, aliased c1/c2 in the
# user's zsh): each `claude-subscription <slug>` exports CLAUDE_SUBSCRIPTION_SLUG
# + CLAUDE_SUBSCRIPTION_LABEL and injects that account's keychain token. A hook
# process inherits those env vars, so the account a session runs under is knowable
# WITHOUT touching any token — this module is the ONE place that env contract and
# the accounts registry are read (docs/dashboard.md, "Accounts & usage").
#
# The plain `claude` alias (no wrapper) is the DEFAULT account: empty slug, the
# `claude` launch word. Everything else is a row of the switcher's accounts.tsv.

import os
import re

from core import sessionapi as API

# The switcher's registry: TSV rows `slug<TAB>label<TAB>keychain-service`.
ACCOUNTS_TSV = os.path.expanduser(
    "~/.config/claude-subscriptions/accounts.tsv")
# The switcher's per-account config dirs (each exported as that account's
# CLAUDE_CONFIG_DIR): configs/<slug> next to the registry.
CONFIGS_DIR = os.path.expanduser("~/.config/claude-subscriptions/configs")

_SLUG_OK = re.compile(r"^[A-Za-z0-9._-]+$")   # a clean argv/keychain bareword
DEFAULT_ALIAS = "claude"                       # the plain (default-account) launch word


def current():
    """The account THIS process runs under, from the switcher's env contract.
    Empty slug ⇒ the plain-`claude` default account (the wrapper wasn't used).
    Never raises — a missing var is just the default."""
    slug = (os.environ.get("CLAUDE_SUBSCRIPTION_SLUG") or "").strip()
    label = (os.environ.get("CLAUDE_SUBSCRIPTION_LABEL") or "").strip()
    if not _SLUG_OK.match(slug or "x"):        # a malformed slug is not a slug
        slug = ""
    return {"slug": slug, "label": label or (slug or "default")}


def registry():
    """The launchable accounts for the new-session picker: one entry per
    switcher accounts.tsv row. The plain-`claude` default is deliberately NOT
    listed — it resolves to whichever account is interactively logged in, i.e.
    a duplicate of one of these, so surfacing it is confusing (an unlabeled
    session that's really c1 or c2). `alias` is the shell command word that
    launches the account (the slug, which IS the user's c1/c2 zsh alias),
    validated as a clean bareword so it is safe in the launch shell string.
    Unreadable/absent TSV ⇒ [] (a machine with no switcher only launches plain
    claude, via the empty-account fallback in alias_for)."""
    out, seen = [], set()
    try:
        with open(ACCOUNTS_TSV, encoding="utf-8") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                slug = cols[0].strip() if cols else ""
                if slug and slug not in seen and _SLUG_OK.match(slug):
                    seen.add(slug)
                    label = cols[1].strip() if len(cols) > 1 else slug
                    out.append({"slug": slug, "label": label, "alias": slug})
    except OSError:
        pass
    return out


def config_dir_for(slug):
    """The switcher's per-account config dir (configs/<slug> — what that
    account's sessions see as $CLAUDE_CONFIG_DIR), or None when the slug is
    empty/unknown/dirless — the caller then falls back to its own ambient
    config dir (model.config_dir). Lets an out-of-process reader (the
    dashboard) resolve ANOTHER session's user-level settings: each account
    has its own settings.json, so reading the caller's would cross accounts."""
    if not slug or not _SLUG_OK.match(slug):
        return None
    d = os.path.join(CONFIGS_DIR, slug)
    return d if os.path.isdir(d) else None


def alias_for(slug):
    """The validated launch command word for a chosen account slug, or None
    when the slug is unknown (the caller then 400s). Empty / 'claude' ⇒ the
    default `claude`. The return is always a registry-vetted bareword — never
    caller-supplied text flowing into the launch shell string."""
    if not slug or slug == DEFAULT_ALIAS:
        return DEFAULT_ALIAS
    for a in registry():
        if a["slug"] == slug:
            return a["alias"]
    return None


LAUNCH_SHELLS = ("zsh", "bash")    # login shells the "$@" wrapper is valid for


def launch_argv(words, cmd="claude"):
    """The argv that launches a session in a fresh terminal tab: `cmd` (the
    account's launch word — `claude` for the default, or a switcher alias like
    `c1`/`c2`) through the user's INTERACTIVE LOGIN shell. kitty execs launch
    argv with kitty's OWN env — a GUI kitty has no user PATH (so a bare
    ["claude"] dies command-not-found and the tab closes instantly, while
    `kitten @ launch` still exits 0) and no shell aliases. `$SHELL -lic`
    reproduces exactly what typing `cmd` in a fresh tab does: profile PATH, rc
    aliases (c1/c2 ARE zsh aliases). `cmd` is placed in the FIXED command
    string, so it MUST be a registry-vetted bareword (alias_for) — never raw
    client text; the prompt/flags ride as positional args via "$@" (after the
    $0 placeholder), never interpolated. Shared by the dashboard's web launch
    (plugins.launch_argv) and the rate-limit migration (relimit.py)."""
    sh = os.environ.get("SHELL") or "/bin/zsh"
    if os.path.basename(sh) not in LAUNCH_SHELLS:
        sh = "/bin/zsh"
    return [sh, "-lic", '%s "$@"' % cmd, cmd, *words]


TARGET_MAX_PCT = 90   # a candidate at/above this effective 5h usage is no
                      # refuge — migrating there would hit the wall again


def pick_target(cur_slug, now=None, cache=None, ceiling=TARGET_MAX_PCT,
                model_scoped_ok=False):
    """The migration target for a session leaving its current account
    (plugins/claude_code/relimit.py): the OTHER registry account with the
    lowest effective 5h usage (core.sessionapi.effective_five_hour over the
    freshest per-account snapshots), skipping any account whose active limit-hit
    stamp bars it (core.sessionapi.limit_hit_blocks). Returns {"slug", "alias",
    "eff"} or None when no account qualifies — then the caller must NOT migrate
    (ping-ponging between two exhausted accounts helps nobody). Two knobs relax
    the bar for a MANUAL migrate (an explicit click outranks the automatic
    refuge rules): ceiling=None drops the % headroom bar, and model_scoped_ok
    lets a MODEL-scoped stamp (e.g. Fable-only) through — the account's other
    models are still a refuge, and the resumed session opens at the prompt so
    the user picks the model. An ACCOUNT-WIDE stamp still disqualifies either
    way — a fully blocked account is useless however deliberate the click."""
    per = API.account_usage(cache=cache)
    best = None
    for a in registry():
        if a["slug"] == cur_slug:
            continue
        ent = per.get(a["slug"]) or {}
        if API.limit_hit_blocks(ent.get("limit_hit"), now, model_scoped_ok):
            continue
        eff = API.effective_five_hour(ent.get("usage"), now)
        if best is None or eff < best["eff"]:
            best = {"slug": a["slug"], "alias": a["alias"], "eff": eff}
    if best is None or (ceiling is not None and best["eff"] >= ceiling):
        return None
    return best
