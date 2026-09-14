# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test architecture api."""

from __future__ import annotations

from tests import (
    architecture_packages,
    architecture_project_dependencies as project_dependencies,
    architecture_standard_dependencies as standard_dependencies,
)

# Keep dependency modules separate from architecture checks.
# isort: split

from tests import (
    architecture_test_declarations,
    architecture_test_imports,
    architecture_test_json,
    architecture_test_layers,
    architecture_test_providers,
    architecture_test_routes,
)

ROOT = project_dependencies.Path(__file__).resolve().parents[1]
DOMAIN_EVENTS = standard_dependencies.importlib.import_module("domain.events")
TEXT_ENCODING = "utf-8"
PYTHON_FILE_PATTERN = "*.py"
IMPLEMENTATION_DIRECTORY_NAME = "impl"
HARNESS_PACKAGE = "harness"
HARNESS_ROOT = ROOT / HARNESS_PACKAGE
HARNESS_IMPLEMENTATION_ROOT = HARNESS_ROOT / IMPLEMENTATION_DIRECTORY_NAME
CLAUDE_CODE_PACKAGE = "claude_code"
DASHBOARD_CLI_PATH = "dashboard/cli.py"
CODEX_PACKAGE = "codex"
OUR_PACKAGES = architecture_packages.owned_packages()
ACTORS_REGISTRY = "actors"
FOLDED_REGISTRY = frozenset(("folded",))


def test_json_allowlist_is_only_foreign_documents() -> None:
    """`json` is banned tree-wide, and every exemption is still needed.

    A document of OURS is a dataclass or a pydantic model; something else turns
    it into bytes. Building one as a dict literal is what let the canonical
    envelope's twelve field names live in four places at once, and what let a
    route answer with a shape its own `response_model` did not describe.

    ruff's TID251 does the banning (ruff.toml), which puts the failure on the
    line that did it. This holds the exemption list to files that STILL need
    one: an entry for a file that no longer touches json is an entry that would
    silently cover the next hand-built document written there.
    """
    configuration = (ROOT / "ruff.toml").read_text(encoding=TEXT_ENCODING)
    exempt = [
        line.split('"')[1]
        for line in configuration.splitlines()
        if line.startswith('"') and "TID251" in line and ("=" in line)
    ]
    assert exempt, "the allowlist parsed as empty; this test would pass vacuously"
    stale = architecture_test_imports.stale_json_exemptions(exempt)
    assert not stale, f"exempt from the json ban and no longer using it: {stale}"
    covered = {exempt_path for exempt_pattern in exempt for exempt_path in ROOT.glob(exempt_pattern)}
    offenders = sorted(

            str(path.relative_to(ROOT))
            for package in OUR_PACKAGES
            for path in (ROOT / package).rglob(PYTHON_FILE_PATTERN)
            if path not in covered and architecture_test_routes.calls_json(path)

    )
    assert not offenders


def test_harness_adapters_never_use_raw_json() -> None:
    """Foreign JSON crosses an adapter boundary through a typed Pydantic model.

    Importing the stdlib codec is forbidden here, rather than allowlisted: a
    direct ``json.load(s)`` creates an untyped intermediate before validation,
    while ``model_validate_json`` validates bytes/text as it decodes them and
    ``model_dump_json`` serializes a declared model directly.
    """
    raw_json_violations = [
        violation
        for path in sorted(HARNESS_IMPLEMENTATION_ROOT.rglob(PYTHON_FILE_PATTERN))
        for violation in architecture_test_providers.raw_json_violations(path)
    ]
    assert not raw_json_violations, architecture_test_layers.listed_failure_message(
        "raw JSON codec use in harness adapters", raw_json_violations,
    )


