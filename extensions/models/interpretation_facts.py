# Copyright (c) 2026 Zhambyl Yermagambet
"""Share pure fact checks between worker result handling and storage acceptance."""

from baqylau_extension_api.manifest.lookup import event_definition
from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.operations.documents import require_document_schema

from extensions.models.interpretation_context import InterpretationContext
from extensions.models.observations import ExtensionObservation


def validate_fact(context: InterpretationContext, fact: CanonicalFact) -> None:
    """Require the original scope and either strict core identity or a declared owned document.

    Raises:
        ValueError: If the fact changes scope or core source identity.

    """
    if fact.scope != context.binding.scope:
        message = "canonical fact changed its original scope"
        raise ValueError(message)
    if isinstance(fact, CoreFact):
        _validate_core(context, fact)
    else:
        package = context.require_package(fact.document.schema_ref.owner)
        declaration = event_definition(package.manifest, fact.event_type, fact.scope.kind)
        require_document_schema(fact.document, declaration.schema_ref, package.schemas, "canonical fact")


def _validate_core(context: InterpretationContext, fact: CoreFact) -> None:
    original = context.original.observation
    parent_actor_id = None if isinstance(original, ExtensionObservation) else original.parent_actor_id
    if fact.parent_actor_id != parent_actor_id or fact.raw_event_ids != (context.binding.raw_event_id,):
        message = "core canonical fact changed its original parent or raw source references"
        raise ValueError(message)


def cause_graph(proposed: tuple[CanonicalFact, ...]) -> dict[str, set[str]]:
    """Keep cause edges from every proposal, including suppressed intermediate facts.

    Returns:
        The complete cause graph for cycle and existence checks.

    """
    graph: dict[str, set[str]] = {}
    for fact in proposed:
        if isinstance(fact, ExtensionFact):
            graph.setdefault(fact.event_id, set()).update(fact.causes)
    return graph


def require_same_identity(accepted: CanonicalFact, proposed: CanonicalFact) -> None:
    """Keep first acceptance without allowing another owner or scope to claim its ID.

    Raises:
        ValueError: If logical identity changes its branch, scope, or document owner.

    """
    if accepted.kind != proposed.kind or accepted.scope != proposed.scope:
        message = "canonical identity belongs to another scope or fact kind"
        raise ValueError(message)
    if (isinstance(accepted, ExtensionFact) and isinstance(proposed, ExtensionFact)
            and accepted.document.schema_ref.owner != proposed.document.schema_ref.owner):
        message = "canonical identity belongs to another extension owner"
        raise ValueError(message)
