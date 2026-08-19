from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast
from uuid import uuid4

import litellm


@dataclass(frozen=True, slots=True)
class ResponsesResult:
    content: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    status: str | None
    incomplete_reason: str | None
    reasoning_tokens: int


class ResponsesCaller(Protocol):
    def __call__(
        self,
        *,
        model: str,
        custom_llm_provider: str,
        api_base: str,
        api_key: str,
        extra_headers: dict[str, object],
        input: list[dict[str, str]],
        reasoning: dict[str, str],
        store: bool,
        timeout: float,
    ) -> object: ...


class ResponsesCallerWithLimit(Protocol):
    def __call__(
        self,
        *,
        model: str,
        custom_llm_provider: str,
        api_base: str,
        api_key: str,
        extra_headers: dict[str, object],
        input: list[dict[str, str]],
        max_output_tokens: int,
        reasoning: dict[str, str],
        store: bool,
        timeout: float,
    ) -> object: ...


class ResponsesUsage(Protocol):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float | None
    output_tokens_details: ResponsesOutputDetails | None


class ResponsesOutputDetails(Protocol):
    reasoning_tokens: int


class IncompleteDetails(Protocol):
    reason: str


class ResponsesAPIResponse(Protocol):
    output_text: str
    usage: ResponsesUsage | None
    status: str | None
    incomplete_details: IncompleteDetails | None


def call_copilot_responses(
    *,
    model: str,
    messages: list[dict[str, str]],
    max_output_tokens: int | None,
    timeout_s: float,
    reasoning_effort: str,
    caller: ResponsesCaller | None = None,
) -> ResponsesResult:
    resolved_model, provider, token, api_base = litellm.get_llm_provider(model=model)
    if provider != "github_copilot" or token is None or api_base is None:
        msg = "responses mode requires resolved GitHub Copilot credentials"
        raise RuntimeError(msg)
    headers: dict[str, object] = {
        "Authorization": f"Bearer {token}",
        "content-type": "application/json",
        "copilot-integration-id": "vscode-chat",
        "editor-version": "vscode/1.95.0",
        "editor-plugin-version": "copilot-chat/0.26.7",
        "user-agent": "GitHubCopilotChat/0.26.7",
        "openai-intent": "conversation-panel",
        "x-github-api-version": "2025-04-01",
        "x-request-id": str(uuid4()),
        "x-vscode-user-agent-library-version": "electron-fetch",
    }
    invoke = caller or cast(ResponsesCaller, litellm.responses)
    if max_output_tokens is None:
        raw = invoke(
            model=resolved_model.removeprefix("responses/"),
            custom_llm_provider="openai",
            api_base=api_base,
            api_key=token,
            extra_headers=headers,
            input=messages,
            reasoning={"effort": reasoning_effort},
            store=False,
            timeout=timeout_s,
        )
    else:
        raw = cast(ResponsesCallerWithLimit, invoke)(
            model=resolved_model.removeprefix("responses/"),
            custom_llm_provider="openai",
            api_base=api_base,
            api_key=token,
            extra_headers=headers,
            input=messages,
            max_output_tokens=max_output_tokens,
            reasoning={"effort": reasoning_effort},
            store=False,
            timeout=timeout_s,
        )
    response = cast(ResponsesAPIResponse, raw)
    usage = response.usage
    if usage is None:
        msg = "responses provider returned no usage"
        raise RuntimeError(msg)
    details = usage.output_tokens_details
    incomplete = response.incomplete_details
    return ResponsesResult(
        content=response.output_text,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=usage.cost,
        status=response.status,
        incomplete_reason=incomplete.reason if incomplete is not None else None,
        reasoning_tokens=details.reasoning_tokens if details is not None else 0,
    )
