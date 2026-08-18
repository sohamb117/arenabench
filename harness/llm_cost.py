from __future__ import annotations

import litellm

from harness.llm_types import CompletionResponse


def completion_cost(response: CompletionResponse) -> float | None:
    try:
        raw_cost = litellm.completion_cost(completion_response=response)
    except Exception:
        return None
    return float(raw_cost)
