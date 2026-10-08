"""The state a new run starts from."""

from pi_jira_agent.graph.state import GraphState, initial_state


def test_initial_state_resets_every_field():
    """A restart reuses the issue's checkpoint thread, and LangGraph keeps any key the new
    input does not mention. A field added to GraphState without a default here would
    carry the previous run's value into the next one."""
    state = initial_state("TEST-1", channel="ui", repos=["web"], max_iterations=2)
    assert set(state) == set(GraphState.__annotations__)
    assert state["issue_key"] == "TEST-1" and state["repos"] == ["web"] and state["status"] == "fetching"


def test_price_uses_the_cached_rate_for_cached_input():
    from pi_jira_agent import usage

    prices = {"m": {"input": 2.0, "output": 8.0, "cached_input": 0.5}}
    # 1M input of which 400k cached, 100k output.
    cost = usage.price("m", prices, input_tokens=1_000_000, output_tokens=100_000, cache_read=400_000)
    assert cost == 0.6 * 2.0 + 0.4 * 0.5 + 0.1 * 8.0
    assert usage.price("unknown", prices, input_tokens=10, output_tokens=10) is None


def test_unpriced_calls_are_counted_not_dropped():
    from pi_jira_agent import usage

    entries = [
        {"stage": "review", "input": 100, "output": 10, "cache_read": 0, "cost_usd": None},
        {"stage": "coding", "input": 500, "output": 50, "cache_read": 0, "cost_usd": 0.02},
    ]
    summary = usage.summarise(entries, budget_usd=0)
    assert summary["cost_usd"] == 0.02
    assert summary["unpriced_calls"] == 1
    assert summary["input"] == 600
    assert summary["budget_usd"] is None
