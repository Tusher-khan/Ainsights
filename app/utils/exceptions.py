"""Typed exceptions for AInsight.

Every exception carries a user-safe message (shown in the UI) and an optional
internal detail that is logged but never returned to the browser.
"""

from __future__ import annotations


class AInsightError(Exception):
    """Base class for all application errors."""

    status_code = 400
    user_message = "Something went wrong while processing your request."

    def __init__(self, user_message: str | None = None, detail: str | None = None):
        self.user_message = user_message or self.__class__.user_message
        self.detail = detail or self.user_message
        super().__init__(self.detail)

    def to_dict(self) -> dict:
        return {
            "error": self.__class__.__name__,
            "message": self.user_message,
            "status": self.status_code,
        }


class ConfigurationError(AInsightError):
    status_code = 500
    user_message = "The application is not configured correctly."


class FileValidationError(AInsightError):
    status_code = 400
    user_message = "This file could not be accepted."


class FileProcessingError(AInsightError):
    status_code = 422
    user_message = "This file could not be processed."


class DatasetNotFoundError(AInsightError):
    status_code = 404
    user_message = "That dataset no longer exists."


class ColumnNotFoundError(AInsightError):
    status_code = 422
    user_message = "A column referenced in the analysis is not in the dataset."


class PlanValidationError(AInsightError):
    status_code = 422
    user_message = "The analysis plan could not be validated."


class SQLSecurityError(AInsightError):
    status_code = 403
    user_message = "That query was blocked because it is not a read-only analytical query."


class QueryExecutionError(AInsightError):
    status_code = 422
    user_message = "The analysis could not be executed on this dataset."


class ResultValidationError(AInsightError):
    status_code = 422
    user_message = "The result of the analysis failed verification."


class LLMError(AInsightError):
    status_code = 503
    user_message = "The language model is unavailable right now."


class LLMTimeoutError(LLMError):
    user_message = "The language model took too long to respond."


class RateLimitError(LLMError):
    status_code = 429
    user_message = "The language model rate limit was reached. Try again shortly."


class PromptInjectionError(AInsightError):
    status_code = 400
    user_message = "That request contains instructions that cannot be followed."


class UnsupportedRequestError(AInsightError):
    status_code = 422
    user_message = "AInsight cannot answer that with the available data."
