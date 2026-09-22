# Copyright (c) 2026 Zhambyl Yermagambet
"""Call terminal presenters through the typed process boundary."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.presentation import ExtensionTerminalPresenter
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.runtime import methods
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.terminal import actions, registration, validation
from baqylau_extension_api.terminal.models import TerminalView, TerminalViewRequest


@dataclass(frozen=True)
class RemoteTerminalPresenter(ExtensionTerminalPresenter):
    """Use data-only terminal presentation without importing feature code."""

    caller: RemoteCaller

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """Check a worker result against its exact requested view binding.

        Returns:
            A bounded terminal view with valid local references.

        """
        response = self.caller.invoke_typed(methods.TERMINAL_PRESENT, terminal_request, TypeAdapter(TerminalView))
        return validation.validate_terminal_view(terminal_request, response)


@dataclass(frozen=True)
class WorkerTerminalPresenter(ExtensionTerminalPresenter):
    """Validate declarations before and after the extension's pure layout call."""

    presenter: ExtensionTerminalPresenter
    load: WorkerLoadRequest
    schemas: SchemaSet

    def present(self, terminal_request: TerminalViewRequest) -> TerminalView:
        """Keep undeclared views and command actions out of client output.

        Returns:
            A checked extension-owned layout.

        Raises:
            ExtensionContractError: If the request has a stale runtime revision.

        """
        request = TerminalViewRequest.model_validate(terminal_request)
        if request.binding.runtime_revision != self.load.environment.runtime_revision:
            message = "terminal request has a stale runtime revision"
            raise ExtensionContractError(message)
        registration.validate_presentation_request(self.load.manifest, self.schemas, request)
        response = validation.validate_terminal_view(request, self.presenter.present(request))
        actions.validate_presentation_actions(self.load.manifest, self.schemas, response)
        return response


def register_terminal(
    channel: RpcChannel, presenter: ExtensionTerminalPresenter, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Keep layout work in the pure lane with a fixed schema snapshot."""
    bound = WorkerTerminalPresenter(presenter, request, schemas)
    channel.register(methods.TERMINAL_PRESENT, ModelHandler(
        TerminalViewRequest, TypeAdapter(TerminalView), bound.present,
    ), "pure")
