from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import litellm
from pydantic import TypeAdapter

type MetadataValue = (
    str | int | float | bool | None | Mapping[str, "MetadataValue"] | Sequence["MetadataValue"]
)
type ModelMetadata = Mapping[str, MetadataValue]
type MetadataLoader = Callable[[str], ModelMetadata]
_METADATA_ADAPTER: Final = TypeAdapter(dict[str, MetadataValue])

_TOKEN_MODES: Final = frozenset({"chat", "completion", "responses"})
_INPUT_RATE_PREFIXES: Final = (
    "input_cost_per_token",
    "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
)
_OUTPUT_RATE_PREFIXES: Final = (
    "output_cost_per_token",
    "output_cost_per_reasoning_token",
)
_MULTIPLIER_PREFIXES: Final = ("regional_processing_uplift_multiplier_",)
_TIER_RANGE_LENGTH: Final = 2


@dataclass(frozen=True, slots=True)
class PricingProfile:
    model: str
    input_usd_per_token: float
    output_usd_per_token: float


class PricingError(Exception):
    def __init__(self, *, model: str, reason: str) -> None:
        super().__init__(model, reason)
        self.model = model
        self.reason = reason

    def __str__(self) -> str:
        return f"unsafe LiteLLM pricing for {self.model!r}: {self.reason}"


def derive_pricing_profiles(
    models: Sequence[str], *, loader: MetadataLoader | None = None
) -> Mapping[str, PricingProfile]:
    """Build one fail-closed conservative profile for every primary/fallback model."""
    load = loader or _load_litellm_metadata
    profiles: dict[str, PricingProfile] = {}
    for index, model in enumerate(models):
        try:
            metadata = load(model)
            profiles[model] = _derive_profile(model, metadata)
        except PricingError as exc:
            role = "fallback" if index > 0 else "primary"
            raise PricingError(model=model, reason=f"{role}: {exc.reason}") from exc
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            role = "fallback" if index > 0 else "primary"
            raise PricingError(
                model=model,
                reason=f"{role}: unknown model in LiteLLM registry",
            ) from exc
    return MappingProxyType(profiles)


def _load_litellm_metadata(model: str) -> ModelMetadata:
    lookup_model = model
    if model.startswith("github_copilot/"):
        lookup_model = model.removeprefix("github_copilot/").removeprefix("responses/")
    return _METADATA_ADAPTER.validate_python(litellm.get_model_info(lookup_model))


def _derive_profile(model: str, metadata: ModelMetadata) -> PricingProfile:
    mode = metadata.get("mode")
    if mode not in _TOKEN_MODES:
        raise PricingError(model=model, reason="non-token model mode")

    input_rates: list[float] = []
    output_rates: list[float] = []
    multipliers = [1.0]
    _collect_mapping_rates(model, metadata, input_rates, output_rates, multipliers)

    tiers = metadata.get("tiered_pricing")
    if tiers is not None:
        if not isinstance(tiers, Sequence) or isinstance(tiers, (str, bytes)):
            raise PricingError(model=model, reason="malformed tiered_pricing")
        for tier in tiers:
            if not isinstance(tier, Mapping):
                raise PricingError(model=model, reason="malformed tiered_pricing entry")
            _reject_complex_tier(model, tier)
            _collect_mapping_rates(model, tier, input_rates, output_rates, multipliers)

    if not input_rates or not output_rates:
        reason = (
            "zero-only token pricing" if input_rates or output_rates else "missing token pricing"
        )
        raise PricingError(model=model, reason=reason)
    if max(input_rates) == 0.0 or max(output_rates) == 0.0:
        raise PricingError(model=model, reason="zero-only token pricing")

    multiplier = max(multipliers)
    return PricingProfile(
        model=model,
        input_usd_per_token=max(input_rates) * multiplier,
        output_usd_per_token=max(output_rates) * multiplier,
    )


def _collect_mapping_rates(
    model: str,
    metadata: ModelMetadata,
    input_rates: list[float],
    output_rates: list[float],
    multipliers: list[float],
) -> None:
    for key, raw in metadata.items():
        if key.startswith(_INPUT_RATE_PREFIXES):
            if raw is not None:
                input_rates.append(_numeric(model, key, raw))
        elif key.startswith(_OUTPUT_RATE_PREFIXES):
            if raw is not None:
                output_rates.append(_numeric(model, key, raw))
        elif key.startswith(_MULTIPLIER_PREFIXES) and raw is not None:
            multipliers.append(_numeric(model, key, raw))
    provider_rates = metadata.get("provider_specific_entry")
    if provider_rates is not None:
        if not isinstance(provider_rates, Mapping):
            raise PricingError(model=model, reason="malformed provider-specific pricing")
        for key, raw in provider_rates.items():
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                multipliers.append(_numeric(model, f"provider_specific_entry.{key}", raw))


def _numeric(model: str, key: str, raw: MetadataValue) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise PricingError(model=model, reason=f"malformed {key}")
    value = float(raw)
    if not math.isfinite(value):
        raise PricingError(model=model, reason=f"non-finite {key}")
    if value < 0:
        raise PricingError(model=model, reason=f"negative {key}")
    return value


def _reject_complex_tier(model: str, tier: Mapping[str, MetadataValue]) -> None:
    raw_range = tier.get("range")
    if raw_range is not None:
        if (
            not isinstance(raw_range, Sequence)
            or isinstance(raw_range, (str, bytes))
            or len(raw_range) != _TIER_RANGE_LENGTH
        ):
            raise PricingError(model=model, reason="malformed tier range")
        for boundary in raw_range:
            _numeric(model, "tier range", boundary)
    for key, raw in tier.items():
        recognized = key.startswith(_INPUT_RATE_PREFIXES + _OUTPUT_RATE_PREFIXES) or key == "range"
        if "cost" in key and not recognized and raw is not None:
            raise PricingError(model=model, reason=f"malformed complex tier field {key}")
