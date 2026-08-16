"""Claude Code subscription accounts and account selection."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from harness.impl.claude_code.usage import state

ACCOUNTS_FILE = os.path.expanduser("~/.config/claude-subscriptions/accounts.tsv")
ACCOUNT_CONFIG_DIRECTORY = os.path.expanduser("~/.config/claude-subscriptions/configs")
DEFAULT_COMMAND = "claude"
SUPPORTED_SHELLS = ("zsh", "bash")
VALID_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def current(environment: Mapping[str, str]) -> dict:
    """The account the given environment selects — the caller says whose
    environment (a hook ships its own; the daemon's is meaningless here)."""
    account_id = (environment.get("CLAUDE_SUBSCRIPTION_SLUG") or "").strip()
    display_name = (environment.get("CLAUDE_SUBSCRIPTION_LABEL") or "").strip()
    if not VALID_ACCOUNT_ID.fullmatch(account_id or "x"):
        account_id = ""
    return {"slug": account_id, "label": display_name or account_id or "default"}


def registry() -> list[dict]:
    accounts = []
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


def alias_for(account_id: str) -> str | None:
    if not account_id or account_id == DEFAULT_COMMAND:
        return DEFAULT_COMMAND
    for account_record in registry():
        if account_record["slug"] == account_id:
            return account_record["alias"]
    return None


def migration_target(current_account_id: str) -> dict | None:
    """Choose the least-used launchable account other than the current one."""

    snapshots = state.latest_by_account()
    candidates = []
    for account_record in registry():
        if account_record["slug"] == current_account_id:
            continue
        snapshot = snapshots.get(account_record["slug"])
        windows = snapshot["windows"] if snapshot is not None else {}
        used_percent = windows.get("five_hour")
        candidates.append((
            float(used_percent) if isinstance(used_percent, (int, float)) else 0.0,
            account_record,
        ))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]
