# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep bounded failure evidence without logging worker documents or credentials."""

from baqylau_extension_api.models.documents import Diagnostic


def call_failure() -> Diagnostic:
    """Do not copy arbitrary worker exception text into a processing journal.

    Returns:
        An explicit failed-call result which keeps the preceding input.

    """
    return Diagnostic(code="processing_call_failed", message="The extension call or its result check failed")
