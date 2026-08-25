"""Typed failures from model inference."""


class ModelUnavailableError(RuntimeError):
    """No configured model provider can currently answer."""


class ProviderUnavailableError(RuntimeError):
    """One provider failed in a way that permits trying another provider."""