def test_owned_packages_never_use_raw() -> None:
    """Documents and intermediate records must have declared shapes.

    Dictionaries are allowed only for the exact typed registry/index symbols
    below, where keyed dynamic lookup is the data structure's actual behavior.
    The exemption is symbol-level, never file-level: payloads and intermediate
    records in the same modules still have to be dataclasses or Pydantic models.
    """
    typed_registry_allowlist = {
        "api/application/file_dictation_route.py": {"DICTATION_RESPONSES"},
        "api/application/file_upload_route.py": {"UPLOAD_RESPONSES"},
        "api/application/static.py": {"router"},
        "api/application/static_delivery.py": {"headers"},
        "api/config.py": {"SECURITY_HEADERS"},
        "api/controls/control_responses.py": {
            "CONTROL_RESPONSES",
            "CONTROL_STATUS",
            "LAUNCH_RESPONSES",
            "LAUNCH_STATUS",
        },
        "api/hooks/routes.py": {"HOOK_RESPONSES"},
        "api/middleware.py": {"headers"},
        "api/openapi_document.py": {"FrameworkDocument"},
        "api/responses.py": {"Documented", "EVERY_ROUTE", "documented", "statuses"},
        "api/runtime.py": {"base_environment", "environment"},
        "api/sse.py": {"NO_STORE"},
        "api/telemetry/models/browser_events_request.py": {"connection", "details"},
        "api/terminal/panes.py": {"PANE_RESPONSES"},
        "app/injection.py": {"Instances", "dependencies", "instances"},
        "app/provider_session_writers.py": {"display_by_harness"},
        "app/provider_translators.py": {"core_translators"},
        "audit/failures.py": {"_states"},
        "audit/record.py": {"_recorders"},
        "client/_daemon_exchange.py": {"request_headers"},
        "client/_handoff.py": {"published_targets"},
        "client/_http.py": {"PANE_COMMAND_PATHS"},
        "client/_model_session.py": {"_entries", "_shells", ACTORS_REGISTRY},
        "client/_model_session_feed.py": {"ATTENTION_TWINS", "entries"},
        "client/_model_session_state.py": {"_entries", "_shells", ACTORS_REGISTRY},
        "client/_pane_rendering.py": {"_published"},
        "client/_render_compose.py": {"copy_targets", "targets"},
        "client/_render_statistics.py": {"_statistic_totals", "tools", "totals"},
        "client/_render_styles.py": {"FILE_VERBS", "PLAN_DECISIONS", "TASK_MARKERS"},
        "client/claude_hook.py": {"reply"},
        "client/claude_otel.py": {"TELEMETRY_HEADERS"},
        "client/codex_hook.py": {"reply"},
        "client/terminal_pane.py": {"_published"},
        "core/input_events.py": {"_watches"},
        "core/kernel_events.py": {"_files"},
        "core/process_subscriptions.py": {"_descriptors"},
        "core/work_queue.py": {"_deadlines"},
        "dashboard/cli.py": {"command"},
        "dashboard/cli_models.py": {"variables"},
        "dashboard/cli_option_parser.py": {"parsed"},
        "dashboard/cli_option_values.py": {"LAUNCH_VARIABLES"},
        "dashboard/config.py": {"STATIC"},
        "dashboard/dictation_credentials.py": {"request_headers"},
        "dashboard/services/preference_reads.py": {"hidden_directories"},
        "dashboard/services/preference_writes.py": {"hide_directory"},
        "dashboard/services/terminal_drafts.py": {"_terminal_text"},
        "dashboard/services/workspace_attention.py": {"pending_questions"},
        "domain/entries.py": {"BODY_TYPES", "ENTRY_TYPES", "open_attentions"},
        "domain/events.py": {"EVENT_TYPES", "PAYLOAD_TYPES"},
        "engine/interpret/control_translator.py": {"translator_by_source"},
        "engine/interpret/liveness.py": {"_terminal_owners"},
        "engine/interpret/puller.py": {"identities"},
        "engine/react/content_checks.py": {"EMPTY_BODY_SUSPECT"},
        "engine/react/loop_materialization.py": {ACTORS_REGISTRY, "known", "states"},
        "engine/react/loop_runtime.py": {"states"},
        "engine/sessiondata/actor_batch.py": {ACTORS_REGISTRY, "current"},
        "engine/sessiondata/actor_identity.py": {"finished_actors"},
        "engine/sessiondata/actor_statistics_support.py": {"FILE_TOOLS", "counts"},
        "engine/sessiondata/actor_status_work.py": {"idle_actors"},
        "engine/sessiondata/contract.py": {ACTORS_REGISTRY, "merged"},
        "engine/sessiondata/naming.py": {"EMPTY_DISPLAY_BY_HARNESS"},
        "engine/sessiondata/session_tasks.py": {"known"},
        "harness/impl/claude_code/canonical/idle_event_source.py": {"_notification_positions", "positions"},
        "harness/impl/claude_code/canonical/message_background_values.py": {"BACKGROUND_OUTCOMES"},
        "harness/impl/claude_code/canonical/message_idle_events.py": {"_notifications_by_actor", "notifications"},
        "harness/impl/claude_code/canonical/tool_browser_values.py": {"CHROME_ACTIONS"},
        "harness/impl/claude_code/canonical/tool_kind_values.py": {"FILE_ACTIONS", "TOOL_KINDS"},
        "harness/impl/claude_code/canonical/toolcall_state_stages.py": {
            "agent_assignments",
            "background_tasks",
            "calls",
            "monitors",
        },
        "harness/impl/claude_code/canonical/transcript_turn_scan.py": {"_turn_ancestry", "parents"},
        "harness/impl/claude_code/canonical/translator.py": {"_pending_compactions", "translator_by_source"},
        "harness/impl/claude_code/canonical/turns.py": {"_response_turns"},
        "harness/impl/claude_code/catalog.py": {"COMMAND_PROMPT_FLOORS"},
        "harness/impl/claude_code/controls/controller_decision_handlers.py": {"DECISION_HANDLERS"},
        "harness/impl/claude_code/controls/controller_handler_registry.py": {"HANDLERS"},
        "harness/impl/claude_code/controls/controller_session_handlers.py": {"SESSION_HANDLERS"},
        "harness/impl/claude_code/model_names.py": {"ALIAS_DISPLAY"},
        "harness/impl/claude_code/usage/windows.py": {"samples"},
        "harness/impl/codex/canonical/events.py": {"EVENTS"},
        "harness/impl/codex/canonical/item_responses.py": {"RESPONSES"},
        "harness/impl/codex/canonical/record_collaboration_registry.py": {"COLLABORATION_ARGUMENTS"},
        "harness/impl/codex/canonical/record_item_registry.py": {"ITEM_COMPLETED_ITEMS"},
        "harness/impl/codex/canonical/rollout_toplevel.py": {"TOP_LEVEL_DOCUMENTS"},
        "harness/impl/codex/canonical/source_cache.py": {
            "child_sources",
            "parent_by_path",
            "without_removed_parents",
            "without_removed_sources",
        },
        "harness/impl/codex/canonical/source_catalog.py": {"_directories", "_rollouts"},
        "harness/impl/codex/canonical/source_groups.py": {"_paths_by_parent", "grouped_paths", "parent_by_path"},
        "harness/impl/codex/canonical/sources.py": {"_child_parent_by_path", "_child_sources", "_sessions"},
        "harness/impl/codex/canonical/translator_core_values.py": {
            "ACTIVITY_CALLS",
            "CODEX_TOOLS",
            "FILE_ACTIONS",
            "GOAL_STATES",
        },
        "harness/impl/codex/canonical/translator_lifecycle_start.py": {
            "_active_turns",
            "_call_records",
            "_collaboration_calls",
            "_compactions",
            "_continuation_shells",
            "_goals",
            "_mcp_tool_outcomes",
            "_plan_tasks",
            "_process_shells",
            "_sources_by_session",
            "_working_directories",
            "result_calls",
        },
        "harness/impl/codex/canonical/translator_tool_models.py": {"direct_value", "requests_by_name", "string_fields"},
        "harness/impl/codex/continuity.py": {"_pending_by_window", "_resolved_by_session"},
        "harness/impl/codex/controls/controller_builder.py": {"handlers"},
        "harness/impl/opencode2/actors.py": {"OUTCOMES"},
        "harness/impl/opencode2/controls.py": {"HANDLERS"},
        "harness/impl/opencode2/launch_variant.py": {"variant"},
        "harness/impl/opencode2/permissions.py": {"CHOICES"},
        "harness/impl/codex/controls/controller_conversation_handlers.py": {"CONVERSATION_HANDLERS"},
        "harness/impl/codex/controls/controller_decision_handlers.py": {"DECISION_HANDLERS"},
        "harness/impl/codex/controls/controller_handler_registry.py": {"HANDLERS"},
        "harness/impl/codex/controls/modeldialog_steps.py": {"EFFORT_LABEL"},
        "harness/impl/codex/usage.py": {"subprocess_environment"},
        "harness/impl/codex/usage_process.py": {"environment"},
        "harness/impl/codex/usage_rows.py": {"WINDOW_LABELS"},
        "harness/models/interrupts.py": {"_marked_at"},
        "harness/models/selections.py": {"SelectionStates", "_efforts", "_models", "remaining_states"},
        "harness/registry.py": {"_plugins"},
        "harness/runtime.py": {"by_harness"},
        "harness/services/open_session_work.py": {"assignments", "shells", "turns"},
        "harness/services/terminal_gate.py": {"_locks"},
        "notify/channels/webpush_delivery.py": {"request_headers"},
        "notify/notifier_models.py": {"NOTIFICATION_KINDS"},
        "notify/notifier_processing.py": {
            "_resolve_change",
            "alertable_by_session",
            "current_states",
            "delivered",
            "pending",
            "previous_states",
            "states",
        },
        "notify/presence.py": {"viewing"},
        "repository/impl/sqlite/audit_read.py": {"counts", "error_counts"},
        "repository/impl/sqlite/connection.py": {"EMPTY_MIGRATIONS"},
        "repository/impl/sqlite/raw_event_audits.py": {"by_raw_event"},
        "repository/impl/sqlite/raw_events.py": {"latest_positions"},
        "repository/impl/sqlite/schema.py": {"MAIN_MIGRATIONS", "migrations"},
        "repository/impl/sqlite/session_data.py": {"leads"},
        "repository/impl/sqlite/session_data_aggregate.py": {"actors_by_session", "newest_by_session"},
        "repository/mapper/workspace.py": {"by_prompt"},
        "sdk/client_catalog_resources.py": {"query"},
        "sdk/client_service_resources.py": {"headers", "query"},
        "sdk/client_session_snapshots.py": {"query_parameters"},
        "sdk/state_assignments.py": FOLDED_REGISTRY,
        "sdk/state_compactions.py": {"open_by_actor"},
        "sdk/state_plans.py": FOLDED_REGISTRY,
        "sdk/state_questions.py": FOLDED_REGISTRY,
        "sdk/state_shells.py": FOLDED_REGISTRY,
        "sdk/state_skills.py": FOLDED_REGISTRY,
        "sdk/transport.py": {"JSON_HEADERS"},
        "terminal/adapter.py": {
            "_open_activity_pane",
            "_open_scoreboard_pane",
            "cleared_tags",
            "on_screen",
            "outcomes",
        },
        "terminal/impl/kitty/metadata.py": {"_window_info"},
        "terminal/impl/kitty/remote.py": {"_socket_directories"},
        "terminal/impl/kitty/remote_commands.py": {"colors"},
        "terminal/impl/kitty/remote_tree.py": {"user_vars"},
        "terminal/impl/kitty/tabs.py": {"colors"},
        "terminal/impl/pty/keys.py": {"NAMED_KEYS"},
        "terminal/impl/pty/registry.py": {"child_environment", "environment", "launch_environment", "windows"},
        "terminal/impl/pty/runtime.py": {"found_identities", "identities", "observed"},
        "terminal/impl/pty/window.py": {"descendant_identities", "tags"},
        "terminal/processes.py": {"_children", "children"},
        "terminal/tabs.py": {"_painted"},
        "terminal/theme.py": {"TAB_APPEARANCES"},
    }
    raw_dictionary_violations = [
        violation
        for path in architecture_test_imports.owned_python_files()
        for violation in architecture_test_declarations.raw_dictionary_violations(path, typed_registry_allowlist)
    ]
    assert not raw_dictionary_violations, architecture_test_layers.listed_failure_message(
        "raw dictionaries in owned packages", raw_dictionary_violations,
    )


