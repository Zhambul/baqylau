# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide browser feed."""

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
    browser_capabilities,
    browser_navigation,
    browser_session_forms,
    browser_usage,
    browser_values,
    references as browser_references,
)

browser_expectation = model_dependencies.expect
regular_expressions = standard_dependencies.re

BUTTON_ROLE: Literal["button"] = "button"
NEW_SESSION_BUTTON_LABEL = "+ session"
MESSAGE_COMPOSER_LABEL = "message composer"
TEXTAREA_SELECTOR = "textarea"
STREAM_SELECTOR = ".stream"
ALERTS_ENABLED_LABEL = "◉ alerts"
DOM_POLL_MILLISECONDS = 25
SENTINEL_SCROLL_TIMEOUT_MILLISECONDS = 250


class _BrowserComposerDriver(browser_navigation.BrowserNavigationDriver):
    """Provide browser composer actions."""

    def send_prompt(self, session: runtime_dependencies.SessionRef, prompt: str) -> browser_references.TurnRef:
        before = self._client.sessions.snapshot(session)
        lead = before.lead()
        composer = self._page.get_by_label(MESSAGE_COMPOSER_LABEL)
        composer.locator(TEXTAREA_SELECTOR).fill(prompt)
        self._submit_composer(session, composer)
        return browser_references.TurnRef(
            session,
            prompt,
            before.entry_cursor,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )

    def type_composer_draft(self, text: str) -> None:
        composer = self._page.get_by_label(MESSAGE_COMPOSER_LABEL)
        browser_expectation(composer).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        composer.locator(TEXTAREA_SELECTOR).fill(text)

    def assert_composer_draft(self, text: str) -> None:
        browser_expectation(self._page.get_by_label(MESSAGE_COMPOSER_LABEL).locator(TEXTAREA_SELECTOR)).to_have_value(
            text,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def send_composer_draft(self, session: runtime_dependencies.SessionRef) -> browser_references.TurnRef:
        before = self._client.sessions.snapshot(session)
        lead = before.lead()
        composer = self._page.get_by_label(MESSAGE_COMPOSER_LABEL)
        prompt = composer.locator(TEXTAREA_SELECTOR).input_value().strip()
        if not prompt:
            msg = "browser composer draft is empty"
            raise AssertionError(msg)
        self._submit_composer(session, composer)
        return browser_references.TurnRef(
            session,
            prompt,
            before.entry_cursor,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )

    def _submit_composer(self, session: runtime_dependencies.SessionRef, composer: client_dependencies.Locator) -> None:
        button = composer.get_by_role(BUTTON_ROLE, name="send", exact=True)
        browser_expectation(button).to_be_enabled(timeout=self._milliseconds(self._wait_policy.feed))
        path = f"/api/sessions/{session.session_id}/controls/send-text"
        try:
            browser_assertions.send_text_request(
                self._page,
                button,
                path,
                self._milliseconds(self._wait_policy.pipeline),
            )
        except model_dependencies.PlaywrightTimeoutError as error:
            composer_value = composer.locator(TEXTAREA_SELECTOR).input_value()
            recent_paths = self._request_paths[-10:]
            msg = (
                f"browser composer Send did not issue its request: value={composer_value!r}, "
                f"disabled={button.is_disabled()}, recent_request_paths={recent_paths!r}"
            )
            raise AssertionError(
                msg,
            ) from error


class _BrowserReloadDriver(_BrowserComposerDriver):
    """Provide browser reload and operation actions."""

    def reload(self, session: runtime_dependencies.SessionRef) -> None:
        self._page.reload()
        self.assert_showing(session)

    def reload_session_list(self) -> None:
        self._page.reload()
        browser_expectation(self._page.get_by_role(BUTTON_ROLE, name=NEW_SESSION_BUTTON_LABEL)).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_running_elapsed_at_least(self, reference: browser_references.ShellRef, seconds: int) -> None:
        browser_assertions.shell_command(self._client.sessions.snapshot(reference.session), reference)
        block = self._shell_block(reference)
        self._show_shell_block(block)
        timer = block.locator(".blive")
        runtime_dependencies.wait_for(
            f"browser running operation time to reach {seconds} seconds",
            standard_dependencies.partial(browser_session_forms.running_elapsed_at_least, timer, seconds),
            timeout=self._wait_policy.feed,
        )

    def assert_completed_elapsed_at_least(self, reference: browser_references.ShellRef, seconds: int) -> None:
        browser_assertions.shell_command(self._client.sessions.snapshot(reference.session), reference)
        block = self._shell_block(reference)
        self._show_shell_block(block)
        browser_session_forms.assert_completed_operation_elapsed(
            block,
            seconds,
            self._milliseconds(self._wait_policy.feed),
        )

    def _shell_block(self, reference: browser_references.ShellRef) -> client_dependencies.Locator:
        return self._page.locator(f'.stream .blk[data-entry-key="shell:{reference.shell_id}"]')

    def _show_shell_block(self, block: client_dependencies.Locator) -> None:
        summaries = self._page.locator(".stream .vsum")
        for index in range(summaries.count()):
            if block.count() > 0:
                break
            summaries.nth(index).click()
        browser_expectation(block).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))


