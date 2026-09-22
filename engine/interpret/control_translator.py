# Copyright (c) 2026 Zhambyl Yermagambet
"""Dispatch confirmed control effects to focused translators."""

from typing import override

from engine.interpret import control_selections, control_sessions
from harness.contract import CoreTranslator
from harness.models import raw_events, translation_stages as stages


class ControlTranslator(CoreTranslator):
    """Translate a confirmed control effect."""

    @override
    def translate(
        self, raw_event: raw_events.RawEvent,
        *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> raw_events.TranslationResult:
        """Translate a confirmed control effect.

        Returns:
            The translation result.

        """
        translator_by_source = {
            "session_finish": control_sessions.session_finish,
            "session_close": control_sessions.session_close,
            "session_rename": control_sessions.session_rename,
            "model_selection": control_selections.model_selection,
            "effort_selection": control_selections.effort_selection,
            "message_queued": control_selections.message_queued,
        }
        translator = translator_by_source.get(
            raw_event.source_name,
            control_selections.plan_decision,
        )
        return stages.select_result(translator(raw_event), translation_stage)
