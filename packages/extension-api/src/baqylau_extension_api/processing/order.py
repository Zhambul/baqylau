# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply validated transform operations in stable input order."""

from collections.abc import Iterable, Iterator, Mapping, Sequence

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.transforms import (
    MAX_TRANSFORM_OUTPUTS,
    Insert,
    Keep,
    Replace,
    TransformOperation,
    TransformOperations,
)

type OperationGroup[Document] = list[TransformOperation[Document]]


def apply_order[Document: WireModel](
    inputs: Mapping[str, Document], operations: TransformOperations[Document],
) -> tuple[Document, ...]:
    """Apply one checked batch without exposing a partial result.

    Returns:
        Inputs and additions in their stable final order.

    Raises:
        ExtensionContractError: If an anchor is unknown or output exceeds the bound.

    """
    grouped = _group_operations(inputs, operations)
    output = tuple(
        document for identity, original in inputs.items()
        for document in _apply_input(original, grouped[identity])
    )
    if len(output) > MAX_TRANSFORM_OUTPUTS:
        message = "transform result exceeds the output bound"
        raise ExtensionContractError(message)
    return output


def _group_operations[Document](
    identities: Iterable[str], operations: TransformOperations[Document],
) -> Mapping[str, OperationGroup[Document]]:
    grouped: dict[str, OperationGroup[Document]] = {identity: [] for identity in identities}
    for operation in operations:
        if operation.input_id not in grouped:
            message = "transform operation refers to an unknown input"
            raise ExtensionContractError(message)
        grouped[operation.input_id].append(operation)
    return grouped


def _apply_input[Document: WireModel](
    original: Document, operations: Sequence[TransformOperation[Document]],
) -> Iterator[Document]:
    yield from (
        operation.document for operation in operations
        if isinstance(operation, Insert) and operation.position == "before"
    )
    decision = next((operation for operation in operations if not isinstance(operation, Insert)), None)
    if decision is None or isinstance(decision, Keep):
        yield original
    elif isinstance(decision, Replace):
        yield decision.document
    yield from (
        operation.document for operation in operations
        if isinstance(operation, Insert) and operation.position == "after"
    )