class _BrowserFeedDriver(_BrowserReloadDriver):
    """Provide browser feed actions."""

    def interrupt_turn(self) -> None:
        stop = self._page.locator("button.actstop")
        browser_expectation(stop).to_be_enabled(timeout=self._milliseconds(self._wait_policy.feed))
        stop.click()

    def assert_queued_prompt(self, text: str) -> None:
        queued = self._page.locator(".msg.prompt.queued").filter(has_text=text)
        browser_expectation(queued).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        browser_expectation(queued.locator(".qbadge")).to_have_text("⧗ queued")

    def assert_no_queued_prompt(self, text: str) -> None:
        browser_expectation(self._page.locator(".msg.prompt.queued").filter(has_text=text)).to_have_count(
            0,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_text_visible(self, text: str) -> None:
        browser_expectation(self._page.get_by_text(text, exact=True).last).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_feed_text_containing_visible(self, text: str) -> None:
        matches = self._page.locator(STREAM_SELECTOR).get_by_text(text, exact=False)
        browser_expectation(matches.first).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))

    def assert_feed_text_containing_absent(self, text: str) -> None:
        matches = self._page.locator(STREAM_SELECTOR).get_by_text(text, exact=False)
        browser_expectation(matches).to_have_count(0)


class _BrowserHistoryDriver(_BrowserFeedDriver):
    """Provide browser history and file actions."""

    def assert_file_diff_colors(self, reference: browser_references.FileOperationRef) -> None:
        snapshot = self._client.sessions.snapshot(reference.session)
        if reference.actor_id != snapshot.lead().actor_id:
            response = self._page.goto(
                "{endpoint}#/s/{session}/a/{actor}".format(
                    endpoint=self._endpoint,
                    session=client_dependencies.quote(reference.session.session_id, safe=""),
                    actor=client_dependencies.quote(reference.actor_id, safe=""),
                ),
            )
            if response is not None and (not response.ok):
                msg = f"dashboard returned HTTP {response.status}"
                raise AssertionError(msg)
            browser_expectation(self._page.locator(STREAM_SELECTOR)).to_be_visible(
                timeout=self._milliseconds(self._wait_policy.feed),
            )
        block = browser_usage.file_diff_block(self._page, snapshot, reference)
        browser_expectation(block).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        browser_usage.assert_file_diff_coloring(block, self._milliseconds(self._wait_policy.feed))

    def assert_older_history_available(self, oldest_marker: str) -> None:
        marker = self._page.locator(STREAM_SELECTOR).get_by_text(oldest_marker, exact=False)
        sentinel = self._page.locator(".load-sentinel")
        deadline = standard_dependencies.time.monotonic() + self._wait_policy.feed
        while not marker.count() and (not sentinel.count()):
            if standard_dependencies.time.monotonic() >= deadline:
                msg = "browser exposed neither older history nor its load sentinel"
                raise AssertionError(msg)
            self._page.wait_for_timeout(DOM_POLL_MILLISECONDS)

    def load_older_history(self) -> None:
        for _ in range(100):
            sentinel = self._page.locator(".load-sentinel")
            if not sentinel.count():
                return
            try:
                sentinel.scroll_into_view_if_needed(timeout=SENTINEL_SCROLL_TIMEOUT_MILLISECONDS)
            except (model_dependencies.PlaywrightError, model_dependencies.PlaywrightTimeoutError):
                continue
            browser_expectation(self._page.locator(".feed-loader")).to_have_count(
                0,
                timeout=self._milliseconds(self._wait_policy.feed),
            )
        msg = "browser history still has an older page after 100 reads"
        raise AssertionError(msg)

    def answer_question(
        self,
        reference: browser_references.QuestionRef,
        option: str,
    ) -> browser_references.BrowserActionRef:
        snapshot = self._client.sessions.snapshot(reference.session)
        _, prompt = browser_values.question(snapshot, reference)
        card = self._page.locator(".askcard").filter(has_text=prompt.question)
        browser_expectation(card).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        question = card.locator(".askq").filter(has_text=prompt.question)
        option_button = question.locator("button.askopt").filter(
            has=self._page.locator(
                ".aol",
                has_text=regular_expressions.compile(f"^{regular_expressions.escape(option)}$"),
            ),
        )
        browser_expectation(option_button).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        option_button.click()
        card.get_by_role(BUTTON_ROLE, name=regular_expressions.compile("^submit answer(s)?$")).click()
        self._wait_for_question_resolution(reference)
        return browser_references.BrowserActionRef(reference.session, snapshot.entry_cursor)

    def discuss_question(self, reference: browser_references.QuestionRef) -> browser_references.BrowserActionRef:
        snapshot = self._client.sessions.snapshot(reference.session)
        _state, prompt = browser_values.question(snapshot, reference)
        card = self._page.locator(".askcard").filter(has_text=prompt.question)
        browser_expectation(card).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        card.get_by_role(BUTTON_ROLE, name="chat about this", exact=True).click()
        self._wait_for_question_resolution(reference)
        return browser_references.BrowserActionRef(reference.session, snapshot.entry_cursor)

    def decide_plan(
        self,
        reference: browser_references.PlanRef,
        action: browser_capabilities.BrowserPlanAction,
        *,
        feedback: str | None = None,
    ) -> browser_references.BrowserActionRef:
        snapshot = self._client.sessions.snapshot(reference.session)
        browser_values.plan(snapshot, reference)
        card = self._page.locator(".plancard")
        browser_expectation(card).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        self._submit_plan_action(reference, action, feedback, card)
        wanted = {
            browser_capabilities.BrowserPlanAction.approve: "approved",
            browser_capabilities.BrowserPlanAction.dismiss: "rejected",
            browser_capabilities.BrowserPlanAction.feedback: "changes_requested",
        }[action]
        self._wait_for_plan_resolution(reference, wanted)
        return browser_references.BrowserActionRef(reference.session, snapshot.entry_cursor)


