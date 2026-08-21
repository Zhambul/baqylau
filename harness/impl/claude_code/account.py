"""Claude Code subscription accounts and account selection."""

from __future__ import annotations

import os
import re
from typing import TypedDict

from domain.ids import AccountId

ACCOUNTS_FILE = os.path.expanduser("~/.config/claude-subscriptions/accounts.tsv")
ACCOUNT_CONFIG_DIRECTORY = os.path.expanduser("~/.config/claude-subscriptions/configs")
DEFAULT_COMMAND = "claude"
SUPPORTED_SHELLS = ("zsh", "bash")
VALID_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9._-]+$")

# The two variables the account switcher exports into a launched CLI's
# environment. Nothing in the daemon reads them — a CLIENT observes its own
# environment and forwards both values raw (client/_wire.py) — but they are named
# here, beside the validation they feed, so the client's copy can be pinned to
# this one (tests/test_canonical_clients.py).
SLUG_VARIABLE = "CLAUDE_SUBSCRIPTION_SLUG"
LABEL_VARIABLE = "CLAUDE_SUBSCRIPTION_LABEL"


def normalize(account_id: AccountId | None, display_name: str | None) -> tuple[AccountId | None, str]:
    """One account as a client reported it, made trustworthy: (id, label).

    A client forwards what its environment said and validates nothing — the
    values reach us as external input. The id has to look like an id or it is no
    id at all, and the label falls back to the id and then to "default", so
    every account row has a name to render.
    """
    # str(): both values arrive as external input — a header, or a JSON field a
    # status line wrote — so neither is known to be a string yet.
    account_id_text = str(account_id or "").strip()
    display_name = str(display_name or "").strip()
    if not VALID_ACCOUNT_ID.fullmatch(account_id_text or "x"):
        account_id_text = ""
    return (AccountId(account_id_text) if account_id_text else None), display_name or account_id_text or "default"


class AccountRecord(TypedDict):
    slug: str
    label: str
    alias: str


def registry() -> list[AccountRecord]:
    accounts: list[AccountRecord] = []
    account_ids = set()
    try:
        with open(ACCOUNTS_FILE, encoding="utf-8") as source:
            for line in source:
                columns = line.rstrip("\n").split("\t")
                account_id = columns[0].strip() if columns else ""
                if not account_id or account_id in account_ids or not VALID_ACCOUNT_ID.fullmatch(account_id):
                    continue
                account_ids.add(account_id)
                display_name = columns[1].strip() if len(columns) > 1 else account_id
                accounts.append({"slug": account_id, "label": display_name, "alias": account_id})
    except OSError:
        return []
    return accounts


def config_directory(account_id: AccountId | None) -> str | None:
    """The `CLAUDE_CONFIG_DIR` one account runs under, or None for the default.

    The switcher gives each subscription its own configuration directory, and
    Claude Code keys its stored credentials by that path — so this is also what
    decides WHICH account answers when the daemon asks the harness a question
    (`usage/live.py`).
    """
    if not account_id or not VALID_ACCOUNT_ID.fullmatch(account_id):
        return None
    directory = os.path.join(ACCOUNT_CONFIG_DIRECTORY, account_id)
    return directory if os.path.isdir(directory) else None


def alias_for(account_id: AccountId) -> str | None:
    if not account_id or account_id == DEFAULT_COMMAND:
        return DEFAULT_COMMAND
    for account_record in registry():
        if account_record["slug"] == account_id:
            return account_record["alias"]
    return None
