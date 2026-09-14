# Copyright (c) 2026 Zhambyl Yermagambet
"""Set the native default effort that a NEW session starts with.

OpenCode2 keeps ONE file for the whole machine:
`$XDG_STATE_HOME/opencode/model.json`, or `~/.local/state/opencode/model.json`.
Its `variant` map holds one effort for each `provider/model`. OpenCode2 reads
that map when a session starts, and the value there has PRIORITY over the
effort in the launch configuration. A launch alone therefore cannot select an
effort: measured 2026-09-12, a file that held `high` gave a `#low` launch a
`high` session, and the same launch against an empty file gave a `low` session.

The native side writes this same file whenever a person selects an effort, so
this is the file OpenCode2 itself treats as the answer.

Only a NEW session needs this. A resumed session keeps the effort it was
created with, and this file does not change it. A RUNNING session does not read
the file again either, so the effort control uses the native command instead.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError

if TYPE_CHECKING:
    from terminal.models.tabs import EnvironmentVariable

STATE_DIRECTORY = "XDG_STATE_HOME"
HOME_DIRECTORY = "HOME"
DEFAULT_STATE_PATH = ".local/state"
MODEL_FILE = Path("opencode") / "model.json"


class NativeModelState(BaseModel):
    """Read the native model state without losing the person's own keys.

    Extra keys are KEPT because this file also holds the person's recent and
    favourite models, and it is written back whole.
    """

    model_config = ConfigDict(extra="allow")
    variant: dict[str, str] = {}


def state_file(environment: tuple[EnvironmentVariable, ...]) -> Path:
    """Read the path of the native model state that the new session gets.

    The environment is the one the terminal tab starts with. The session reads
    the state directory from it, so the same environment tells where to write.
    A name the tab does not set comes from this process, because the tab starts
    from the environment of this process.

    Returns:
        The file OpenCode2 reads its default effort from.

    """
    configured = _setting(environment, STATE_DIRECTORY)
    if configured:
        return Path(configured) / MODEL_FILE
    home = _setting(environment, HOME_DIRECTORY)
    base = Path(home) if home else Path.home()
    return base / DEFAULT_STATE_PATH / MODEL_FILE


def apply(environment: tuple[EnvironmentVariable, ...], model: str, effort: str) -> str | None:
    """Record the effort that the next session of one model starts with.

    Every key that is not understood is kept, because this file also holds the
    person's own recent and favourite models.

    Returns:
        A reason when the effort could not be recorded, or None.

    """
    path = state_file(environment)
    saved = _read(path)
    if saved is None:
        return f"native model state at {path} could not be read"
    saved.variant = {**saved.variant, model: effort}
    return _write(path, saved)


def _setting(environment: tuple[EnvironmentVariable, ...], name: str) -> str | None:
    given = (setting.content for setting in environment if setting.name == name)
    return next(given, os.environ.get(name))


def _read(path: Path) -> NativeModelState | None:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # The first session on a machine writes this file.
        return NativeModelState()
    except OSError:
        return None
    try:
        return NativeModelState.model_validate_json(content)
    except ValidationError:
        return None


def _write(path: Path, native_model_state: NativeModelState) -> str | None:
    try:
        _replace(path, native_model_state)
    except OSError as error:
        return f"native model state at {path} could not be written: {error}"
    return None


def _replace(path: Path, native_model_state: NativeModelState) -> None:
    """Put the whole document in place at once.

    A reader of this file never sees a half-written document, because the
    person's own OpenCode2 sessions read it while this one writes it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp",
    ) as pending:
        pending.write(native_model_state.model_dump_json())
        staged = Path(pending.name)
    staged.replace(path)
