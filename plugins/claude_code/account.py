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

# The switcher's registry: TSV rows `slug<TAB>label<TAB>keychain-service`.
ACCOUNTS_TSV = os.path.expanduser(
    "~/.config/claude-subscriptions/accounts.tsv")

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
    """The launchable accounts for the new-session picker: the plain default
    first, then each accounts.tsv row. `alias` is the shell command word that
    launches it (`claude` for default; the slug — which IS the user's c1/c2
    zsh alias — otherwise), validated as a clean bareword so it is safe to
    place in the launch shell string. Unreadable/absent TSV ⇒ just the
    default (a machine with no switcher still launches plain claude)."""
    out = [{"slug": "", "label": "default", "alias": DEFAULT_ALIAS}]
    seen = {""}
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