def test_no_canon_payload_carries_presentation() -> None:
    """A canonical fact says what HAPPENED; how it is drawn is the renderers'.

    A payload that grew an `html` or an `ansi` field would put one surface's
    styling into the store every other surface reads, permanently — the
    canonical schema is append-only, so the field could never be taken back out.

    This is a property of twelve dataclass DECLARATIONS, and it used to be
    checked by a loop over every registered payload, re-run on every stored
    event ever built, to answer a question that cannot change while the
    process is running. It belongs in the suite that reads the tree, and this
    is that suite.
    """
    forbidden = frozenset((
        "ansi",
        "bubbled",
        "chrome",
        "css",
        "glyph",
        "gutter",
        "html",
        "note",
        "rgb",
        "web",
        "wrap",
    ))
    carrying = [
        f"{payload_type.__name__} carries {sorted(found)!r}"
        for payload_type in DOMAIN_EVENTS.EVENT_TYPES
        if (
            found := forbidden.intersection(
                field.name for field in standard_dependencies.dataclasses.fields(payload_type)
            )
        )
    ]
    assert not carrying


def test_claude_otel_is_not_a_top_level_harness() -> None:
    """OTLP is one harness's side channel, not a harness.

    What an export MEANS stays under the plugin that reports it; the endpoint that
    receives one is a client (`client/claude_otel.py`), spawned by the launcher
    beside that gateway.
    """
    assert not (HARNESS_IMPLEMENTATION_ROOT / "otel").exists()
    assert (HARNESS_IMPLEMENTATION_ROOT / CLAUDE_CODE_PACKAGE / "otel" / "gateway.py").is_file()
    assert (HARNESS_IMPLEMENTATION_ROOT / CLAUDE_CODE_PACKAGE / "otel" / "launch.py").is_file()


def test_canon_shared_code_imports_no_concrete() -> None:
    """Verify canonical shared code imports no concrete harness package."""
    assert not architecture_test_providers.concrete_harness_importers()


def test_harness_plugins_do_not_import_each_other() -> None:
    """Verify harness plugins do not import each other."""
    forbidden_imports = {CLAUDE_CODE_PACKAGE: "harness.impl.codex", CODEX_PACKAGE: "harness.impl.claude_code"}
    importers = [
        importer
        for package_name, forbidden_prefix in forbidden_imports.items()
        for importer in architecture_test_json.cross_plugin_importers(package_name, forbidden_prefix)
    ]
    assert not importers
