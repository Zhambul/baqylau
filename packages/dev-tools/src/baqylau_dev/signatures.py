# Copyright (c) 2026 Zhambyl Yermagambet
"""Compare protocol method names without a host repository path."""

import ast
from collections.abc import Mapping

type MethodSignatures = dict[str, tuple[str, ...]]


def method_signatures(node: ast.ClassDef) -> MethodSignatures:
    """Read the call shape of methods declared in a class.

    Returns:
        Method names and ordered parameter names, including argument kinds.

    """
    return {
        member.name: parameter_names(member.args)
        for member in node.body
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def parameter_names(arguments: ast.arguments) -> tuple[str, ...]:
    """Keep positional-only, keyword-only, and variadic parameters distinct.

    Returns:
        A stable signature that includes names used in keyword calls.

    """
    positional = tuple(argument.arg for argument in arguments.posonlyargs)
    names = [*positional, *(("/",) if positional else ())]
    names.extend(argument.arg for argument in arguments.args)
    if arguments.vararg:
        names.append(_variadic_name(arguments.vararg, "*"))
    names.extend(f"*{argument.arg}" for argument in arguments.kwonlyargs)
    if arguments.kwarg:
        names.append(_variadic_name(arguments.kwarg, "**"))
    return tuple(names)


def satisfies(
    members: Mapping[str, tuple[str, ...]],
    protocol: Mapping[str, tuple[str, ...]],
) -> bool:
    """Require every method of a nonempty protocol to have the same call shape.

    Returns:
        True when all required method signatures match.

    """
    return bool(protocol) and all(
        members.get(name) == arguments for name, arguments in protocol.items()
    )


def _variadic_name(argument: ast.arg, prefix: str) -> str:
    return f"{prefix}{argument.arg}"
