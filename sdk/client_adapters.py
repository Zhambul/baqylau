# Copyright (c) 2026 Zhambyl Yermagambet
"""Define SDK response adapters and time limits."""

from pydantic import TypeAdapter

from sdk import application_models, control_models

HEALTH = TypeAdapter(application_models.health_response.HealthResponse)
LAUNCH = TypeAdapter(control_models.launch_response.LaunchResponse)
CONTROL: TypeAdapter[control_models.control_outcome_response.ControlOutcomeResponse] = TypeAdapter(
    control_models.control_outcome_response.ControlOutcomeResponse,
)
SESSION_LIST = TypeAdapter(application_models.session_data.SessionDataListResponse)
SESSION_DATA = TypeAdapter(application_models.session_data.SessionDataResponse)
ENTRY_PAGE = TypeAdapter(application_models.entry.EntryPageResponse)
APPLICATION = TypeAdapter(application_models.global_application_response.GlobalApplicationResponse)
SESSION_APPLICATION = TypeAdapter(application_models.session_application_response.SessionApplicationResponse)
HARNESS_LIST = TypeAdapter(tuple[application_models.harness_description_response.HarnessDescriptionResponse, ...])
HARNESS_CATALOG = TypeAdapter(application_models.harness_catalog_response.HarnessCatalogResponse)
INSIGHTS = TypeAdapter(application_models.application_insights_response.ApplicationInsightsResponse)
RESUMABLE_SESSIONS = TypeAdapter(tuple[application_models.resumable_session_response.ResumableSessionResponse, ...])
UPLOAD = TypeAdapter(application_models.upload_response.UploadResponse)
SAVED = TypeAdapter(application_models.saved_response.SavedResponse)
DIAGNOSTICS_CHECKPOINT = TypeAdapter(application_models.models.DiagnosticsCheckpointResponse)
DIAGNOSTICS_REPORT = TypeAdapter(application_models.models.DiagnosticsReportResponse)
TERMINAL_DIAGNOSTICS = TypeAdapter(application_models.models.TerminalDiagnosticsResponse)
RAW_EVENT_AUDIT = TypeAdapter(application_models.raw_event_audit_models.RawEventAuditResponse)
ERROR_FRAME = TypeAdapter(application_models.error_frame.ErrorFrame)
SESSION_STREAM = TypeAdapter(application_models.stream_frame.SessionStreamFrame)
GLOBAL_STREAM = TypeAdapter(application_models.stream_frame.GlobalStreamFrame)
PANE_COMMAND = TypeAdapter(application_models.pane_command_response.PaneCommandResponse)
AUTOMATIC_NAME_TIMEOUT_SECONDS = 120.0
CONTROL_TIMEOUT_SECONDS = 60.0
LAUNCH_TIMEOUT_SECONDS = 35.0
