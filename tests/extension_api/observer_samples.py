# Copyright (c) 2026 Zhambyl Yermagambet
"""Build observer declarations and accepted job input without feature imports."""

from typing import Literal

from baqylau_extension_api.core.conversation import TurnStarted
from baqylau_extension_api.manifest import contributions, data, metadata
from baqylau_extension_api.manifest.observers import ObserverSelection
from baqylau_extension_api.manifest.operations import QueryDefinition
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.canonical import CommittedFact, CoreFact
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.observer_jobs import ObservationJobBinding, ObservationJobRequest
from baqylau_extension_api.models.observer_results import ObservationSucceeded
from baqylau_extension_api.models.scopes import InstallationScope, SessionScope

from tests.extension_api import manifest_samples, operation_samples, samples

EVENT_TYPE = "test.sample.trigger"
ACCEPTED_AT = 42
SCOPES: data.ScopeKinds = ("installation", "session", "workspace", "repository")
SESSION_SCOPE = SessionScope(session_id="session", actor_id="actor", harness="harness")


def manifest(*, effect: Literal["read", "write"] = "read", reconciliation: bool = True) -> ExtensionManifest:
    """Declare one observer, a source decoder, a query, and a raw transformer.

    Returns:
        Data-only feature metadata with explicit observer effect policy.

    """
    schema = operation_samples.schema_definition()
    return manifest_samples.backend_manifest(operation_samples.OWNER).model_copy(update={
        "backend": metadata.BackendEntry(module="observer_backend"),
        "schemas": (schema,),
        "capabilities": ("lifecycle", "observer", "queries", "translator", "raw_transformer"),
        "contributions": contributions.Contributions(
            event_types=(data.DocumentDefinition(name=EVENT_TYPE, schema_ref=schema.reference, scopes=SCOPES),),
            source_types=(data.DocumentDefinition(
                name=operation_samples.SOURCE_TYPE, schema_ref=schema.reference, scopes=SCOPES,
            ),),
            processing=(ObserverSelection(
                scopes=SCOPES, input_types=(EVENT_TYPE, "turn.started"), effect=effect, reconciliation=reconciliation,
            ), data.ProcessingSelection(
                capability="raw_transformer", scopes=("session",), input_types=("test.record",),
            )),
            queries=(QueryDefinition(
                name=operation_samples.QUERY_ID, scopes=("installation",),
                arguments=schema.reference, result=schema.reference,
            ),),
        ),
    })


def request(encoded: str = '"complete"') -> ObservationJobRequest:
    """Name one committed trigger and one accepted observer attempt.

    Returns:
        Captured input without host-owned storage or live service objects.

    """
    binding = ObservationJobBinding(
        extension_id=operation_samples.OWNER, scope=InstallationScope(), runtime_revision=samples.RUNTIME_REVISION,
        history_revision="history-1", job_id="job-1", call_id="attempt-1", event_id="trigger-1",
    )
    return ObservationJobRequest(
        binding=binding, deadline=1000, settings_revision=0,
        event=CommittedFact(
            cursor=7, accepted_at=ACCEPTED_AT, fact=ExtensionFact(
                event_id=binding.event_id, scope=binding.scope, event_type=EVENT_TYPE,
                document=operation_samples.query_request(encoded).arguments,
            ),
        ),
    )


def succeeded() -> ObservationSucceeded:
    """Supply one output observation that retains its committed trigger.

    Returns:
        A valid complete result for boundary and schema tests.

    """
    binding = request().binding
    observation = operation_samples.observation().model_copy(update={"causes": (binding.event_id,)})
    return ObservationSucceeded(binding=binding, observations=(observation,))


def core_request() -> ObservationJobRequest:
    """Use the exact public core trigger without an extension document.

    Returns:
        A session-bound core fact with the same durable job fields.

    """
    original = request()
    binding = original.binding.model_copy(update={"scope": SESSION_SCOPE})
    fact = CoreFact(event_id=binding.event_id, scope=SESSION_SCOPE, payload=TurnStarted(prompt_message_id=None))
    event = original.event.model_copy(update={"fact": fact})
    return original.model_copy(update={"binding": binding, "event": event})
