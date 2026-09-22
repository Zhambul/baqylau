# Copyright (c) 2026 Zhambyl Yermagambet
"""Register only the typed capabilities provided by the loaded package."""

from pydantic import TypeAdapter

from baqylau_extension_api.models.lifecycle import (
    ActivationRequest,
    ActivationResult,
    DeactivationRequest,
    DeactivationResult,
)
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import (
    CanonicalTransformRequest,
    CanonicalTransformResult,
    RawTransformRequest,
)
from baqylau_extension_api.runtime import (
    commands,
    methods,
    presentation,
    projection,
    projection_transforms,
    queries,
    sources,
    translation,
)
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.dispatch import WorkerDispatch
from baqylau_extension_api.runtime.migrations import register_migrations
from baqylau_extension_api.runtime.observers import register_observer
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet


def register_capabilities(channel: RpcChannel, dispatch: WorkerDispatch, request: WorkerLoadRequest) -> None:
    """Bind typed methods without importing feature modules into the host."""
    schemas = SchemaSet((*request.manifest.schemas, *request.peer_schemas))
    _register_lifecycle(channel, dispatch)
    _register_sources(channel, dispatch, request, schemas)
    if dispatch.capabilities.raw_transformer is not None:
        channel.register(methods.RAW_TRANSFORM, ModelHandler(
            RawTransformRequest, TypeAdapter(RawTransformResult), dispatch.transform_raw,
        ), "pure")
    if dispatch.capabilities.canonical_transformer is not None:
        channel.register(methods.CANONICAL_TRANSFORM, ModelHandler(
            CanonicalTransformRequest, TypeAdapter(CanonicalTransformResult), dispatch.transform_canonical,
        ), "pure")
    _register_projection(channel, dispatch, request, schemas)
    _register_operations(channel, dispatch, request, schemas)


def _register_operations(
    channel: RpcChannel, dispatch: WorkerDispatch, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    if dispatch.capabilities.terminal is not None:
        presentation.register_terminal(channel, dispatch.capabilities.terminal, request, schemas)
    if dispatch.capabilities.queries is not None:
        queries.register_queries(channel, dispatch.capabilities.queries, request, schemas)
    if dispatch.capabilities.commands is not None:
        commands.register_commands(channel, dispatch.capabilities.commands, request, schemas)
    if dispatch.capabilities.migrations is not None:
        register_migrations(channel, dispatch.capabilities.migrations, request, schemas)
    if dispatch.capabilities.observer is not None:
        register_observer(channel, dispatch.capabilities.observer, request, schemas)


def _register_lifecycle(channel: RpcChannel, dispatch: WorkerDispatch) -> None:
    channel.register(methods.ACTIVATE, ModelHandler(
        ActivationRequest, TypeAdapter(ActivationResult), dispatch.activate_package,
    ), "live")
    channel.register(methods.DEACTIVATE, ModelHandler(
        DeactivationRequest, TypeAdapter(DeactivationResult), dispatch.deactivate_package,
    ), "live")


def _register_sources(
    channel: RpcChannel, dispatch: WorkerDispatch, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    if dispatch.capabilities.sources is not None:
        sources.register_sources(channel, dispatch.capabilities.sources, request, schemas)
    if dispatch.capabilities.translator is not None:
        translation.register_translator(channel, dispatch.capabilities.translator, request, schemas)


def _register_projection(
    channel: RpcChannel, dispatch: WorkerDispatch, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    if dispatch.capabilities.projector is not None:
        projection.register_projector(channel, dispatch.capabilities.projector, request, schemas)
    if dispatch.capabilities.projection_transformer is not None:
        projection_transforms.register_projection_transformer(
            channel, dispatch.capabilities.projection_transformer, request, schemas,
        )
