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


def test_manifest_records_what_produced_a_run():
    from pi_jira_agent import manifest, prompts
    from pi_jira_agent.config import settings

    taken = manifest.build(settings, pr_review=False)
    assert set(taken["stages"]) == {"requirements", "planning", "coding", "review", "pr_review"}
    assert taken["stages"]["pr_review"]["enabled"] is False
    assert taken["stages"]["coding"]["model"] == settings.stage_model("coding").model
    assert taken["pi_sdk_version"] == "1.1.0", "the pinned SDK"
    assert len(taken["runner_sha"]) == 12 and taken["runner_sha"] != "unknown"
    assert taken["agent_git_sha"] != "unknown"
    assert taken["prompts"]["phase_review"] == {"version": 1, "sha": prompts.get("phase_review").sha}
    assert set(taken["prompts"]) == {"phase_review", "requirements_framing", "scope_check", "pi_system"}
    # No key, token or path: the manifest is shown in the UI and stored with every run.
    assert "api_key" not in str(taken)


def test_a_prompt_edit_changes_its_hash(tmp_path, monkeypatch):
    from pi_jira_agent import prompts

    (tmp_path / "demo.md").write_text("<!-- version: 3 -->\nBe careful.\n", encoding="utf-8")
    monkeypatch.setattr(prompts, "_DIR", tmp_path)
    prompts.get.cache_clear()
    try:
        first = prompts.get("demo")
        assert (first.version, first.text) == (3, "Be careful.")
        (tmp_path / "demo.md").write_text("<!-- version: 3 -->\nBe very careful.\n", encoding="utf-8")
        prompts.get.cache_clear()
        assert prompts.get("demo").sha != first.sha, "an edit shows even when the version was not bumped"
        (tmp_path / "bad.md").write_text("No header.\n", encoding="utf-8")
        import pytest

        with pytest.raises(ValueError, match="must start with"):
            prompts.get("bad")
    finally:
        prompts.get.cache_clear()
