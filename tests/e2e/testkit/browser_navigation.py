# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide browser navigation."""

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
    browser_assertions,
    browser_session_forms,
    browser_terminal_dependencies as terminal_dependencies,
    browser_usage,
    browser_values,
    references as browser_references,
)

browser_expectation = model_dependencies.expect
regular_expressions = standard_dependencies.re

BUTTON_ROLE: Literal["button"] = "button"
NEW_SESSION_BUTTON_LABEL = "+ session"
NEW_SESSION_PROMPT_PATTERN = regular_expressions.compile(r"what should .* start on\?")
MESSAGE_COMPOSER_LABEL = "message composer"
ACCOUNT_NAME = "account"
DOM_POLL_MILLISECONDS = 25
FORM_NOT_IN_RESUME_MODE = "browser session form is not in resume mode"
MISSING_DOCUMENT_MARKER = "the browser document marker is missing"
APPLICATION_READ_NOT_INTERCEPTED = "the initial application read was not intercepted"
FORM_ALREADY_IN_RESUME_MODE = "browser session form is already in resume mode"


class _BrowserPrivateCapabilities(
    browser_usage.BrowserTimingCapability,
    browser_usage.BrowserFormCapability,
    browser_session_forms.BrowserPageCapability,
):
    """Define private browser methods that later layers provide."""


class _BrowserDriverCapabilities(_BrowserPrivateCapabilities, browser_session_forms.BrowserOperationCapability):
    """Define browser methods that later capability layers provide."""


class _BrowserDriverCore(_BrowserDriverCapabilities):
    """Use visible controls and return the same typed references as API journeys."""

    def __init__(
        self,
        page: client_dependencies.Page,
        client: runtime_dependencies.BaqylauClient,
        endpoint: str,
        workspace: str,
        wait_policy: terminal_dependencies.WaitPolicy,
    ) -> None:
        self._page = page
        self._client = client
        self._endpoint = f"{endpoint.rstrip('/')}/"
        self._workspace = workspace
        self._wait_policy = wait_policy
        self._resume = terminal_dependencies.SessionResumeSupport(client, wait_policy)
        self._browser_failures: list[str] = []
        self._request_paths: list[str] = []
        self._network_drop_expected = False
        self._usage_document_marker: str | None = None
        page.on("console", self._record_console_error)
        page.on("pageerror", lambda error: self._browser_failures.append(str(error)))
        page.on("request", self._record_request)


class _BrowserUsageDriver(_BrowserDriverCore):
    """Provide application usage browser actions."""

    def open_session_list(self) -> None:
        response = self._page.goto(self._endpoint)
        if response is None or not response.ok:
            status = None if response is None else response.status
            msg = f"dashboard returned HTTP {status}"
            raise AssertionError(msg)
        browser_expectation(self._page.get_by_role(BUTTON_ROLE, name=NEW_SESSION_BUTTON_LABEL)).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        if self._usage_document_marker == "pending":
            marker = self._page.evaluate("() => globalThis.__baqylauUsageDocumentMarker")
            if not isinstance(marker, str):
                raise AssertionError(MISSING_DOCUMENT_MARKER)
            self._usage_document_marker = marker

    def omit_usage_from_next_application_read(self, harness: str) -> None:
        self._page.add_init_script("globalThis.__baqylauUsageDocumentMarker = crypto.randomUUID()")
        self._usage_document_marker = "pending"
        self._page.route("**/api/application", standard_dependencies.partial(self._omit_usage_row, harness), times=1)

    def assert_usage_row_appears_without_reload(self, harness: str) -> None:
        marker = self._usage_document_marker
        if marker is None or marker == "pending":
            raise AssertionError(APPLICATION_READ_NOT_INTERCEPTED)
        harness_rows = runtime_dependencies.wait_for(
            f"usage collection for harness {harness!r}",
            lambda: browser_values.usage_rows_for_harness(self._client.usage.state().usage_rows, harness) or None,
            timeout=self._wait_policy.feed,
        )
        if len(harness_rows) != 1:
            msg = f"harness {harness!r} has {len(harness_rows)} usage rows"
            raise AssertionError(msg)
        display_name = harness_rows[0].display_name
        name = self._page.locator(".aname").filter(
            has_text=regular_expressions.compile(f"^{regular_expressions.escape(display_name)}$"),
        )
        browser_expectation(name).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        assert self._page.evaluate("() => globalThis.__baqylauUsageDocumentMarker") == marker, (
            "the browser reloaded the document"
        )


