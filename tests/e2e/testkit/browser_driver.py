# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide browser driver."""

from __future__ import annotations

from typing import Literal

from tests.e2e.testkit import (
    browser_client_dependencies as client_dependencies,
    browser_model_dependencies as model_dependencies,
    browser_runtime_dependencies as runtime_dependencies,
    browser_standard_dependencies as standard_dependencies,
)

# Keep browser dependencies separate from action helpers.
# isort: split

from tests.e2e.testkit import (
    browser_capabilities,
    browser_feed,
    browser_session_forms,
    browser_usage,
    browser_values,
    references as browser_references,
)

browser_expectation = model_dependencies.expect
regular_expressions = standard_dependencies.re

SESSION_FRAGMENT = regular_expressions.compile("^/s/([^/?#]+)$")
SSE_DOCUMENT_MARKER = "baqylau-e2e-sse-marker"
BUTTON_ROLE: Literal["button"] = "button"
NEW_SESSION_BUTTON_LABEL = "+ session"
NEW_SESSION_PROMPT_PATTERN = regular_expressions.compile(r"what should .* start on\?")
ACCOUNT_NAME = "account"
NO_KNOWN_SESSIONS: frozenset[str] = frozenset()


class _BrowserConnectionDriver(browser_feed.BrowserWorkspaceDriver):
    """Provide browser connection and session-card actions."""

    def assert_connected(self) -> None:
        browser_expectation(self._page.locator("#conn")).to_have_attribute(
            "data-on", "1", timeout=self._milliseconds(self._wait_policy.feed),
        )

    def mark_document_for_connection_recovery(self) -> None:
        self.assert_connected()
        self._page.evaluate("value => { globalThis.__baqylauE2eSseMarker = value; }", SSE_DOCUMENT_MARKER)
        self._network_drop_expected = True

    def assert_reconnected_without_reload(self) -> None:
        self.assert_connected()
        found = self._page.evaluate("() => globalThis.__baqylauE2eSseMarker")
        assert found == SSE_DOCUMENT_MARKER, "the browser reloaded while the event stream reconnected"
        self._network_drop_expected = False

    def assert_session_card_visible(self, session: runtime_dependencies.SessionRef) -> None:
        browser_expectation(self._session_card(session)).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_session_card_absent(self, session: runtime_dependencies.SessionRef) -> None:
        browser_expectation(self._session_card(session)).to_have_count(
            0, timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_shared_project_group(
        self,
        sessions: tuple[runtime_dependencies.SessionRef, ...],
        project_directory: str,
        worktree_directory: str,
    ) -> None:
        headers = self._page.locator(".dirhead")
        project = headers.filter(has_text=project_directory)
        browser_expectation(project).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        browser_expectation(project.locator(".dircount")).to_have_text(f"{len(sessions)} sessions")
        browser_expectation(headers.filter(has_text=worktree_directory)).to_have_count(0)
        for session in sessions:
            self.assert_session_card_visible(session)


class _BrowserUsageAssertionDriver(_BrowserConnectionDriver):
    """Provide browser usage and cleanup assertions."""

    def assert_default_model_usage_window(self, harness: str, model: str) -> None:
        row, model_window = runtime_dependencies.wait_for(
            f"harness {harness!r} to publish its {model!r} model usage window",
            standard_dependencies.partial(self._current_model_usage_window, harness, model),
            timeout=self._wait_policy.feed,
        )
        self._page.reload()
        browser_expectation(self._page.get_by_role(BUTTON_ROLE, name=NEW_SESSION_BUTTON_LABEL)).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        browser_session_forms.assert_rendered_usage_window(self._page, row, model_window)

    def assert_clean(self) -> None:
        assert not self._browser_failures, f"browser reported failures: {self._browser_failures}"


class _BrowserSessionSupport(_BrowserUsageAssertionDriver):
    """Provide private session-form support."""

    def _launch_new_session(
        self, spec: browser_references.SessionSpec, prompt: str, workspace: str,
    ) -> runtime_dependencies.SessionRef:
        """Launch a new session through the browser.

        Returns:
            The reference for the new visible session.

        """
        known = frozenset(
            session_summary.session.session_id for session_summary in self._client.sessions.list().sessions
        )
        dialog = self._open_new_session()
        self._configure_fresh_session(dialog, spec, workspace)
        dialog.get_by_placeholder(NEW_SESSION_PROMPT_PATTERN).fill(prompt)
        dialog.get_by_role(BUTTON_ROLE, name="launch", exact=True).click()
        return self._wait_for_visible_session(known)

    def _submit_plan_action(
        self,
        reference: browser_references.PlanRef,
        action: browser_capabilities.BrowserPlanAction,
        feedback: str | None,
        card: client_dependencies.Locator,
    ) -> None:
        """Submit one requested action from a visible plan card.

        Raises:
            AssertionError: If a feedback action has no feedback text.

        """
        if action == browser_capabilities.BrowserPlanAction.dismiss:
            card.get_by_role(BUTTON_ROLE, name="chat about this", exact=True).click()
            return
        outcome = self._client.sessions.read_plan_choices(reference.session, reference.attention_id).outcome
        msg = "plan did not return browser choices"
        assert isinstance(outcome, model_dependencies.PlanChoicesResultResponse), msg
        if action == browser_capabilities.BrowserPlanAction.feedback:
            if feedback is None or not feedback.strip():
                msg = "plan feedback is empty"
                raise AssertionError(msg)
            card.get_by_placeholder("feedback for requested changes…").fill(feedback)
        choice_label = browser_usage.plan_choice_label(outcome, action)
        card.get_by_role(BUTTON_ROLE, name=choice_label, exact=True).click()

    def _open_new_session(self) -> client_dependencies.Locator:
        self.open_session_list()
        self._page.get_by_role(BUTTON_ROLE, name=NEW_SESSION_BUTTON_LABEL).click()
        dialog = self._page.get_by_role("dialog", name="new session")
        browser_expectation(dialog).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        return dialog

    def _new_session_dialog(self) -> client_dependencies.Locator:
        dialog = self._page.get_by_role("dialog", name="new session")
        browser_expectation(dialog).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        return dialog

    def _configure_fresh_session(
        self,
        dialog: client_dependencies.Locator,
        spec: browser_references.SessionSpec,
        workspace: str,
    ) -> None:
        directory = dialog.get_by_label("directory")
        directory.fill(workspace)
        directory.press("Tab")
        self._select_harness(dialog, spec)
        self._select_account(dialog, spec)
        self._select_model_and_effort(dialog, spec, workspace)

    def _select_harness(self, dialog: client_dependencies.Locator, spec: browser_references.SessionSpec) -> None:
        """Select the requested harness from the browser catalog.

        Raises:
            AssertionError: If the catalog does not have exactly one matching harness.

        """
        available_harnesses = self._client.harnesses.list()
        harnesses = tuple(harness for harness in available_harnesses if harness.name == spec.harness)
        if len(harnesses) != 1:
            msg = f"harness {spec.harness!r} has {len(harnesses)} catalog rows"
            raise AssertionError(msg)
        self._select(dialog, "harness", harnesses[0].display_name)

    def _select_account(self, dialog: client_dependencies.Locator, spec: browser_references.SessionSpec) -> None:
        """Select the requested account when the session specifies one.

        Raises:
            AssertionError: If the usage rows do not have exactly one matching account.

        """
        if spec.account_id is not None:
            accounts = [
                row
                for row in self._client.usage.state().usage_rows
                if row.harness == spec.harness and row.account_id == spec.account_id and row.switchable
            ]
            if len(accounts) != 1:
                msg = f"account {spec.account_id!r} has {len(accounts)} usage rows"
                raise AssertionError(msg)
            self._select(dialog, ACCOUNT_NAME, accounts[0].display_name)


class _BrowserPageSupport(_BrowserSessionSupport):
    """Provide private browser-page support."""

    def _select_model_and_effort(
        self,
        dialog: client_dependencies.Locator,
        spec: browser_references.SessionSpec,
        workspace: str,
    ) -> None:
        """Select the requested model and effort from its harness catalog.

        Raises:
            AssertionError: If the catalog does not have exactly one matching model or effort.

        """
        catalog = self._client.harnesses.catalog(spec.harness, workspace=workspace)
        models = tuple(model for model in catalog.models if model.model_id == spec.model)
        if len(models) != 1:
            msg = f"model {spec.model!r} has {len(models)} catalog rows"
            raise AssertionError(msg)
        model = models[0]
        self._select(dialog, "model", model.display_name)
        efforts = tuple(effort for effort in model.efforts if effort.effort == spec.effort)
        if len(efforts) != 1:
            msg = f"effort {spec.effort!r} has {len(efforts)} catalog rows"
            raise AssertionError(msg)
        self._select(dialog, "effort", efforts[0].display_name)

    def _select(self, dialog: client_dependencies.Locator, label: str, option: str) -> None:
        button = dialog.get_by_role(BUTTON_ROLE, name=label, exact=True)
        browser_expectation(button).to_be_enabled(timeout=self._milliseconds(self._wait_policy.feed))
        button.click()
        listbox = dialog.get_by_role("listbox", name=label, exact=True)
        listbox.get_by_role("option", name=option, exact=True).click()

    def _wait_for_visible_session(self, known: frozenset[str] = NO_KNOWN_SESSIONS) -> runtime_dependencies.SessionRef:
        self._page.wait_for_function(
            r"""
            known => {
                const match = /^#\/s\/([^/?#]+)$/.exec(location.hash);
                return match !== null && !known.includes(match[1]);
            }
            """,
            arg=list(known),
            timeout=self._milliseconds(self._wait_policy.session_announcement),
        )
        fragment = client_dependencies.urlsplit(self._page.url).fragment
        match = SESSION_FRAGMENT.fullmatch(fragment)
        if match is None:
            page_url = self._page.url
            msg = f"dashboard did not open a session URL: {page_url}"
            raise AssertionError(msg)
        return runtime_dependencies.SessionRef(match.group(1))

    def _wait_for_session_url(self, session: runtime_dependencies.SessionRef) -> None:
        self._page.wait_for_url(
            regular_expressions.compile(f".*/#/s/{regular_expressions.escape(session.session_id)}$"),
            timeout=self._milliseconds(self._wait_policy.session_announcement),
        )

    def _record_console_error(self, console_message: client_dependencies.ConsoleMessage) -> None:
        expected_network_failure = self._network_drop_expected and any(

                marker in console_message.text
                for marker in (
                    "ERR_CONNECTION_REFUSED",
                    "ERR_EMPTY_RESPONSE",
                    "ERR_INCOMPLETE_CHUNKED_ENCODING",
                    "ERR_INTERNET_DISCONNECTED",
                    "ERR_NETWORK_CHANGED",
                )

        )
        if console_message.type == "error" and (not expected_network_failure):
            self._browser_failures.append(console_message.text)

    def _record_request(self, request: client_dependencies.Request) -> None:
        self._request_paths.append(client_dependencies.urlsplit(request.url).path)

    def _resume_option(self, source: runtime_dependencies.SessionRef) -> client_dependencies.Locator:
        return (
            self
            ._new_session_dialog()
            .get_by_role("listbox", name="sessions to resume")
            .locator(f'[role="option"][data-session-id="{source.session_id}"]')
        )


class _BrowserResolutionSupport(_BrowserPageSupport):
    """Provide private state-resolution support."""

    def _session_card(self, session: runtime_dependencies.SessionRef) -> client_dependencies.Locator:
        return self._page.locator(".scard").filter(has_text=session.session_id)

    def _omit_usage_row(self, harness: str, route: client_dependencies.Route) -> None:
        """Fulfill one application response without the selected usage row."""
        response = route.fetch()
        document = model_dependencies.GlobalApplicationResponse.model_validate(response.json())
        filtered = document.model_copy(
            update={"usage_rows": tuple(row for row in document.usage_rows if row.harness != harness)},
        )
        route.fulfill(response=response, json=filtered.model_dump(mode="json"))

    def _current_model_usage_window(
        self, harness: str, model: str,
    ) -> tuple[model_dependencies.UsageRowResponse, model_dependencies.UsageWindowResponse] | None:
        """Return the current default model usage window.

        Returns:
            The current default model usage window.

        """
        return browser_session_forms.default_model_usage_window(self._client.usage.state().usage_rows, harness, model)

    def _workspace_group(self) -> client_dependencies.Locator:
        return self._page.locator(".dirhead").filter(has_text=self._workspace)

    def _wait_for_question_resolution(self, reference: browser_references.QuestionRef) -> None:
        self._client.sessions.watch(reference.session).wait(
            "browser question action to resolve",
            lambda snapshot: None if browser_values.question(snapshot, reference)[0].pending else True,
            timeout=self._wait_policy.feed,
        )

    def _wait_for_plan_resolution(self, reference: browser_references.PlanRef, state: str) -> None:
        self._client.sessions.watch(reference.session).wait(
            f"browser plan action to record state {state!r}",
            lambda snapshot: True if browser_values.plan(snapshot, reference).state == state else None,
            timeout=self._wait_policy.feed,
        )

    def _milliseconds(self, seconds: float) -> float:
        """Return a browser timeout in milliseconds.

        Returns:
            A browser timeout in milliseconds.

        """
        return seconds * 1000


class BrowserSessionDriver(_BrowserResolutionSupport):
    """Use visible controls and return typed browser references."""
