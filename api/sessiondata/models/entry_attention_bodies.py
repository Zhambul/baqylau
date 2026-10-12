# Copyright (c) 2026 Zhambyl Yermagambet
"""Define question entry bodies."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, model_validator


class EmptyQuestionIdError(ValueError):
    """Report an empty question identifier."""


class QuestionChoiceResponse(BaseModel):
    """Represent one question choice."""

    label: str
    description: str | None


class QuestionResponse(BaseModel):
    """Represent one question."""

    question_id: str
    title: str | None
    question: str
    multiple: bool
    choices: tuple[QuestionChoiceResponse, ...]
    free_text: bool

    @model_validator(mode="after")
    def require_question_id(self) -> Self:
        """Reject an empty question identifier.

        Returns:
            This question after validation.

        Raises:
            EmptyQuestionIdError: If the question identifier is empty.

        """
        if not self.question_id:
            message = "question_id must not be empty"
            raise EmptyQuestionIdError(message)
        return self


class QuestionAskedBodyResponse(BaseModel):
    """Represent a question-asked entry body."""

    attention_id: str
    questions: tuple[QuestionResponse, ...]


class QuestionAnswerResponse(BaseModel):
    """Represent one question answer."""

    question_id: str
    labels: tuple[str, ...]

    @model_validator(mode="after")
    def require_question_id(self) -> Self:
        """Reject an empty question identifier.

        Returns:
            This answer after validation.

        Raises:
            EmptyQuestionIdError: If the question identifier is empty.

        """
        if not self.question_id:
            message = "question_id must not be empty"
            raise EmptyQuestionIdError(message)
        return self


class QuestionAnsweredBodyResponse(BaseModel):
    """Represent a question-answered entry body."""

    attention_id: str
    answers: tuple[QuestionAnswerResponse, ...]
    feedback: str | None
