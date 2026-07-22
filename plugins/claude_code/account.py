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
from plugins.claude_code import model as M

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


def _hit_brief(hit):
    """A compact limit-hit stamp for the pick trace (docs/relimit.md *Audit
    trail*): scope + reset, enough to see WHY a rung was barred without carrying
    the whole message. None when no stamp."""
    if not hit:
        return None
    return {"model": hit.get("model"), "slug": hit.get("slug"),
            "resets_at": hit.get("resets_at")}


def _rank(per, model, cur_slug, skip_cur, now, ceiling, trace=None):
    """The best-headroom (lowest effective 5h) registry account that can run
    `model` (a family word), or None. Skips the current account when `skip_cur`
    (the same-model rung — you never migrate a model to the account that just
    ran out of it), any account an active limit-hit bars for `model`
    (core.sessionapi.model_available), and any account at/above the % ceiling.
    When `trace` is a list, append one record per account it considered (rung,
    slug, effective 5h, limit-hit scope, and the reject reason or None) — the
    pick-reasoning the audit records (pick_target's `explain`)."""
    best = None
    for a in registry():
        slug = a["slug"]
        ent = per.get(slug) or {}
        hit = ent.get("limit_hit")
        eff = API.effective_five_hour(ent.get("usage"), now)
        if skip_cur and slug == cur_slug:
            reject = "current account (just ran out of this model)"
        elif not API.model_available(hit, model, now):
            reject = "limit-hit bars this rung"
        elif ceiling is not None and eff >= ceiling:
            reject = "over %d%% 5h ceiling" % ceiling
        else:
            reject = None
        if trace is not None:
            trace.append({"rung": model, "slug": slug, "eff5h": eff,
                          "limit_hit": _hit_brief(hit), "reject": reject})
        if reject is not None:
            continue
        if best is None or eff < best["eff"]:
            best = {"slug": slug, "alias": a["alias"],
                    "model": model, "eff": eff}
    return best


def pick_target(cur_slug, cur_model, now=None, cache=None, ceiling=TARGET_MAX_PCT,
                explain=None):
    """The migration target for a rate-limited session, walking the model
    downgrade ladder (docs/relimit.md *Model-downgrade ladder*). `cur_model` is
    the limited/current model family (model.family vocabulary). For each rung of
    model.ladder_from(cur_model) — fable→opus→sonnet, best model first — pick the
    best-headroom account that can still run that model; the FIRST rung with any
    candidate wins. This keeps the model as high as possible (same model on
    another account before any downgrade) AND never skips a rung (Opus is fully
    explored across all accounts before Sonnet). At the top rung the current
    account is skipped (it just ran out of `cur_model`); at downgrade rungs it
    rejoins as a normal candidate (its Fable cap doesn't bar Opus). Returns
    {"slug", "alias", "model", "eff"} or None when nothing qualifies — then the
    caller must NOT migrate. `model` is the downgrade rung to pass to `--model`,
    or "" when the chosen model IS the current one (a same-model migration, or
    the keep-model fallback below) — so a caller forwards `model` verbatim and
    passes `--model` exactly when it is non-empty (a same-model resume stays bare,
    the proven path). `ceiling` is the % headroom bar (TARGET_MAX_PCT for the
    automatic path, None for a manual ⇆ click, which outranks the refuge rule).

    When `cur_model` is unknown / not a ladder rung (an account-wide limit whose
    transcript model couldn't be read, or a haiku session), the ladder is empty:
    fall back to today's behavior — keep the current model, migrate to the
    least-used OTHER account with NO active limit-hit at all (we can't prove the
    kept model survives a model-scoped stamp, so any active stamp disqualifies),
    and return `model=""` so the caller resumes bare (no `--model`).

    When `explain` is a dict, it is filled with the FULL decision trace — the
    resolved `cur_model`, which `branch` ran (`ladder` vs `fallback` — the tell
    that `cur_model` was unknown, so the coarse "any active limit-hit
    disqualifies" rule applied), the `ceiling`, the per-account `candidates`
    (rung / eff5h / limit-hit scope / reject reason), and the `chosen` target —
    so a refusal is reconstructible from the audit DB, never re-derived by hand
    (docs/relimit.md *Audit trail*). Purely additive: the return is unchanged."""
    per = API.account_usage(cache=cache)
    # Accept EITHER a family word (relimit's limit_model/session_model already
    # collapse to one) OR a raw model id (the dashboard passes what it reads off
    # the transcript) — family() is idempotent on a family word.
    cur_model = M.family(cur_model)
    ladder = M.ladder_from(cur_model)
    trace = [] if explain is not None else None
    if explain is not None:
        explain.update({"cur_slug": cur_slug, "cur_model": cur_model,
                        "ladder": list(ladder),
                        "branch": "ladder" if ladder else "fallback",
                        "ceiling": ceiling, "candidates": trace, "chosen": None})
    if ladder:
        for i, model in enumerate(ladder):
            best = _rank(per, model, cur_slug, i == 0, now, ceiling, trace)
            if best is not None:
                if best["model"] == cur_model:   # top rung — no downgrade
                    best["model"] = ""           # resume bare (proven path)
                if explain is not None:
                    explain["chosen"] = {"slug": best["slug"], "eff5h": best["eff"],
                                         "model": best["model"] or cur_model}
                return best
        return None
    best = None
    for a in registry():
        slug = a["slug"]
        ent = per.get(slug) or {}
        hit = ent.get("limit_hit")
        eff = API.effective_five_hour(ent.get("usage"), now)
        if slug == cur_slug:
            reject = "current account"
        elif API.limit_hit_active(hit, now):
            # The fallback branch is COARSER than the ladder on purpose: with an
            # unknown cur_model it can't prove the kept model survives a
            # model-scoped stamp, so ANY active limit-hit disqualifies (even one
            # scoped to a DIFFERENT model). The trace records this so the
            # over-refusal is visible (docs/relimit.md).
            reject = "any active limit-hit (fallback branch: cur_model unknown)"
        elif ceiling is not None and eff >= ceiling:
            reject = "over %d%% 5h ceiling" % ceiling
        else:
            reject = None
        if trace is not None:
            trace.append({"rung": "keep(%s)" % (cur_model or "?"), "slug": slug,
                          "eff5h": eff, "limit_hit": _hit_brief(hit),
                          "reject": reject})
        if reject is not None:
            continue
        if best is None or eff < best["eff"]:
            best = {"slug": slug, "alias": a["alias"], "model": "", "eff": eff}
    if explain is not None and best is not None:
        explain["chosen"] = {"slug": best["slug"], "eff5h": best["eff"],
                             "model": cur_model or ""}
    return best