class _BrowserSessionFormDriver(_BrowserUsageDriver):
    """Provide session-form browser actions."""

    def start(self, spec: browser_references.SessionSpec, prompt: str) -> browser_assertions.BrowserSessionStart:
        workspace = spec.workspace or self._workspace
        session = self._launch_new_session(spec, prompt, workspace)
        snapshot = self._client.sessions.snapshot(session)
        launched_session = snapshot.session_data.session
        if launched_session.harness != spec.harness:
            msg = f"browser launched {launched_session.harness!r}, not {spec.harness!r}"
            raise AssertionError(msg)
        if launched_session.working_directory != workspace:
            msg = f"browser launched in {launched_session.working_directory!r}, not {workspace!r}"
            raise AssertionError(msg)
        return browser_assertions.BrowserSessionStart(
            session,
            terminal_dependencies.selector_turns.launched_turn(
                self._client.sessions.watch(session), self._wait_policy.feed,
            ),
        )

    def resume(self, source: runtime_dependencies.SessionRef, prompt: str) -> browser_assertions.BrowserSessionResume:
        form = self.open_fresh_session_form(source)
        form = self.switch_session_form_to_resume(form)
        return self.resume_from_session_form(form, prompt)

    def open_fresh_session_form(
        self, source: runtime_dependencies.SessionRef,
    ) -> browser_references.BrowserSessionFormRef:
        request_start_index = len(self._request_paths)
        workspace = self._client.sessions.snapshot(source).session_data.session.working_directory
        dialog = self._open_new_session()
        dialog.get_by_label("directory").fill(workspace)
        dialog.get_by_label("directory").press("Tab")
        browser_expectation(dialog.get_by_text("fresh conversation", exact=True)).to_be_visible()
        return browser_references.BrowserSessionFormRef(source, request_start_index)

    def open_configured_fresh_session_form(
        self, spec: browser_references.SessionSpec,
    ) -> browser_references.BrowserSessionFormRef:
        request_start_index = len(self._request_paths)
        dialog = self._open_new_session()
        self._configure_fresh_session(dialog, spec, spec.workspace or self._workspace)
        return browser_references.BrowserSessionFormRef(None, request_start_index)

    def type_session_form_prompt(self, _form: browser_references.BrowserSessionFormRef, text: str) -> None:
        self._new_session_dialog().get_by_placeholder(NEW_SESSION_PROMPT_PATTERN).fill(text)

    def close_session_form(self, _form: browser_references.BrowserSessionFormRef) -> None:
        self._new_session_dialog().press("Escape")
        browser_expectation(self._page.get_by_role("dialog", name="new session")).to_have_count(0)

    def assert_session_form_prompt(self, _form: browser_references.BrowserSessionFormRef, text: str) -> None:
        browser_expectation(self._new_session_dialog().get_by_placeholder(NEW_SESSION_PROMPT_PATTERN)).to_have_value(
            text,
        )


