"""The state a new run starts from."""

from pi_jira_agent.graph.state import GraphState, initial_state


def test_initial_state_resets_every_field():
    """A restart reuses the issue's checkpoint thread, and LangGraph keeps any key the new
    input does not mention. A field added to GraphState without a default here would
    carry the previous run's value into the next one."""
    state = initial_state("TEST-1", channel="ui", repos=["web"], max_iterations=2)
    assert set(state) == set(GraphState.__annotations__)
    assert state["issue_key"] == "TEST-1" and state["repos"] == ["web"] and state["status"] == "fetching"
