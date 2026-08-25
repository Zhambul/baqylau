"""Real account selection actions and checks."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pytest_bdd import given, parsers, then

from api.common.models.values.usage_row import UsageRowResponse
from sdk.client import BaqylauClient, WaitTimeout, wait_for
from sdk.state import SessionSnapshot
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    AccountSelectionRef,
    AccountSelections,
    SessionSpecs,
    Sessions,
)


@given(parsers.parse('session configuration "{session_name}" selects an available account "{account_name}"'))
def select_available_account(
    client: BaqylauClient,
    session_specs: SessionSpecs,
    account_selections: AccountSelections,
    wait_policy: WaitPolicy,
    session_name: str,
    account_name: str,
) -> None:
    _select_available_account(
        client,
        session_specs,
        account_selections,
        wait_policy,
        session_name,
        account_name,
    )


@given(parsers.parse('session configuration "{session_name}" uses {mode} account'))
def configure_account_mode(
    client: BaqylauClient,
    session_specs: SessionSpecs,
    account_selections: AccountSelections,
    wait_policy: WaitPolicy,
    session_name: str,
    mode: str,
) -> None:
    if mode == "no":
        return
    if mode == "an available":
        _select_available_account(
            client,
            session_specs,
            account_selections,
            wait_policy,
            session_name,
            f"{session_name} account",
        )
        return
    _select_account_by_id(
        client,
        session_specs,
        account_selections,
        wait_policy,
        session_name,
        mode,
    )


def _select_account_by_id(
    client: BaqylauClient,
    session_specs: SessionSpecs,
    account_selections: AccountSelections,
    wait_policy: WaitPolicy,
    session_name: str,
    account_id: str,
) -> None:
    spec = session_specs.get(session_name)

    def configured() -> UsageRowResponse | None:
        choices = [
            row
            for row in client.usage.state().usage_rows
            if row.harness == spec.harness
            and row.account_id == account_id
            and row.switchable
        ]
        if len(choices) > 1:
            raise AssertionError(
                f"account {account_id!r} has {len(choices)} usage rows"
            )
        return choices[0] if choices else None

    selected = wait_for(
        f"harness {spec.harness!r} to publish account {account_id!r}",
        configured,
        timeout=wait_policy.feed,
    )
    account_selections.bind(
        f"{session_name} account",
        AccountSelectionRef(account_id, selected.display_name),
    )
    session_specs.replace(
        session_name,
        replace(spec, account_id=account_id),
    )


def _select_available_account(
    client: BaqylauClient,
    session_specs: SessionSpecs,
    account_selections: AccountSelections,
    wait_policy: WaitPolicy,
    session_name: str,
    account_name: str,
) -> None:
    spec = session_specs.get(session_name)

    def published() -> tuple[UsageRowResponse, ...] | None:
        rows = _switchable_rows(client, spec.harness)
        return rows or None

    rows = wait_for(
        f"harness {spec.harness!r} to publish switchable accounts",
        published,
        timeout=wait_policy.feed,
    )

    def measured() -> tuple[UsageRowResponse, ...] | None:
        rows = _switchable_rows(client, spec.harness)
        if not any(row.windows or row.limit or row.authentication_error for row in rows):
            return None
        return rows

    try:
        rows = wait_for(
            f"harness {spec.harness!r} to publish account capacity",
            measured,
            timeout=min(wait_policy.feed, 15.0),
        )
    except WaitTimeout:
        rows = _switchable_rows(client, spec.harness)

    choices = tuple(row for row in rows if _can_launch(row, spec.model))
    if not choices:
        pytest.skip(
            f"harness {spec.harness!r} has no available account: "
            + "; ".join(_capacity_summary(row) for row in rows)
        )
    selected = max(
        choices,
        key=lambda row: (
            row.scheduling_score if row.scheduling_score is not None else -1,
            row.default_for_launch,
        ),
    )
    selected_account_id = selected.account_id
    if selected_account_id is None:
        raise AssertionError("the selected switchable account has no identity")
    account_selections.bind(
        account_name,
        AccountSelectionRef(selected_account_id, selected.display_name),
    )
    session_specs.replace(
        session_name,
        replace(spec, account_id=selected_account_id),
    )


def _switchable_rows(
    client: BaqylauClient,
    harness: str,
) -> tuple[UsageRowResponse, ...]:
    return tuple(
        row
        for row in client.usage.state().usage_rows
        if row.harness == harness
        and row.switchable
        and row.account_id is not None
    )


def _can_launch(row: UsageRowResponse, model: str | None) -> bool:
    if row.authentication_error or not row.scheduling_allowed or row.limit:
        return False
    relevant = tuple(
        window
        for window in row.windows
        if window.model_id is None or model is None or window.model_id == model
    )
    return bool(relevant) and all(window.used_percent < 100 for window in relevant)


def _capacity_summary(row: UsageRowResponse) -> str:
    identity = row.account_id or row.display_name
    if row.authentication_error:
        return f"{identity} authentication failed"
    if row.limit:
        return f"{identity} is blocked"
    if not row.windows:
        return f"{identity} usage is unavailable"
    windows = ", ".join(
        f"{window.key}={window.used_percent}%" for window in row.windows
    )
    return f"{identity} {windows}"


@then(parsers.parse('session "{session_name}" uses account "{account_name}"'))
def session_uses_account(
    client: BaqylauClient,
    sessions: Sessions,
    account_selections: AccountSelections,
    wait_policy: WaitPolicy,
    session_name: str,
    account_name: str,
) -> None:
    session = sessions.get(session_name)
    expected = account_selections.get(account_name)

    def matches(snapshot: SessionSnapshot) -> SessionSnapshot | None:
        account = snapshot.data.session.account
        if account is None:
            return None
        if account.account_id != expected.account_id or account.display_name != expected.display_name:
            raise AssertionError(
                f"session account is {account.account_id!r} / {account.display_name!r}; "
                f"expected {expected.account_id!r} / {expected.display_name!r}"
            )
        return snapshot

    client.sessions.watch(session).wait(
        f"session {session_name!r} to report account {account_name!r}",
        matches,
        timeout=wait_policy.feed,
    )
