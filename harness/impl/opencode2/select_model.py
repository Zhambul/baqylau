# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the model of a live session through its native model list."""

from harness.contract import ControlHandler
from harness.impl.opencode2 import model_selection, native_screen, native_selection
from harness.models import controls
from harness.models.catalog import ModelOption

DIALOG_TITLE = "Select model"
MODEL_COMMAND = "/models"
VARIANT_TITLE = "Select variant"
FOOTER_FIELDS = 2


class SelectModelHandler(ControlHandler):
    """Select a model and confirm the native footer."""

    def __call__(
        self, request: controls.ControlRequest, control_context: controls.ControlContext,
    ) -> controls.ControlResult:
        """Select the requested model in the owned terminal.

        Returns:
            The confirmed selection, or a failure reason.

        Raises:
            TypeError: If another control is dispatched here.

        """
        if not isinstance(request, controls.SelectModel):
            message = "select_model requires SelectModel"
            raise TypeError(message)
        model = model_selection.option(control_context, request.model)
        if model is None:
            return controls.ControlResult(
                request.request_id, controls.ControlAcknowledgement.REJECTED, "model is not in the native catalog",
            )
        label = model_selection.label(model)
        reason = native_selection.choose(control_context, MODEL_COMMAND, DIALOG_TITLE, label)
        if reason is not None:
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.REJECTED, reason)
        reason = _variant(control_context, model)
        if reason is not None:
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.INDETERMINATE, reason)
        if native_screen.wait_for(
            control_context, lambda screen: _selected(screen, label), native_selection.DIALOG_SECONDS,
        ):
            return controls.ControlResult(request.request_id, controls.ControlAcknowledgement.ACKNOWLEDGED)
        return controls.ControlResult(
            request.request_id, controls.ControlAcknowledgement.INDETERMINATE,
            f"native model was not selected; footer: {_footer(native_screen.text(control_context))}",
        )


def _selected(screen: str, label: str) -> bool:
    if DIALOG_TITLE in screen or VARIANT_TITLE in screen:
        return False
    footer = _footer(screen)
    if not footer:
        return False
    fields = footer.split("·")
    if len(fields) < FOOTER_FIELDS:
        return False
    return model_selection.normalized(fields[1]) == label


def _footer(screen: str) -> str:
    lines = [line.strip() for line in screen.splitlines() if native_screen.is_composer_footer(line)]
    return lines[-1] if lines else ""


def _variant(control_context: controls.ControlContext, model_option: ModelOption) -> str | None:
    label = model_selection.label(model_option)
    native_screen.wait_for(
        control_context,
        lambda screen: VARIANT_TITLE in screen or _selected(screen, label),
        native_selection.DIALOG_SECONDS,
    )
    if VARIANT_TITLE not in native_screen.text(control_context):
        return None
    return native_selection.search(
        control_context, VARIANT_TITLE, model_selection.effort(model_option, control_context.current_effort),
    )
