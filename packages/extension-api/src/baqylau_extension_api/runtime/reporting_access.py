# Copyright (c) 2026 Zhambyl Yermagambet
"""Call the host observation and audit routes from a worker, and register them on the host channel."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.reporting import ExtensionAuditService, ExtensionObservationSink
from baqylau_extension_api.models.reporting import (
    DiagnosticRecord,
    DiagnosticRecorded,
    ObservationsSubmitted,
    ObservationSubmission,
)
from baqylau_extension_api.runtime import channel, codec, methods
from baqylau_extension_api.runtime.contract import RemoteCaller

SUBMITTED: TypeAdapter[ObservationsSubmitted] = TypeAdapter(ObservationsSubmitted)
RECORDED: TypeAdapter[DiagnosticRecorded] = TypeAdapter(DiagnosticRecorded)


@dataclass(frozen=True)
class RemoteObservationSink(ExtensionObservationSink):
    """Submit originals only in the live lane."""

    caller: RemoteCaller

    def submit_observations(self, observation_submission: ObservationSubmission) -> ObservationsSubmitted:
        """Submit new originals through the host.

        Returns:
            The new and repeated counts.

        """
        return self.caller.invoke_typed(methods.OBSERVATIONS_SUBMIT, observation_submission, SUBMITTED)


@dataclass(frozen=True)
class RemoteAuditService(ExtensionAuditService):
    """Record diagnostics only in the live lane."""

    caller: RemoteCaller

    def record_diagnostic(self, diagnostic_record: DiagnosticRecord) -> DiagnosticRecorded:
        """Record one diagnostic through the host.

        Returns:
            The confirmation.

        """
        return self.caller.invoke_typed(methods.AUDIT_RECORD, diagnostic_record, RECORDED)


def register_reporting_access(
    rpc: channel.RpcChannel, sink: ExtensionObservationSink | None, audit: ExtensionAuditService | None,
) -> None:
    """Register the host observation and audit callbacks that one worker connection has."""
    if sink is not None:
        sink_handler = codec.ModelHandler(ObservationSubmission, SUBMITTED, sink.submit_observations)
        rpc.register(methods.OBSERVATIONS_SUBMIT, sink_handler, "live")
    if audit is not None:
        audit_handler = codec.ModelHandler(DiagnosticRecord, RECORDED, audit.record_diagnostic)
        rpc.register(methods.AUDIT_RECORD, audit_handler, "live")
