"""The answer-question decision on the HTTP boundary."""

from enum import StrEnum


class AnswerDecisionBody(StrEnum):
    ANSWER = "answer"
    DISCUSS = "discuss"
