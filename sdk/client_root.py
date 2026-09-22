# Copyright (c) 2026 Zhambyl Yermagambet
"""Split SDK client implementation."""

from __future__ import annotations

from sdk import transport
from sdk.client_application import ApplicationResource
from sdk.client_catalog_resources import (
    HarnessesResource,
    InsightsResource,
    UploadsResource,
)
from sdk.client_extension_catalog import ExtensionsResource
from sdk.client_preferences import PreferencesResource
from sdk.client_service_resources import (
    DiagnosticsResource,
    StreamsResource,
    TerminalResource,
    UsageResource,
)
from sdk.client_session_actions import SessionsResource


class _ApplicationClientResources:
    """Expose application and session resources."""

    _application: ApplicationResource
    _sessions: SessionsResource
    _harnesses: HarnessesResource
    _insights: InsightsResource
    _uploads: UploadsResource
    _preferences: PreferencesResource

    @property
    def application(self) -> ApplicationResource:
        """The application resource."""
        return self._application

    @property
    def sessions(self) -> SessionsResource:
        """The sessions resource."""
        return self._sessions

    @property
    def harnesses(self) -> HarnessesResource:
        """The harness catalog resource."""
        return self._harnesses

    @property
    def insights(self) -> InsightsResource:
        """The insights resource."""
        return self._insights

    @property
    def uploads(self) -> UploadsResource:
        """The uploads resource."""
        return self._uploads

    @property
    def preferences(self) -> PreferencesResource:
        """The preferences resource."""
        return self._preferences


class _RuntimeClientResources:
    """Expose runtime and diagnostic resources."""

    _transport: transport.HttpTransport
    _usage: UsageResource
    _terminal: TerminalResource
    _streams: StreamsResource
    _diagnostics: DiagnosticsResource
    _extensions: ExtensionsResource

    @property
    def transport(self) -> transport.HttpTransport:
        """The HTTP transport."""
        return self._transport

    @property
    def usage(self) -> UsageResource:
        """The usage resource."""
        return self._usage

    @property
    def terminal(self) -> TerminalResource:
        """The terminal resource."""
        return self._terminal

    @property
    def streams(self) -> StreamsResource:
        """The stream resource."""
        return self._streams

    @property
    def diagnostics(self) -> DiagnosticsResource:
        """The diagnostics resource."""
        return self._diagnostics

    @property
    def extensions(self) -> ExtensionsResource:
        """The extension catalog resource."""
        return self._extensions


class BaqylauClient(_ApplicationClientResources, _RuntimeClientResources):
    """Represent baqylau client."""

    def __init__(self, base_url: str) -> None:
        """Initialize the Baqylau client."""
        self._transport = transport.HttpTransport(base_url)
        self._application = ApplicationResource(self._transport)
        self._sessions = SessionsResource(self._transport)
        self._harnesses = HarnessesResource(self._transport)
        self._insights = InsightsResource(self._transport)
        self._uploads = UploadsResource(self._transport)
        self._preferences = PreferencesResource(self._transport, self._application)
        self._usage = UsageResource(self._application)
        self._terminal = TerminalResource(self._transport)
        self._streams = StreamsResource(self._transport)
        self._diagnostics = DiagnosticsResource(self._transport)
        self._extensions = ExtensionsResource(self._transport)

    def close(self) -> None:
        """Close close."""
        self._transport.close()
