from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from orchestrator.pricing import (
    ModelMetadata,
    PricingError,
    PricingProfile,
    derive_pricing_profiles,
)

INPUT_RATE = 0.000_002
OUTPUT_RATE = 0.000_008


def _loader(entries: dict[str, ModelMetadata]) -> Callable[[str], ModelMetadata]:
    def load(model: str) -> ModelMetadata:
        if model not in entries:
            raise RuntimeError("not mapped")
        return entries[model]

    return load


def test_profile_uses_maximum_token_rates_and_multiplier() -> None:
    metadata: ModelMetadata = {
        "mode": "chat",
        "input_cost_per_token": 0.000_001,
        "input_cost_per_token_above_200k_tokens": 0.000_003,
        "cache_read_input_token_cost": 0.000_000_5,
        "cache_creation_input_token_cost": 0.000_004,
        "output_cost_per_token": 0.000_005,
        "output_cost_per_reasoning_token": 0.000_007,
        "regional_processing_uplift_multiplier_eu": 1.1,
        "provider_specific_entry": {"global": 1.0, "priority": 1.5},
        "tiered_pricing": [
            {
                "range": [0, 1000],
                "input_cost_per_token": 0.000_002,
                "output_cost_per_token": 0.000_006,
            }
        ],
    }

    profiles = derive_pricing_profiles(
        ("provider/model",), loader=_loader({"provider/model": metadata})
    )

    assert profiles == {
        "provider/model": PricingProfile(
            model="provider/model",
            input_usd_per_token=0.000_006,
            output_usd_per_token=0.000_010_5,
        )
    }


@pytest.mark.parametrize(
    ("metadata", "reason"),
    [
        ({"mode": "chat"}, "missing"),
        (
            {"mode": "chat", "input_cost_per_token": 0.0, "output_cost_per_token": 0.0},
            "zero-only",
        ),
        (
            {"mode": "chat", "input_cost_per_token": 0.1, "output_cost_per_token": 0.0},
            "zero-only",
        ),
        (
            {"mode": "embedding", "input_cost_per_token": 0.1, "output_cost_per_token": 0.1},
            "non-token",
        ),
        (
            {"mode": "chat", "input_cost_per_token": "cheap", "output_cost_per_token": 0.1},
            "malformed",
        ),
        (
            {"mode": "chat", "input_cost_per_token": -0.1, "output_cost_per_token": 0.1},
            "negative",
        ),
        (
            {
                "mode": "chat",
                "input_cost_per_token": math.inf,
                "output_cost_per_token": 0.1,
            },
            "non-finite",
        ),
        (
            {
                "mode": "chat",
                "input_cost_per_token": 0.1,
                "output_cost_per_token": 0.1,
                "tiered_pricing": {"tier": 2},
            },
            "malformed",
        ),
    ],
)
def test_profile_rejects_unsafe_metadata(metadata: ModelMetadata, reason: str) -> None:
    with pytest.raises(PricingError, match=reason):
        derive_pricing_profiles(("provider/model",), loader=_loader({"provider/model": metadata}))


def test_profile_rejects_unknown_model() -> None:
    with pytest.raises(PricingError, match="unknown"):
        derive_pricing_profiles(("provider/unknown",), loader=_loader({}))


def test_profile_ignores_null_optional_rates() -> None:
    metadata: ModelMetadata = {
        "mode": "responses",
        "input_cost_per_token": INPUT_RATE,
        "input_cost_per_token_flex": None,
        "output_cost_per_token": OUTPUT_RATE,
        "output_cost_per_reasoning_token": None,
        "regional_processing_uplift_multiplier_us": None,
    }

    profile = derive_pricing_profiles(
        ("provider/model",), loader=_loader({"provider/model": metadata})
    )["provider/model"]

    assert profile.input_usd_per_token == INPUT_RATE
    assert profile.output_usd_per_token == OUTPUT_RATE


def test_profile_rejects_complex_fallback_tier() -> None:
    entries: dict[str, ModelMetadata] = {
        "provider/primary": {
            "mode": "chat",
            "input_cost_per_token": 0.1,
            "output_cost_per_token": 0.2,
        },
        "provider/fallback": {
            "mode": "chat",
            "tiered_pricing": [{"range": [0, 100], "input_cost_per_query": 0.3}],
        },
    }

    with pytest.raises(PricingError, match="fallback"):
        derive_pricing_profiles(("provider/primary", "provider/fallback"), loader=_loader(entries))
