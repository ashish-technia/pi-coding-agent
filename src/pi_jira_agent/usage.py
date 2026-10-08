"""Tokens and cost of every model call in a run, and the run's budget (R-14).

Each call that returns adds one entry to ``GraphState["usage"]``. Pi reports the cost of its
own sessions; the stages that call a model directly are priced from ``MODEL_PRICES``. A call
whose model has no price is still recorded, with ``cost_usd`` None, so the tokens are never
lost and the total says how many calls it could not price.

A session that fails (or is stopped for going over budget) returns nothing, so its tokens are
not in the ledger; the runner logs them and reports them as an activity event instead.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import get_usage_metadata_callback

from .config import StageModelConfig

# USD per million tokens: {"gpt-4.1": {"input": 2.0, "output": 8.0, "cached_input": 0.5}}
Prices = dict[str, dict[str, float]]


class BudgetExceeded(RuntimeError):
    """The run has spent its budget. Surfaces as a stuck run that can be retried."""


def _entry(
    stage: str,
    cfg: StageModelConfig,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
    cost_usd: float | None,
) -> dict:
    return {
        "stage": stage,
        "provider": cfg.provider,
        "model": cfg.model,
        "input": int(input_tokens),
        "output": int(output_tokens),
        "cache_read": int(cache_read),
        "cache_write": int(cache_write),
        "cost_usd": None if cost_usd is None else round(float(cost_usd), 6),
        "at": round(time.time(), 3),
    }


def price(model: str, prices: Prices, *, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float | None:
    """Cost of one call in USD, or None when ``model`` has no entry in ``prices``.

    ``input_tokens`` includes the cached ones, as providers report it; cached tokens are
    charged at ``cached_input`` when the entry has one.
    """
    rate = prices.get(model)
    if not rate:
        return None
    cached = min(max(cache_read, 0), input_tokens)
    fresh = input_tokens - cached
    return (
        fresh * rate.get("input", 0.0)
        + cached * rate.get("cached_input", rate.get("input", 0.0))
        + output_tokens * rate.get("output", 0.0)
    ) / 1_000_000


def from_pi(stage: str, cfg: StageModelConfig, reported: dict | None) -> dict | None:
    """An entry from what the Pi runner put in ``AgentResult.usage``."""
    if not reported:
        return None
    return _entry(
        stage,
        cfg,
        input_tokens=reported.get("input", 0),
        output_tokens=reported.get("output", 0),
        cache_read=reported.get("cache_read", 0),
        cache_write=reported.get("cache_write", 0),
        cost_usd=reported.get("cost"),
    )


async def tracked(structured: Any, messages: list, *, stage: str, cfg: StageModelConfig, prices: Prices):
    """``await structured.ainvoke(messages)`` plus the entry for what it used (None if unreported)."""
    with get_usage_metadata_callback() as collected:
        result = await structured.ainvoke(messages)
    reported = collected.usage_metadata or {}
    if not reported:
        return result, None
    input_tokens = sum(u.get("input_tokens", 0) for u in reported.values())
    output_tokens = sum(u.get("output_tokens", 0) for u in reported.values())
    cache_read = sum((u.get("input_token_details") or {}).get("cache_read", 0) for u in reported.values())
    return result, _entry(
        stage,
        cfg,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cost_usd=price(
            cfg.model, prices, input_tokens=input_tokens, output_tokens=output_tokens, cache_read=cache_read
        ),
    )


def appended(state: Mapping[str, Any], *entries: dict | None) -> list[dict]:
    """The run's ledger with ``entries`` added. A node returns this as its ``usage`` update."""
    return [*(state.get("usage") or []), *(e for e in entries if e)]


def spent(entries: list[dict]) -> float:
    return sum(e.get("cost_usd") or 0.0 for e in entries)


def remaining(state: Mapping[str, Any], budget_usd: float) -> float | None:
    """What the run may still spend, or None when there is no budget."""
    if budget_usd <= 0:
        return None
    return max(budget_usd - spent(state.get("usage") or []), 0.0)


def check_budget(state: Mapping[str, Any], budget_usd: float) -> None:
    """Raise before a model call when the run has already spent its budget."""
    left = remaining(state, budget_usd)
    if left is not None and left <= 0:
        raise BudgetExceeded(
            f"The run reached its budget of ${budget_usd:.2f} "
            f"(${spent(state.get('usage') or []):.2f} spent). Raise RUN_BUDGET_USD, restart and retry."
        )


def summarise(entries: list[dict], budget_usd: float) -> dict:
    """Totals for the status API: per stage and overall."""
    stages: dict[str, dict] = {}
    for e in entries:
        s = stages.setdefault(
            e["stage"],
            {"stage": e["stage"], "calls": 0, "input": 0, "output": 0, "cache_read": 0, "cost_usd": 0.0, "unpriced": 0},
        )
        s["calls"] += 1
        s["input"] += e.get("input", 0)
        s["output"] += e.get("output", 0)
        s["cache_read"] += e.get("cache_read", 0)
        if e.get("cost_usd") is None:
            s["unpriced"] += 1
        else:
            s["cost_usd"] = round(s["cost_usd"] + e["cost_usd"], 6)
    by_stage = list(stages.values())
    return {
        "by_stage": by_stage,
        "calls": sum(s["calls"] for s in by_stage),
        "input": sum(s["input"] for s in by_stage),
        "output": sum(s["output"] for s in by_stage),
        "cost_usd": round(spent(entries), 6),
        # Calls whose model has no price: the cost above is a floor when this is not zero.
        "unpriced_calls": sum(s["unpriced"] for s in by_stage),
        "budget_usd": budget_usd if budget_usd > 0 else None,
    }
