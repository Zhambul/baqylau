# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the services of one extension change stream together."""

from dataclasses import dataclass

from core.change_signal import ChangeSignal
from extensions.projection_models import GenerationHeads
from extensions.source_scope_contract import ExtensionScopeRegistry
from repository.contract.extension_records import ExtensionRecordRepository


@dataclass(frozen=True)
class ChangeStream:
    """Keep the change stream services, the history, the live generation reader, and the scope holds together."""

    records: ExtensionRecordRepository
    changes: ChangeSignal
    history_revision: str
    heads: GenerationHeads
    scopes: ExtensionScopeRegistry
