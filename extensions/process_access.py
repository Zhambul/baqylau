# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one worker's declared programs through the bounded runner, with no shell."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from baqylau_extension_api.contracts.processes import ExtensionProcessService
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.processes import ProcessDeclaration
from baqylau_extension_api.models import processes

from extensions.environment_contract import PreparationRunner
from extensions.models.processes import PreparationCommand
from extensions.preparation_output import PreparationOutputLimitError


@dataclass(frozen=True)
class HostProcessService(ExtensionProcessService):
    """Bind the bounded runner to one worker's manifest, so a worker runs only its declared programs."""

    manifest: ExtensionManifest
    runner: PreparationRunner

    def run_process(self, process_request: processes.ProcessRequest) -> processes.ProcessResult:
        """Run one declared program in its own process group, with no shell.

        Returns:
            The exit code and output, or the failed bound.

        Raises:
            ExtensionContractError: If the program is not declared or the directory does not exist.

        """
        checked = processes.ProcessRequest.model_validate(process_request)
        declaration = _declaration(self.manifest, checked.name)
        if not Path(checked.cwd).is_dir():
            message = "the process working directory does not exist"
            raise ExtensionContractError(message)
        executable = shutil.which(declaration.executable)
        if executable is None:
            return processes.ProcessFailed(name=checked.name, reason="not_found")
        try:
            output = self.runner.run_preparation(_command(declaration, checked, executable))
        except TimeoutError:
            return processes.ProcessFailed(name=checked.name, reason="timed_out")
        except PreparationOutputLimitError:
            return processes.ProcessFailed(name=checked.name, reason="output_limit")
        return processes.ProcessExited(
            name=checked.name, exit_code=output.return_code,
            stdout=output.stdout.decode(errors="replace"), stderr=output.stderr.decode(errors="replace"),
        )


def _declaration(manifest: ExtensionManifest, name: str) -> ProcessDeclaration:
    matching = (process for process in manifest.contributions.processes if process.name == name)
    declared = next(matching, None)
    if declared is None:
        message = "the package does not declare this process"
        raise ExtensionContractError(message)
    return declared


def _command(
    declaration: ProcessDeclaration, process_request: processes.ProcessRequest, executable: str,
) -> PreparationCommand:
    limit = declaration.max_seconds
    requested = process_request.timeout_seconds
    timeout = limit if requested is None else min(limit, requested)
    return PreparationCommand(
        arguments=(executable, *process_request.arguments), environment=os.environ.copy(),
        directory=Path(process_request.cwd), timeout_seconds=timeout, output_limit=processes.MAX_PROCESS_OUTPUT,
    )
