# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide slow and bidirectional handlers for transport conformance tests."""

from dataclasses import dataclass
from threading import Event

from baqylau_extension_api.runtime.context import require_live_call
from baqylau_extension_api.runtime.contract import EncodedHandler, RemoteCaller
from pydantic import TypeAdapter

from tests.extension_api.rpc_samples import EchoRequest


@dataclass(frozen=True)
class CallbackEcho:
    """Read a host response from a synchronous worker capability."""

    caller: RemoteCaller

    def echo(self, request: EchoRequest) -> EchoRequest:
        """Make a host callback while the original host call is pending.

        Returns:
            The host callback's validated result.

        """
        return self.caller.invoke_typed("host.echo", request, TypeAdapter(EchoRequest))


@dataclass(frozen=True)
class WaitingCall(EncodedHandler):
    """Hold a live thread until the test releases it."""

    entered: Event
    release: Event

    def invoke(self, encoded: str) -> str:
        """Keep a slow call active while other requests complete.

        Returns:
            The same encoded request after release.

        Raises:
            TimeoutError: If the test does not release the work.

        """
        self.entered.set()
        if not self.release.wait(timeout=5):
            message = "test did not release the slow handler"
            raise TimeoutError(message)
        return encoded


class ImmediateCall(EncodedHandler):
    """Return live work immediately to check reserved cancellation capacity."""

    def invoke(self, encoded: str) -> str:
        """Check live-call access on the control thread.

        Returns:
            The same request bytes.

        """
        require_live_call()
        return encoded


def waiting_call(release: Event) -> WaitingCall:
    """Create one independently observed call with a shared release signal.

    Returns:
        A live-work fixture with its own start signal.

    """
    return WaitingCall(Event(), release)


def forbidden_service(request: EchoRequest) -> EchoRequest:
    """Try a live service guard from a pure capability.

    Returns:
        The input only when the caller is allowed to use live services.

    """
    require_live_call()
    return request
