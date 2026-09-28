# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide architecture test controls."""

from __future__ import annotations

from tests import (
    architecture_project_dependencies as project_dependencies,
    architecture_standard_dependencies as standard_dependencies,
    architecture_test_canonical,
    architecture_test_files,
    architecture_test_harnesses,
    architecture_test_imports,
    architecture_test_providers,
    architecture_test_tables,
)

ROOT = project_dependencies.Path(__file__).resolve().parents[1]
TEXT_ENCODING = "utf-8"
PYTHON_FILE_PATTERN = "*.py"
IMPLEMENTATION_DIRECTORY_NAME = "impl"
HARNESS_PACKAGE = "harness"
MODELS_DIRECTORY_NAME = "models"
TYPING_DICT_NAME = "Dict"
IDS_FILE_NAME = "ids.py"
RAW_RESPONSE_ROUTES = (
    "index",
    "build_asset",
    "extension_web_asset",
    "static",
    "service_worker",
    "favicon",
    "openapi_yaml",
    "record_hook_delivery",
    "global_stream",
    "session_stream",
    "extension_changes",
)
type RegisteredHandler = tuple[str, str, str | None, list[str] | None]


def control_name_values() -> dict[str, str]:
    """Return each control member name and its text value.

    Returns:
        Each control member name and its text value.

    """
    controls_path = ROOT / HARNESS_PACKAGE / MODELS_DIRECTORY_NAME / "control_enums.py"
    tree = standard_dependencies.ast.parse(controls_path.read_text(encoding=TEXT_ENCODING))
    return architecture_test_canonical.control_members(architecture_test_tables.control_class(tree))


def controller_registrations(
    path: project_dependencies.Path,
    node: standard_dependencies.ast.AST,
    classes: dict[str, list[str]],
    mappings: project_dependencies.Mapping[str, standard_dependencies.ast.Dict],
    control_name_values: dict[str, str],
) -> project_dependencies.Iterator[RegisteredHandler]:
    """Read handler registrations from a direct controller constructor call.

    Yields:
        Registrations from each locally resolved dictionary argument.

    """
    controller = architecture_test_harnesses.harness_controller_call(node)
    if controller is not None:
        for mapping in controller.args:
            resolved_mapping = architecture_test_harnesses.resolved_controller_mapping(mapping, mappings)
            if isinstance(resolved_mapping, standard_dependencies.ast.Dict):
                yield from architecture_test_files.registrations_in_mapping(
                    path, resolved_mapping, classes, control_name_values,
                )


def native_id_violations(adapter: str, canonical_entity_ids: set[str]) -> list[str]:
    """Check an adapter for canonical identifier construction outside its ID module.

    Returns:
        The prohibited constructor call messages.

    """
    root = ROOT / HARNESS_PACKAGE / IMPLEMENTATION_DIRECTORY_NAME / adapter
    violations: list[str] = []
    for path in root.rglob(PYTHON_FILE_PATTERN):
        if path == root / IDS_FILE_NAME:
            continue
        violations.extend(architecture_test_files.native_id_violations_in_path(path, canonical_entity_ids))
    return violations


def key_value_table_violations(schema: str) -> tuple[list[str], int]:
    """Check schema tables for generic storage columns.

    Returns:
        Column failure messages and the number of matched tables.

    """
    allowed_opaque = {
        "canonical_events.payload",
        "raw_events.payload",
        "state_files.content",
        "session_data.payload",
        "session_data_actors.payload",
        "session_entries.payload",
        # The same typed feed body as session_entries, kept for a candidate or retired generation.
        "extension_candidate_entries.payload",
    }
    tables = standard_dependencies.re.findall(
        r'CREATE TABLE IF NOT EXISTS (\w+)\((.*?)\n\s*\)(?:;|\s*""")',
        schema,
        standard_dependencies.re.DOTALL,
    )
    violations: list[str] = []
    for table, body in tables:
        violations.extend(architecture_test_providers.table_column_violations(table, body, allowed_opaque))
    return (violations, len(tables))


def route_model_violations(
    path: project_dependencies.Path,
    node: standard_dependencies.ast.FunctionDef | standard_dependencies.ast.AsyncFunctionDef,
    decorators: list[str],
) -> list[str]:
    """Check a route for a return type and API-owned response models.

    Returns:
        Failure messages, or an empty list for valid or exempt routes.

    """
    if node.name in RAW_RESPONSE_ROUTES:
        return []
    endpoint = f"{path.relative_to(ROOT)}:{node.name}"
    if not node.returns:
        return [f"{endpoint} has no return type"]
    bindings = architecture_test_providers.binding_modules(path)
    return [
        f"{endpoint} answers with {name}, which is {module}'s and not the api layer's"
        for name in architecture_test_imports.declared_route_names(node, decorators)
        if (module := bindings.get(name)) is not None and (not module.startswith("api."))
    ]


def is_unapproved_dictionary(
    node: standard_dependencies.ast.AST,
    parents: project_dependencies.Mapping[standard_dependencies.ast.AST, standard_dependencies.ast.AST],
    allowed_registries: project_dependencies.AbstractSet[str],
) -> bool:
    """Check whether a dictionary expression has an approved registry owner.

    Returns:
        True for a dictionary literal or comprehension without approval.

    """
    if not isinstance(node, (standard_dependencies.ast.Dict, standard_dependencies.ast.DictComp)):
        return False
    return architecture_test_providers.assigned_name(node, parents) not in allowed_registries


def dictionary_type_violation(
    node: standard_dependencies.ast.AST,
    parents: project_dependencies.Mapping[standard_dependencies.ast.AST, standard_dependencies.ast.AST],
    allowed_registries: project_dependencies.AbstractSet[str],
) -> str | None:
    """Return a violation for one raw dictionary annotation.

    Returns:
        A violation for one raw dictionary annotation.

    """
    if isinstance(node, standard_dependencies.ast.Attribute) and node.attr in {"dict", TYPING_DICT_NAME}:
        return "uses the raw dictionary type"
    if (
        isinstance(node, standard_dependencies.ast.Name)
        and node.id in {"dict", TYPING_DICT_NAME}
        and (architecture_test_providers.assigned_name(node, parents) not in allowed_registries)
    ):
        return "uses the raw dictionary type"
    return None
