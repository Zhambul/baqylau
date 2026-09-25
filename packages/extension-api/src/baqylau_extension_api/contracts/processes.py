# Copyright (c) 2026 Zhambyl Yermagambet
"""Run declared programs and call the user's models through the host."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from baqylau_extension_api.models.inference import InferenceRequest, InferenceResult
    from baqylau_extension_api.models.processes import ProcessRequest, ProcessResult


class ExtensionProcessService(Protocol):
    """Run one program that the package manifest declares, with an argument array and no shell."""

    def run_process(self, process_request: ProcessRequest) -> ProcessResult:
        """Run the program and return its exit code and bounded output, or the failed bound."""
        ...


class ExtensionInferenceService(Protocol):
    """Send one prompt to the configured model of a size class."""

    def infer(self, inference_request: InferenceRequest) -> InferenceResult:
        """Return the model's text, or the unavailable state."""
        ...
