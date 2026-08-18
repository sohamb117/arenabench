from __future__ import annotations

import pytest

from harness.attempt_logging import AttemptIdentity, failure_frame
from harness.llm import LlmCallError, LlmFailureCategory, LlmFailureMetadata

MAX_ERROR_TEXT_CHARS = 1_024

SECRET_CASES = (
    ('{"api_key":"sk-proj-json-secret"}', "sk-proj-json-secret"),
    ("{'API_KEY': 'ghp_abcdefghijklmnopqrstuvwxyz123456'}", "ghp_abcdefghijklmnopqrstuvwxyz123456"),
    ('token="github_pat_abcdefghijklmnopqrstuvwxyz"', "github_pat_abcdefghijklmnopqrstuvwxyz"),
    ("secret: xoxb-123456789-secret", "xoxb-123456789-secret"),
    ("Authorization: Bearer sk-bearer-secret", "sk-bearer-secret"),
    ("api_key sk-bare-secret", "sk-bare-secret"),
    ("aws_access_key_id=AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    (
        "aws_secret_access_key verySecretValue123/+=",
        "verySecretValue123/+=",
    ),
)
LITELLM_ERROR_SHAPES = (
    (
        "litellm.AuthenticationError: headers={'Authorization': 'Bearer sk-proj-auth-secret'}",
        "sk-proj-auth-secret",
    ),
    (
        'litellm.BadRequestError: request={"api_key":"sk-request-secret"}',
        "sk-request-secret",
    ),
    (
        "litellm.APIConnectionError: kwargs={'API_KEY': 'ghp_abcdefghijklmnopqrstuvwxyz123456'}",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
    ),
    (
        'litellm.RateLimitError: body={"token":"xoxb-123456789-rate-secret"}',
        "xoxb-123456789-rate-secret",
    ),
    (
        "litellm.ServiceUnavailableError: AKIAABCDEFGHIJKLMNOP "
        "aws_secret_access_key awsSecretValue123/+=",
        "awsSecretValue123/+=",
    ),
)


@pytest.mark.parametrize(
    "category",
    ["provider_refusal", "provider_error", "reservation_error", "context_logging_error"],
)
@pytest.mark.parametrize(("error_text", "secret"), SECRET_CASES)
def test_failure_frame_redacts_credentials_before_bounding(
    category: LlmFailureCategory, error_text: str, secret: str
) -> None:
    identity = AttemptIdentity(turn=3, request_id="req", attempt=2)
    error = LlmCallError(
        "prefix " + error_text + " z" * 2_000,
        LlmFailureMetadata(category=category, attempt=2),
    )

    frame = failure_frame(identity, error)

    assert secret not in frame.error_text
    assert "[REDACTED]" in frame.error_text
    assert len(frame.error_text) <= MAX_ERROR_TEXT_CHARS


@pytest.mark.parametrize(("error_text", "secret"), LITELLM_ERROR_SHAPES)
def test_failure_frame_redacts_real_litellm_error_shapes(error_text: str, secret: str) -> None:
    identity = AttemptIdentity(turn=1, request_id="litellm", attempt=0)
    error = LlmCallError(
        error_text,
        LlmFailureMetadata(category="provider_error", attempt=0),
    )

    frame = failure_frame(identity, error)

    assert secret not in frame.error_text
    assert "[REDACTED]" in frame.error_text