class _BrowserResumeFormDriver(_BrowserSessionFormDriver):
    """Provide resume-form browser actions."""

    def assert_session_form_has_no_account_selection(self, _form: browser_references.BrowserSessionFormRef) -> None:
        dialog = self._new_session_dialog()
        browser_expectation(dialog.get_by_label(ACCOUNT_NAME, exact=True)).to_have_count(0)
        browser_expectation(dialog.locator(".nslabel", has_text=ACCOUNT_NAME)).to_have_count(0)

    def switch_session_form_to_resume(
        self, form: browser_references.BrowserSessionFormRef,
    ) -> browser_references.BrowserSessionFormRef:
        if form.resume_request_start_index is not None:
            raise AssertionError(FORM_ALREADY_IN_RESUME_MODE)
        request_start_index = len(self._request_paths)
        dialog = self._new_session_dialog()
        dialog.get_by_text("fresh conversation", exact=True).click()
        browser_expectation(dialog.get_by_text("resume a conversation", exact=True)).to_be_visible()
        return browser_references.BrowserSessionFormRef(form.source, form.request_start_index, request_start_index)

    def assert_form_did_not_request_resume_catalog(self, form: browser_references.BrowserSessionFormRef) -> None:
        paths = self._request_paths[form.request_start_index :]
        assert not browser_values.resume_catalog_requests(paths), (
            "a fresh browser session form requested the resume catalog"
        )

    def assert_form_requested_resume_catalog(self, form: browser_references.BrowserSessionFormRef) -> None:
        start = form.resume_request_start_index
        if start is None:
            raise AssertionError(FORM_NOT_IN_RESUME_MODE)
        deadline = standard_dependencies.time.monotonic() + self._wait_policy.feed
        while not browser_values.resume_catalog_requests(self._request_paths[start:]):
            if standard_dependencies.time.monotonic() >= deadline:
                msg = "resume mode did not request the resume catalog"
                raise AssertionError(msg)
            self._page.wait_for_timeout(DOM_POLL_MILLISECONDS)

    def assert_form_offers_source(self, form: browser_references.BrowserSessionFormRef) -> None:
        option = self._resume_option(browser_values.form_source(form))
        browser_expectation(option).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))

    def resume_from_session_form(
        self, form: browser_references.BrowserSessionFormRef, prompt: str,
    ) -> browser_assertions.BrowserSessionResume:
        if form.resume_request_start_index is None:
            raise AssertionError(FORM_NOT_IN_RESUME_MODE)
        prepared = self._resume.prepare(browser_values.form_source(form))
        option = self._resume_option(browser_values.form_source(form))
        browser_expectation(option).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        option.click()
        dialog = self._new_session_dialog()
        dialog.get_by_placeholder(NEW_SESSION_PROMPT_PATTERN).fill(prompt)
        launch = dialog.get_by_role(BUTTON_ROLE, name="launch", exact=True)
        browser_expectation(launch).to_be_enabled(timeout=self._milliseconds(self._wait_policy.feed))
        launch.click()
        completed = self._resume.complete(prepared, prompt)
        self._wait_for_session_url(completed.turn.session)
        return browser_assertions.BrowserSessionResume(completed.turn.session, completed.continuation, completed.turn)


class BrowserNavigationDriver(_BrowserResumeFormDriver):
    """Provide session navigation browser actions."""

    def assert_showing(self, session: runtime_dependencies.SessionRef) -> None:
        """Wait for the requested session and its message composer."""
        self._wait_for_session_url(session)
        browser_expectation(self._page.get_by_label(MESSAGE_COMPOSER_LABEL)).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def open_session(self, session: runtime_dependencies.SessionRef) -> None:
        """Open the session page and wait for its composer.

        Raises:
            AssertionError: If the dashboard returns an error response.

        """
        response = self._page.goto(f"{self._endpoint}#/s/{session.session_id}")
        if response is not None and (not response.ok):
            msg = f"dashboard returned HTTP {response.status}"
            raise AssertionError(msg)
        self.assert_showing(session)

    def close_session(self, session: runtime_dependencies.SessionRef) -> None:
        """Close the session through its visible control.

        Raises:
            AssertionError: If the close control is not available.

        """
        self.assert_showing(session)
        close = self._page.get_by_role(BUTTON_ROLE, name="✕ close", exact=True)
        browser_expectation(close).to_be_enabled(timeout=self._milliseconds(self._wait_policy.feed))
        close.click()
        confirm = self._page.get_by_role(BUTTON_ROLE, name="close session?", exact=True)
        with self._page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.endswith(f"/api/sessions/{session.session_id}/controls/close-session")
            ),
            timeout=self._milliseconds(self._wait_policy.feed),
        ) as response_info:
            confirm.click()
            if not response_info.value.ok:
                status = response_info.value.status
                msg = f"browser close returned HTTP {status}"
                raise AssertionError(msg)
        browser_expectation(self._page).to_have_url(
            regular_expressions.compile("/#/$"), timeout=self._milliseconds(self._wait_policy.feed),
        )