class _BrowserStatusDriver(_BrowserHistoryDriver):
    """Provide browser status display assertions."""

    def assert_session_card_status(self, session: runtime_dependencies.SessionRef, status: str) -> None:
        card = self._session_card(session)
        browser_expectation(card).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        browser_expectation(card).to_have_attribute("data-tab", status)
        browser_values.assert_status_color(card.locator(".badge .st"), status)

    def assert_session_header_status(self, status: str) -> None:
        header = self._page.locator(".shead")
        browser_expectation(header).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        browser_expectation(header).to_have_attribute("data-tab", status)
        browser_values.assert_status_color(header.locator(".badge .st"), status)

    def assert_session_header_title(self, title: str) -> None:
        browser_expectation(self._page.locator(".shead .proj")).to_have_text(
            title,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_attention_status(self, session: runtime_dependencies.SessionRef, status: str) -> None:
        pill = self._page.locator(f'.attn-pill[href="#/s/{session.session_id}"]')
        browser_expectation(pill).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        class_name = {
            "awaiting_attention": "ask",
            "awaiting_response": "done",
            "thinking": "busy",
            "working": "busy",
            "executing": "run",
            "awaiting_background": "run",
            "idle": "idle",
        }[status]
        browser_expectation(pill).to_have_class(regular_expressions.compile(f"\\b{class_name}\\b"))
        browser_values.assert_status_color(pill.locator(".adot"), status)

    def assert_asking_count(self, count: int) -> None:
        if count > 0:
            browser_expectation(self._page.locator(".alead.ask")).to_have_text(f"{count} asking")
            browser_expectation(self._page).to_have_title(f"({count}) baqylau")
        else:
            browser_expectation(self._page.locator(".alead.ask")).to_have_count(0)
            browser_expectation(self._page).to_have_title("baqylau")
        favicon = self._page.locator("#favicon").get_attribute("href") or ""
        has_attention_color = "e06c75" in favicon.casefold()
        expected_attention_color = count > 0
        message = f"favicon attention state is {has_attention_color}, expected {expected_attention_color}"
        assert has_attention_color == expected_attention_color, message


class _BrowserNotificationDriver(_BrowserStatusDriver):
    """Provide browser notification actions."""

    def set_session_notifications_muted(self, session: runtime_dependencies.SessionRef, *, muted: bool) -> None:
        """Change the session mute setting and verify the stored state."""
        self.assert_showing(session)
        button = self._page.locator("#sessact").get_by_role(
            BUTTON_ROLE,
            name=ALERTS_ENABLED_LABEL if muted else "○ muted",
            exact=True,
        )
        button.click()
        self.assert_session_notifications_muted(session, muted=muted)

    def assert_session_notifications_muted(self, session: runtime_dependencies.SessionRef, *, muted: bool) -> None:
        """Verify the session mute button and stored preference."""
        label = "○ muted" if muted else ALERTS_ENABLED_LABEL
        button = self._page.locator("#sessact").get_by_role(BUTTON_ROLE, name=label, exact=True)
        browser_expectation(button).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        runtime_dependencies.wait_for(
            f"session {session.session_id!r} notification mute state {muted}",
            lambda: (
                True
                if self._client.preferences.session_state(session).preferences.notifications_muted == muted
                else None
            ),
            timeout=self._wait_policy.feed,
        )

    def set_global_notifications(self, *, enabled: bool) -> None:
        """Change the global notification setting and verify the stored state."""
        button = self._page.locator("#notifytoggle")
        current = ALERTS_ENABLED_LABEL if enabled else "○ alerts off"
        opposite = "○ alerts off" if enabled else ALERTS_ENABLED_LABEL
        browser_expectation(button).to_have_text(opposite)
        button.click()
        browser_expectation(button).to_have_text(current, timeout=self._milliseconds(self._wait_policy.feed))
        self.assert_global_notifications(enabled=enabled)

    def assert_global_notifications(self, *, enabled: bool) -> None:
        """Verify the global notification button and stored preference."""
        label = ALERTS_ENABLED_LABEL if enabled else "○ alerts off"
        browser_expectation(self._page.locator("#notifytoggle")).to_have_text(
            label,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        runtime_dependencies.wait_for(
            f"global notification state {enabled}",
            lambda: True if self._client.application.state().notifications.enabled == enabled else None,
            timeout=self._wait_policy.feed,
        )


class BrowserWorkspaceDriver(_BrowserNotificationDriver):
    """Provide browser workspace and connection actions."""

    def assert_workspace_visible(self) -> None:
        """Check that the workspace group is visible."""
        timeout = self._milliseconds(self._wait_policy.feed)
        browser_expectation(self._workspace_group()).to_be_visible(timeout=timeout)

    def hide_workspace(self) -> None:
        """Hide the workspace through its visible control."""
        group = self._workspace_group()
        browser_expectation(group).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        button = group.locator(".dirhide")
        browser_expectation(button).to_be_enabled(timeout=self._milliseconds(self._wait_policy.feed))
        button.click()
        browser_expectation(self._workspace_group()).to_have_count(
            0,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_workspace_hidden(self) -> None:
        """Check that the workspace group is absent."""
        browser_expectation(self._workspace_group()).to_have_count(0)
