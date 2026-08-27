"""A browser page object for real dashboard session journeys."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote, urlsplit

from api.application.models.preferences.global_application_response import (
    GlobalApplicationResponse,
)
from api.controls.models.control_outcome_response import PlanChoicesResultResponse
from api.common.models.values.usage_row import UsageRowResponse, UsageWindowResponse
from api.sessiondata.models.entry import FileBodyResponse, QuestionResponse
from domain.sessiondata import ActorStatus
from playwright.sync_api import (
    ConsoleMessage,
    Error as PlaywrightError,
    Locator,
    Page,
    Request,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    expect,
)

from sdk.client import BaqylauClient, SessionRef, wait_for
from sdk.state import PlanState, QuestionState, SessionSnapshot
from terminal.theme import tab_appearance
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    BrowserActionRef,
    BrowserSessionFormRef,
    FileOperationRef,
    PlanRef,
    QuestionRef,
    SessionContinuationRef,
    SessionSpec,
    TurnRef,
)
from tests.e2e.testkit.resume import SessionResumeSupport

SESSION_FRAGMENT = re.compile(r"^/s/([^/?#]+)$")
SSE_DOCUMENT_MARKER = "baqylau-e2e-sse-marker"


@dataclass(frozen=True)
class BrowserSessionStart:
    session: SessionRef
    turn: TurnRef


@dataclass(frozen=True)
class BrowserSessionResume:
    session: SessionRef
    continuation: SessionContinuationRef
    turn: TurnRef


class BrowserPlanAction(StrEnum):
    APPROVE = "approve"
    DISMISS = "dismiss"
    FEEDBACK = "feedback"


def default_model_usage_window(
    rows: Sequence[UsageRowResponse],
    harness: str,
    model: str,
) -> tuple[UsageRowResponse, UsageWindowResponse] | None:
    """Select one default model window, or wait while no row exists."""
    failures = [
        f"{row.harness}: {row.collection_error}"
        for row in rows
        if row.collection_error is not None
    ]
    if failures:
        if all("timed out" in failure.casefold() for failure in failures):
            return None
        raise AssertionError("usage refresh failed: " + "; ".join(failures))
    harness_rows = [row for row in rows if row.harness == harness]
    if not harness_rows:
        return None
    if len(harness_rows) > 1:
        raise AssertionError(
            f"harness {harness!r} has {len(harness_rows)} usage rows"
        )
    row = harness_rows[0]
    if row.account_id is not None or row.switchable:
        raise AssertionError(f"harness {harness!r} published an account selection")
    matches = [
        (row, window)
        for window in row.windows
        if window.scope == "model" and window.model_id == model
    ]
    if len(matches) > 1:
        raise AssertionError(
            f"harness {harness!r} has {len(matches)} model usage "
            f"windows for {model!r}"
        )
    return matches[0] if matches else None


class BrowserSessionDriver:
    """Use visible controls and return the same typed references as API journeys."""

    def __init__(
        self,
        page: Page,
        client: BaqylauClient,
        endpoint: str,
        workspace: str,
        wait_policy: WaitPolicy,
    ) -> None:
        self._page = page
        self._client = client
        self._endpoint = endpoint.rstrip("/") + "/"
        self._workspace = workspace
        self._wait_policy = wait_policy
        self._resume = SessionResumeSupport(client, wait_policy)
        self._browser_failures: list[str] = []
        self._request_paths: list[str] = []
        self._network_drop_expected = False
        self._usage_document_marker: str | None = None
        page.on("console", self._record_console_error)
        page.on("pageerror", lambda error: self._browser_failures.append(str(error)))
        page.on("request", self._record_request)

    def open_session_list(self) -> None:
        response = self._page.goto(self._endpoint)
        if response is None or not response.ok:
            status = None if response is None else response.status
            raise AssertionError(f"dashboard returned HTTP {status}")
        expect(self._page.get_by_role("button", name="+ session")).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        if self._usage_document_marker == "pending":
            marker = self._page.evaluate(
                "() => globalThis.__baqylauUsageDocumentMarker"
            )
            if not isinstance(marker, str):
                raise AssertionError("the browser document marker is missing")
            self._usage_document_marker = marker

    def omit_usage_from_next_application_read(self, harness: str) -> None:
        self._page.add_init_script(
            "globalThis.__baqylauUsageDocumentMarker = crypto.randomUUID()"
        )
        self._usage_document_marker = "pending"

        def omit(route: Route) -> None:
            response = route.fetch()
            document = GlobalApplicationResponse.model_validate(response.json())
            filtered = document.model_copy(update={
                "usage_rows": tuple(
                    row for row in document.usage_rows if row.harness != harness
                ),
            })
            route.fulfill(response=response, json=filtered.model_dump(mode="json"))

        self._page.route("**/api/application", omit, times=1)

    def assert_usage_row_appears_without_reload(self, harness: str) -> None:
        marker = self._usage_document_marker
        if marker is None or marker == "pending":
            raise AssertionError("the initial application read was not intercepted")
        name = self._page.locator(".aname").filter(
            has_text=re.compile(rf"^{re.escape(harness)}$")
        )
        expect(name).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        current_marker = self._page.evaluate(
            "() => globalThis.__baqylauUsageDocumentMarker"
        )
        assert current_marker == marker, "the browser reloaded the document"

    def start(self, spec: SessionSpec, prompt: str) -> BrowserSessionStart:
        known = frozenset(
            item.session.session_id for item in self._client.sessions.list().sessions
        )
        workspace = spec.workspace or self._workspace
        dialog = self._open_new_session()
        self._configure_fresh_session(dialog, spec, workspace)
        dialog.get_by_placeholder(re.compile(r"what should .* start on\?")).fill(prompt)
        dialog.get_by_role("button", name="launch", exact=True).click()
        session = self._wait_for_visible_session(known)
        snapshot = self._client.sessions.snapshot(session)
        if snapshot.data.session.harness != spec.harness:
            raise AssertionError(
                f"browser launched {snapshot.data.session.harness!r}, not "
                f"{spec.harness!r}"
            )
        if snapshot.data.session.working_directory != workspace:
            raise AssertionError(
                f"browser launched in {snapshot.data.session.working_directory!r}, "
                f"not {workspace!r}"
            )
        turn = selectors.launched_turn(
            self._client.sessions.watch(session),
            self._wait_policy.feed,
        )
        return BrowserSessionStart(session, turn)

    def resume(self, source: SessionRef, prompt: str) -> BrowserSessionResume:
        form = self.open_fresh_session_form(source)
        form = self.switch_session_form_to_resume(form)
        return self.resume_from_session_form(form, prompt)

    def open_fresh_session_form(self, source: SessionRef) -> BrowserSessionFormRef:
        request_start_index = len(self._request_paths)
        workspace = self._client.sessions.snapshot(
            source
        ).data.session.working_directory
        dialog = self._open_new_session()
        dialog.get_by_label("directory").fill(workspace)
        dialog.get_by_label("directory").press("Tab")
        expect(dialog.get_by_text("fresh conversation", exact=True)).to_be_visible()
        return BrowserSessionFormRef(source, request_start_index)

    def open_configured_fresh_session_form(
        self,
        spec: SessionSpec,
    ) -> BrowserSessionFormRef:
        request_start_index = len(self._request_paths)
        dialog = self._open_new_session()
        self._configure_fresh_session(
            dialog,
            spec,
            spec.workspace or self._workspace,
        )
        return BrowserSessionFormRef(None, request_start_index)

    def type_session_form_prompt(
        self,
        _form: BrowserSessionFormRef,
        text: str,
    ) -> None:
        self._new_session_dialog().get_by_placeholder(
            re.compile(r"what should .* start on\?")
        ).fill(text)

    def close_session_form(self, _form: BrowserSessionFormRef) -> None:
        self._new_session_dialog().press("Escape")
        expect(self._page.get_by_role("dialog", name="new session")).to_have_count(0)

    def assert_session_form_prompt(
        self,
        _form: BrowserSessionFormRef,
        text: str,
    ) -> None:
        expect(
            self._new_session_dialog().get_by_placeholder(
                re.compile(r"what should .* start on\?")
            )
        ).to_have_value(text)

    def assert_session_form_has_no_account_selection(
        self,
        _form: BrowserSessionFormRef,
    ) -> None:
        dialog = self._new_session_dialog()
        expect(dialog.get_by_label("account", exact=True)).to_have_count(0)
        expect(dialog.locator(".nslabel", has_text="account")).to_have_count(0)

    def switch_session_form_to_resume(
        self,
        form: BrowserSessionFormRef,
    ) -> BrowserSessionFormRef:
        if form.resume_request_start_index is not None:
            raise AssertionError("browser session form is already in resume mode")
        request_start_index = len(self._request_paths)
        dialog = self._new_session_dialog()
        dialog.get_by_text("fresh conversation", exact=True).click()
        expect(dialog.get_by_text("resume a conversation", exact=True)).to_be_visible()
        return BrowserSessionFormRef(
            form.source,
            form.request_start_index,
            request_start_index,
        )

    def assert_form_did_not_request_resume_catalog(
        self,
        form: BrowserSessionFormRef,
    ) -> None:
        paths = self._request_paths[form.request_start_index:]
        assert self._resume_catalog_requests(paths) == (), (
            "a fresh browser session form requested the resume catalog"
        )

    def assert_form_requested_resume_catalog(
        self,
        form: BrowserSessionFormRef,
    ) -> None:
        start = form.resume_request_start_index
        if start is None:
            raise AssertionError("browser session form is not in resume mode")
        deadline = time.monotonic() + self._wait_policy.feed
        while not self._resume_catalog_requests(self._request_paths[start:]):
            if time.monotonic() >= deadline:
                raise AssertionError("resume mode did not request the resume catalog")
            self._page.wait_for_timeout(25)

    def assert_form_offers_source(self, form: BrowserSessionFormRef) -> None:
        option = self._resume_option(self._form_source(form))
        expect(option).to_have_count(
            1,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def resume_from_session_form(
        self,
        form: BrowserSessionFormRef,
        prompt: str,
    ) -> BrowserSessionResume:
        if form.resume_request_start_index is None:
            raise AssertionError("browser session form is not in resume mode")
        source = self._form_source(form)
        prepared = self._resume.prepare(source)
        option = self._resume_option(source)
        expect(option).to_have_count(
            1,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        option.click()
        dialog = self._new_session_dialog()
        dialog.get_by_placeholder(re.compile(r"what should .* start on\?")).fill(prompt)
        launch = dialog.get_by_role("button", name="launch", exact=True)
        # Selecting a resume row starts the matching harness-catalog load. On
        # a busy parallel run the row is visible before that request settles,
        # and clicking the still-disabled button is a silent browser no-op.
        expect(launch).to_be_enabled(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        launch.click()
        completed = self._resume.complete(prepared, prompt)
        self._wait_for_session_url(completed.turn.session)
        return BrowserSessionResume(
            completed.turn.session,
            completed.continuation,
            completed.turn,
        )

    def assert_showing(self, session: SessionRef) -> None:
        self._wait_for_session_url(session)
        expect(self._page.get_by_label("message composer")).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def open_session(self, session: SessionRef) -> None:
        response = self._page.goto(
            f"{self._endpoint}#/s/{session.session_id}"
        )
        # A hash-only navigation stays in the current document, so Playwright
        # correctly returns no HTTP response. A response that does exist must
        # still be successful.
        if response is not None and not response.ok:
            raise AssertionError(f"dashboard returned HTTP {response.status}")
        self.assert_showing(session)

    def close_session(self, session: SessionRef) -> None:
        self.assert_showing(session)
        close = self._page.get_by_role("button", name="✕ close", exact=True)
        expect(close).to_be_enabled(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        close.click()
        confirm = self._page.get_by_role(
            "button", name="close session?", exact=True
        )
        with self._page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.endswith(
                f"/api/sessions/{session.session_id}/controls/close-session"
            ),
            timeout=self._milliseconds(self._wait_policy.feed),
        ) as response_info:
            confirm.click()
        if not response_info.value.ok:
            raise AssertionError(
                f"browser close returned HTTP {response_info.value.status}"
            )
        expect(self._page).to_have_url(
            re.compile(r"/#/$"),
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def send_prompt(self, session: SessionRef, prompt: str) -> TurnRef:
        before = self._client.sessions.snapshot(session)
        lead = before.lead()
        composer = self._page.get_by_label("message composer")
        composer.locator("textarea").fill(prompt)
        composer.get_by_role("button", name="send", exact=True).click()
        return TurnRef(
            session,
            prompt,
            before.cursor,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )

    def type_composer_draft(self, text: str) -> None:
        composer = self._page.get_by_label("message composer")
        expect(composer).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        composer.locator("textarea").fill(text)

    def assert_composer_draft(self, text: str) -> None:
        expect(
            self._page.get_by_label("message composer").locator("textarea")
        ).to_have_value(
            text,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def send_composer_draft(self, session: SessionRef) -> TurnRef:
        before = self._client.sessions.snapshot(session)
        lead = before.lead()
        composer = self._page.get_by_label("message composer")
        prompt = composer.locator("textarea").input_value().strip()
        if not prompt:
            raise AssertionError("browser composer draft is empty")
        button = composer.get_by_role("button", name="send", exact=True)
        expect(button).to_be_enabled(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        button.click()
        return TurnRef(
            session,
            prompt,
            before.cursor,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        )

    def reload(self, session: SessionRef) -> None:
        self._page.reload()
        self.assert_showing(session)

    def reload_session_list(self) -> None:
        self._page.reload()
        expect(self._page.get_by_role("button", name="+ session")).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_queued_prompt(self, text: str) -> None:
        queued = self._page.locator(".msg.prompt.queued").filter(has_text=text)
        expect(queued).to_have_count(
            1,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        expect(queued.locator(".qbadge")).to_have_text("⧗ queued")

    def assert_no_queued_prompt(self, text: str) -> None:
        expect(
            self._page.locator(".msg.prompt.queued").filter(has_text=text)
        ).to_have_count(
            0,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_text_visible(self, text: str) -> None:
        expect(self._page.get_by_text(text, exact=True).last).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_feed_text_containing_visible(self, text: str) -> None:
        matches = self._page.locator(".stream").get_by_text(text, exact=False)
        expect(matches).to_have_count(
            1,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        expect(matches).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_feed_text_containing_absent(self, text: str) -> None:
        expect(self._page.locator(".stream").get_by_text(text, exact=False)).to_have_count(0)

    def assert_file_diff_colors(self, reference: FileOperationRef) -> None:
        snapshot = self._client.sessions.snapshot(reference.session)
        operations = [
            entry.body
            for entry in snapshot.entries
            if entry.entry_id == reference.entry_id
            and isinstance(entry.body, FileBodyResponse)
        ]
        if len(operations) != 1:
            raise AssertionError(
                f"file operation {reference.entry_id!r} has {len(operations)} matches"
            )
        if reference.actor_id != snapshot.lead().actor_id:
            response = self._page.goto(
                f"{self._endpoint}#/s/{quote(reference.session.session_id, safe='')}"
                f"/a/{quote(reference.actor_id, safe='')}"
            )
            if response is not None and not response.ok:
                raise AssertionError(f"dashboard returned HTTP {response.status}")
            expect(self._page.locator(".stream")).to_be_visible(
                timeout=self._milliseconds(self._wait_policy.feed),
            )
        block = self._page.locator(".stream .blk").filter(
            has=self._page.locator(".bchips", has_text=operations[0].path),
        ).filter(
            has=self._page.locator(".bchips > span:first-child", has_text="Edit"),
        )
        expect(block).to_have_count(
            1,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        if block.get_attribute("data-open") != "1":
            block.locator(".bhead").click()
        removed = block.locator(".tdiff .removed").first
        added = block.locator(".tdiff .added").first
        expect(removed).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        expect(added).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        self._assert_mixed_background(removed, "--red", 24)
        self._assert_mixed_background(added, "--green", 24)
        expect(removed).to_have_attribute("aria-label", re.compile(r"^removed line "))
        expect(added).to_have_attribute("aria-label", re.compile(r"^added line "))
        expect(removed.locator(".dm")).to_have_text("−")
        expect(added.locator(".dm")).to_have_text("+")

    def assert_older_history_available(self, oldest_marker: str) -> None:
        marker = self._page.locator(".stream").get_by_text(
            oldest_marker,
            exact=False,
        )
        sentinel = self._page.locator(".load-sentinel")
        deadline = time.monotonic() + self._wait_policy.feed
        while marker.count() == 0 and sentinel.count() == 0:
            # The initial page can finish between the two observations: in
            # that state the old marker has arrived and the now-unneeded
            # sentinel has already been retired. Observe both outcomes until
            # either one is true instead of pinning a disappearing DOM node.
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "browser exposed neither older history nor its load sentinel"
                )
            self._page.wait_for_timeout(25)

    def load_older_history(self) -> None:
        for _page_number in range(100):
            sentinel = self._page.locator(".load-sentinel")
            if sentinel.count() == 0:
                return
            try:
                # IntersectionObserver may consume and replace this sentinel
                # between count() and the scroll.  Re-read instead of waiting
                # 30 seconds for a node that was successfully retired.
                sentinel.scroll_into_view_if_needed(timeout=250)
            except (PlaywrightError, PlaywrightTimeoutError):
                continue
            expect(self._page.locator(".feed-loader")).to_have_count(
                0,
                timeout=self._milliseconds(self._wait_policy.feed),
            )
        raise AssertionError("browser history still has an older page after 100 reads")

    def answer_question(
        self,
        reference: QuestionRef,
        option: str,
    ) -> BrowserActionRef:
        snapshot = self._client.sessions.snapshot(reference.session)
        state, prompt = self._question(snapshot, reference)
        card = self._page.locator(".askcard").filter(has_text=prompt.question)
        expect(card).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        question = card.locator(".askq").filter(has_text=prompt.question)
        option_button = question.locator("button.askopt").filter(
            has=self._page.locator(
                ".aol",
                has_text=re.compile(rf"^{re.escape(option)}$"),
            )
        )
        expect(option_button).to_have_count(
            1,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        option_button.click()
        card.get_by_role("button", name=re.compile(r"^submit answer(s)?$")).click()
        self._wait_for_question_resolution(reference)
        return BrowserActionRef(reference.session, snapshot.cursor)

    def discuss_question(self, reference: QuestionRef) -> BrowserActionRef:
        snapshot = self._client.sessions.snapshot(reference.session)
        _state, prompt = self._question(snapshot, reference)
        card = self._page.locator(".askcard").filter(has_text=prompt.question)
        expect(card).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        card.get_by_role("button", name="chat about this", exact=True).click()
        self._wait_for_question_resolution(reference)
        return BrowserActionRef(reference.session, snapshot.cursor)

    def decide_plan(
        self,
        reference: PlanRef,
        action: BrowserPlanAction,
        *,
        feedback: str | None = None,
    ) -> BrowserActionRef:
        snapshot = self._client.sessions.snapshot(reference.session)
        self._plan(snapshot, reference)
        card = self._page.locator(".plancard")
        expect(card).to_have_count(1, timeout=self._milliseconds(self._wait_policy.feed))
        if action == BrowserPlanAction.DISMISS:
            card.get_by_role("button", name="chat about this", exact=True).click()
        else:
            receipt = self._client.sessions.read_plan_choices(
                reference.session,
                reference.attention_id,
            )
            outcome = receipt.outcome
            if not isinstance(outcome, PlanChoicesResultResponse):
                raise AssertionError("plan did not return browser choices")
            choices = [
                choice
                for choice in outcome.choices
                if choice.feedback == (action == BrowserPlanAction.FEEDBACK)
            ]
            if action == BrowserPlanAction.APPROVE:
                choices = [choice for choice in choices if not choice.feedback]
            if len(choices) == 0:
                raise AssertionError(f"plan has no {action.value} choice")
            choice = choices[0]
            if action == BrowserPlanAction.FEEDBACK:
                if feedback is None or not feedback.strip():
                    raise AssertionError("plan feedback is empty")
                card.get_by_placeholder("feedback for requested changes…").fill(feedback)
            card.get_by_role("button", name=choice.label, exact=True).click()
        wanted = {
            BrowserPlanAction.APPROVE: "approved",
            BrowserPlanAction.DISMISS: "rejected",
            BrowserPlanAction.FEEDBACK: "changes_requested",
        }[action]
        self._wait_for_plan_resolution(reference, wanted)
        return BrowserActionRef(reference.session, snapshot.cursor)

    def assert_session_card_status(self, session: SessionRef, status: str) -> None:
        card = self._session_card(session)
        expect(card).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        expect(card).to_have_attribute("data-tab", status)
        self._assert_status_color(card.locator(".badge .st"), status)

    def assert_session_header_status(self, status: str) -> None:
        header = self._page.locator(".shead")
        expect(header).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        expect(header).to_have_attribute("data-tab", status)
        self._assert_status_color(header.locator(".badge .st"), status)

    def assert_session_header_title(self, title: str) -> None:
        expect(self._page.locator(".shead .proj")).to_have_text(
            title,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_attention_status(self, session: SessionRef, status: str) -> None:
        pill = self._page.locator(f'.attn-pill[href="#/s/{session.session_id}"]')
        expect(pill).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        class_name = {
            "awaiting_attention": "ask",
            "awaiting_response": "done",
            "thinking": "busy",
            "working": "busy",
            "executing": "run",
            "awaiting_background": "run",
            "idle": "idle",
        }[status]
        expect(pill).to_have_class(re.compile(rf"\b{class_name}\b"))
        self._assert_status_color(pill.locator(".adot"), status)

    def assert_asking_count(self, count: int) -> None:
        if count > 0:
            expect(self._page.locator(".alead.ask")).to_have_text(f"{count} asking")
            expect(self._page).to_have_title(f"({count}) baqylau")
        else:
            expect(self._page.locator(".alead.ask")).to_have_count(0)
            expect(self._page).to_have_title("baqylau")
        favicon = self._page.locator("#favicon").get_attribute("href") or ""
        has_attention_color = "e06c75" in favicon.casefold()
        assert has_attention_color == (count > 0), (
            f"favicon attention state is {has_attention_color}, expected {count > 0}"
        )

    def set_session_notifications_muted(self, session: SessionRef, muted: bool) -> None:
        self.assert_showing(session)
        button = self._page.locator("#sessact").get_by_role(
            "button",
            name="◉ alerts" if muted else "○ muted",
            exact=True,
        )
        button.click()
        self.assert_session_notifications_muted(session, muted)

    def assert_session_notifications_muted(self, session: SessionRef, muted: bool) -> None:
        label = "○ muted" if muted else "◉ alerts"
        expect(self._page.locator("#sessact").get_by_role("button", name=label, exact=True)).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        wait_for(
            f"session {session.session_id!r} notification mute state {muted}",
            lambda: (
                True
                if self._client.preferences.session_state(session).preferences.notifications_muted
                == muted
                else None
            ),
            timeout=self._wait_policy.feed,
        )

    def set_global_notifications(self, enabled: bool) -> None:
        button = self._page.locator("#notifytoggle")
        current = "◉ alerts" if enabled else "○ alerts off"
        opposite = "○ alerts off" if enabled else "◉ alerts"
        expect(button).to_have_text(opposite)
        button.click()
        expect(button).to_have_text(current, timeout=self._milliseconds(self._wait_policy.feed))
        self.assert_global_notifications(enabled)

    def assert_global_notifications(self, enabled: bool) -> None:
        label = "◉ alerts" if enabled else "○ alerts off"
        expect(self._page.locator("#notifytoggle")).to_have_text(
            label,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        wait_for(
            f"global notification state {enabled}",
            lambda: (
                True
                if self._client.application.state().notifications.enabled == enabled
                else None
            ),
            timeout=self._wait_policy.feed,
        )

    def assert_workspace_visible(self) -> None:
        expect(self._workspace_group()).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def hide_workspace(self) -> None:
        group = self._workspace_group()
        expect(group).to_be_visible(timeout=self._milliseconds(self._wait_policy.feed))
        button = group.locator(".dirhide")
        expect(button).to_be_enabled(timeout=self._milliseconds(self._wait_policy.feed))
        button.click()
        expect(self._workspace_group()).to_have_count(
            0,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_workspace_hidden(self) -> None:
        expect(self._workspace_group()).to_have_count(0)

    def assert_connected(self) -> None:
        expect(self._page.locator("#conn")).to_have_attribute(
            "data-on",
            "1",
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def mark_document_for_connection_recovery(self) -> None:
        self.assert_connected()
        self._page.evaluate(
            "value => { globalThis.__baqylauE2eSseMarker = value; }",
            SSE_DOCUMENT_MARKER,
        )
        self._network_drop_expected = True

    def assert_reconnected_without_reload(self) -> None:
        self.assert_connected()
        found = self._page.evaluate("() => globalThis.__baqylauE2eSseMarker")
        assert found == SSE_DOCUMENT_MARKER, (
            "the browser reloaded while the event stream reconnected"
        )
        self._network_drop_expected = False

    def assert_session_card_visible(self, session: SessionRef) -> None:
        expect(self._session_card(session)).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_session_card_absent(self, session: SessionRef) -> None:
        expect(self._session_card(session)).to_have_count(
            0,
            timeout=self._milliseconds(self._wait_policy.feed),
        )

    def assert_shared_project_group(
        self,
        sessions: tuple[SessionRef, ...],
        project_directory: str,
        worktree_directory: str,
    ) -> None:
        headers = self._page.locator(".dirhead")
        project = headers.filter(has_text=project_directory)
        expect(project).to_have_count(
            1,
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        expect(project.locator(".dircount")).to_have_text(
            f"{len(sessions)} sessions"
        )
        expect(headers.filter(has_text=worktree_directory)).to_have_count(0)
        for session in sessions:
            self.assert_session_card_visible(session)

    def assert_default_model_usage_window(self, harness: str, model: str) -> None:
        def current_window() -> tuple[UsageRowResponse, UsageWindowResponse] | None:
            return default_model_usage_window(
                self._client.usage.state().usage_rows,
                harness,
                model,
            )

        row, model_window = wait_for(
            f"harness {harness!r} to publish its {model!r} model usage window",
            current_window,
            timeout=self._wait_policy.feed,
        )
        self._page.reload()
        expect(self._page.get_by_role("button", name="+ session")).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        names = self._page.locator(".aname")
        expect(names).to_have_count(2)
        assert set(names.all_text_contents()) == {"claude", "codex"}
        account_name = row.display_name
        account = self._page.locator(".acct").filter(
            has=self._page.locator(".aname").filter(
                has_text=re.compile(rf"^{re.escape(account_name)}$")
            )
        )
        expect(account).to_have_count(1)
        model_bar = account.locator(".ubar").filter(
            has=self._page.locator(".ulabel").filter(
                has_text=re.compile(rf"^{re.escape(model_window.label)}$")
            )
        )
        expect(model_bar).to_have_count(1)
        expect(model_bar.locator(".upct")).to_have_text(
            f"{model_window.used_percent}%"
        )
        expect(self._page.locator(".usage-collection-error")).to_have_count(0)
        weekly = next(
            (
                window
                for window in row.windows
                if window.scope == "account" and window.duration_minutes == 7 * 24 * 60
            ),
            None,
        )
        if weekly is None or weekly.resets_at is None:
            raise AssertionError(
                f"account {account_name!r} has no weekly reset information"
            )
        weekly_bar = account.locator(".ubar").filter(
            has=self._page.locator(".ulabel").filter(
                has_text=re.compile(rf"^{re.escape(weekly.label)}$")
            )
        )
        expect(weekly_bar.locator(".ureset")).to_contain_text("resets")

    def assert_clean(self) -> None:
        assert self._browser_failures == [], (
            f"browser reported failures: {self._browser_failures}"
        )

    def _open_new_session(self) -> Locator:
        self.open_session_list()
        self._page.get_by_role("button", name="+ session").click()
        dialog = self._page.get_by_role("dialog", name="new session")
        expect(dialog).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        return dialog

    def _new_session_dialog(self) -> Locator:
        dialog = self._page.get_by_role("dialog", name="new session")
        expect(dialog).to_be_visible(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        return dialog

    def _configure_fresh_session(
        self,
        dialog: Locator,
        spec: SessionSpec,
        workspace: str,
    ) -> None:
        directory = dialog.get_by_label("directory")
        directory.fill(workspace)
        directory.press("Tab")
        harnesses = tuple(
            harness
            for harness in self._client.harnesses.list()
            if harness.name == spec.harness
        )
        if len(harnesses) != 1:
            raise AssertionError(
                f"harness {spec.harness!r} has {len(harnesses)} catalog rows"
            )
        self._select(dialog, "harness", harnesses[0].display_name)
        catalog = self._client.harnesses.catalog(
            spec.harness,
            workspace=workspace,
        )
        if spec.account_id is not None:
            accounts = [
                row
                for row in self._client.usage.state().usage_rows
                if row.harness == spec.harness
                and row.account_id == spec.account_id
                and row.switchable
            ]
            if len(accounts) != 1:
                raise AssertionError(
                    f"account {spec.account_id!r} has {len(accounts)} usage rows"
                )
            self._select(dialog, "account", accounts[0].display_name)
        models = tuple(model for model in catalog.models if model.model_id == spec.model)
        if len(models) != 1:
            raise AssertionError(
                f"model {spec.model!r} has {len(models)} catalog rows"
            )
        model = models[0]
        self._select(dialog, "model", model.display_name)
        efforts = tuple(effort for effort in model.efforts if effort.value == spec.effort)
        if len(efforts) != 1:
            raise AssertionError(
                f"effort {spec.effort!r} has {len(efforts)} catalog rows"
            )
        self._select(dialog, "effort", efforts[0].display_name)

    def _select(self, dialog: Locator, label: str, option: str) -> None:
        button = dialog.get_by_role("button", name=label, exact=True)
        expect(button).to_be_enabled(
            timeout=self._milliseconds(self._wait_policy.feed),
        )
        button.click()
        listbox = dialog.get_by_role("listbox", name=label, exact=True)
        listbox.get_by_role("option", name=option, exact=True).click()

    def _wait_for_visible_session(
        self,
        known: frozenset[str] = frozenset(),
    ) -> SessionRef:
        self._page.wait_for_function(
            r"""known => {
                const match = /^#\/s\/([^/?#]+)$/.exec(location.hash);
                return match !== null && !known.includes(match[1]);
            }""",
            arg=list(known),
            timeout=self._milliseconds(self._wait_policy.session_announcement),
        )
        fragment = urlsplit(self._page.url).fragment
        match = SESSION_FRAGMENT.fullmatch(fragment)
        if match is None:
            raise AssertionError(f"dashboard did not open a session URL: {self._page.url}")
        return SessionRef(match.group(1))

    def _wait_for_session_url(self, session: SessionRef) -> None:
        self._page.wait_for_url(
            re.compile(rf".*/#/s/{re.escape(session.session_id)}$"),
            timeout=self._milliseconds(self._wait_policy.session_announcement),
        )

    def _record_console_error(self, console_message: ConsoleMessage) -> None:
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
        if console_message.type == "error" and not expected_network_failure:
            self._browser_failures.append(console_message.text)

    def _record_request(self, request: Request) -> None:
        self._request_paths.append(urlsplit(request.url).path)

    @staticmethod
    def _resume_catalog_requests(paths: list[str]) -> tuple[str, ...]:
        return tuple(path for path in paths if path == "/api/resumable-sessions")

    def _resume_option(self, source: SessionRef) -> Locator:
        return self._new_session_dialog().get_by_role(
            "listbox",
            name="sessions to resume",
        ).locator(f'[role="option"][data-session-id="{source.session_id}"]')

    @staticmethod
    def _form_source(form: BrowserSessionFormRef) -> SessionRef:
        if form.source is None:
            raise AssertionError("browser session form has no resume source")
        return form.source

    def _session_card(self, session: SessionRef) -> Locator:
        return self._page.locator(".scard").filter(has_text=session.session_id)

    def _workspace_group(self) -> Locator:
        return self._page.locator(".dirhead").filter(has_text=self._workspace)

    @staticmethod
    def _question(
        snapshot: SessionSnapshot,
        reference: QuestionRef,
    ) -> tuple[QuestionState, QuestionResponse]:
        states = [
            item
            for item in snapshot.questions()
            if item.attention_id == reference.attention_id
        ]
        if len(states) != 1:
            raise AssertionError(
                f"question attention {reference.attention_id!r} has {len(states)} matches"
            )
        prompts = [
            item
            for item in states[0].questions
            if item.question_id == reference.question_id
        ]
        if len(prompts) != 1:
            raise AssertionError(
                f"question {reference.question_id!r} has {len(prompts)} matches"
            )
        return states[0], prompts[0]

    @staticmethod
    def _plan(snapshot: SessionSnapshot, reference: PlanRef) -> PlanState:
        states = [
            item
            for item in snapshot.plans()
            if item.attention_id == reference.attention_id
        ]
        if len(states) != 1:
            raise AssertionError(
                f"plan attention {reference.attention_id!r} has {len(states)} matches"
            )
        return states[0]

    def _wait_for_question_resolution(self, reference: QuestionRef) -> None:
        self._client.sessions.watch(reference.session).wait(
            "browser question action to resolve",
            lambda snapshot: (
                True if not self._question(snapshot, reference)[0].pending else None
            ),
            timeout=self._wait_policy.feed,
        )

    def _wait_for_plan_resolution(self, reference: PlanRef, state: str) -> None:
        self._client.sessions.watch(reference.session).wait(
            f"browser plan action to record state {state!r}",
            lambda snapshot: (
                True if self._plan(snapshot, reference).state == state else None
            ),
            timeout=self._wait_policy.feed,
        )

    @staticmethod
    def _assert_status_color(locator: Locator, status: str) -> None:
        appearance = tab_appearance(ActorStatus(status))
        color = appearance.active_background
        expect(locator).to_have_css(
            "background-color",
            f"rgb({color.red}, {color.green}, {color.blue})",
        )

    @staticmethod
    def _assert_mixed_background(
        locator: Locator,
        variable: str,
        percentage: int,
    ) -> None:
        actual, expected = locator.evaluate(
            """(element, input) => {
              const probe = document.createElement('div');
              probe.style.background = `color-mix(in srgb, var(${input.variable}) ${input.percentage}%, transparent)`;
              element.append(probe);
              const result = [
                getComputedStyle(element).backgroundColor,
                getComputedStyle(probe).backgroundColor,
              ];
              probe.remove();
              return result;
            }""",
            {"variable": variable, "percentage": percentage},
        )
        assert actual == expected
        assert actual not in ("transparent", "rgba(0, 0, 0, 0)")

    @staticmethod
    def _milliseconds(seconds: float) -> float:
        return seconds * 1000
