from dataclasses import dataclass
from typing import Literal


ProviderName = Literal["openrouter", "ollama"]


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    provider: ProviderName
    parameter_count_billion: float
    purpose: str


OPENROUTER_WORKER = ModelConfig(
    model_id="qwen/qwen3-4b",
    provider="openrouter",
    parameter_count_billion=4.02,
    purpose="Domain worker agents",
)

OPENROUTER_REASONING = ModelConfig(
    model_id="qwen/qwen3-8b",
    provider="openrouter",
    parameter_count_billion=8.2,
    purpose="Coordinator, policy and verifier agents",
)

OPENROUTER_FALLBACK = ModelConfig(
    model_id="qwen/qwen-2.5-7b-instruct",
    provider="openrouter",
    parameter_count_billion=7.0,
    purpose="Fallback when Qwen3 endpoint fails",
)

OLLAMA_LOCAL = ModelConfig(
    model_id="qwen3:4b",
    provider="ollama",
    parameter_count_billion=4.02,
    purpose="Local execution and offline verification",
)


def validate_model_limit(config: ModelConfig) -> None:
    if config.parameter_count_billion > 10:
        raise ValueError(
            f"Model {config.model_id} exceeds the 10B parameter limit."
        )