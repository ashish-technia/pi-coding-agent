"""Chat-model factory for the stages that call an LLM directly (requirements, review).

Planning and coding go through the Pi harness instead; see pi_agent.py.
"""

from __future__ import annotations

from typing import Any

from .config import StageModelConfig


def make_chat_model(cfg: StageModelConfig):
    provider = cfg.provider.lower()
    key_kwargs: dict[str, Any] = {"api_key": cfg.api_key} if cfg.api_key else {}

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=cfg.model, **key_kwargs)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # `model` is the field name; pyright only sees its alias, `model_name`.
        return ChatAnthropic(model=cfg.model, **key_kwargs)  # pyright: ignore[reportCallIssue]
    if provider in {"google", "gemini"}:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI  # pyright: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ValueError(
                "Provider 'google' requires the langchain-google-genai package: "
                "pip install langchain-google-genai"
            ) from exc

        return ChatGoogleGenerativeAI(model=cfg.model, **({"google_api_key": cfg.api_key} if cfg.api_key else {}))
    raise ValueError(
        f"Unsupported model provider {cfg.provider!r}; expected 'openai', 'anthropic' or 'google'."
    )
