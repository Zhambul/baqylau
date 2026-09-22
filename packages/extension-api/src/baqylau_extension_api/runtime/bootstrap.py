# Copyright (c) 2026 Zhambyl Yermagambet
"""Load one package while the reader remains available for factory callbacks."""

import asyncio
from collections.abc import Mapping
from pathlib import Path

from baqylau_extension_api.runtime import host_context, loading, package_loading
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.codec import encode_parameters
from baqylau_extension_api.runtime.contract import AsyncReceiver, RemoteCaller
from baqylau_extension_api.runtime.dispatch import WorkerDispatch
from baqylau_extension_api.runtime.models import ExtensionTransportError, RpcEnvelope
from baqylau_extension_api.runtime.registration import register_capabilities
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest, WorkerReady
from baqylau_extension_api.runtime.worker_services import worker_services


class WorkerBootstrap(AsyncReceiver):
    """Allow one load attempt in each new worker process."""

    def __init__(self, channel: RpcChannel, caller: RemoteCaller, runtime_revision: str, directory: Path) -> None:
        """Bind the worker to its host-selected revision and callback channel."""
        self._channel = channel
        self._caller = caller
        self._revision = runtime_revision
        self._directory = directory
        self._attempted = False

    async def receive(self, packet: Mapping[str, str]) -> Mapping[str, str]:
        """Validate the declaration before the first feature import.

        Returns:
            The ready reply after typed capability registration.

        Raises:
            ExtensionTransportError: If loading was attempted or a revision is invalid.

        """
        envelope = RpcEnvelope.model_validate(packet)
        request = WorkerLoadRequest.model_validate_json(envelope.json_text)
        if self._attempted or envelope.runtime_revision != self._revision:
            message = "worker load was already attempted or used a stale revision"
            raise ExtensionTransportError(message)
        if request.environment.runtime_revision != self._revision:
            message = "worker environment has a stale runtime revision"
            raise ExtensionTransportError(message)
        self._attempted = True
        with host_context.host_call_scope(envelope.host_call_id):
            plugin = await asyncio.to_thread(
                package_loading.load_package_backend, request, worker_services(request, self._caller), self._directory,
            )
        register_capabilities(self._channel, WorkerDispatch(request.environment, plugin.capabilities), request)
        ready = WorkerReady(
            extension_info=plugin.extension_info, capabilities=loading.capability_names(plugin.capabilities),
        )
        return encode_parameters(RpcEnvelope(
            runtime_revision=self._revision, json_text=ready.model_dump_json(), host_call_id=envelope.host_call_id,
        ))
