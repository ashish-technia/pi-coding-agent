// R-14: the runner reports what a session used, and stops one that passes the run's budget.
import assert from "node:assert/strict";
import { test } from "node:test";
import { runAgent } from "../pi-sdk-runner.mjs";
import { answer, call, payload, scripted, tempDir } from "./helpers.mjs";

const plan = {
  branch_name: "feature/T-1",
  commit_message: "T-1: greet",
  pr_title: "T-1",
  pr_description: "",
  files_changed: ["README.md"],
  analysis: "README.md holds the greeting.",
  plan_steps: [{ file: "README.md", action: "modify", change: "Change the greeting", evidence: "It says hello" }],
  verification: ["read README.md"],
  open_questions: [],
  notes_response: "",
  phases: [],
};

test("the result carries the session's tokens and cost", async () => {
  const repo = tempDir({ "README.md": "hello" });
  try {
    const { deps, events } = await scripted([call("read", { path: "README.md" }), answer(plan)]);
    const result = await runAgent(payload(repo.dir), deps);

    assert.ok(result.usage.input > 0, "input tokens");
    assert.ok(result.usage.output > 0, "output tokens");
    assert.equal(result.usage.cost, 0); // the scripted provider is free
    assert.deepEqual(Object.keys(result.usage).sort(), ["cache_read", "cache_write", "cost", "input", "output"]);
    assert.equal(events.filter((e) => e.ev === "usage").length, 1);
  } finally {
    repo.cleanup();
  }
});

test("a session that passes the remaining budget is stopped and says so", async () => {
  const repo = tempDir({ "README.md": "hello" });
  try {
    const { deps, events } = await scripted([
      call("read", { path: "README.md" }),
      call("read", { path: "README.md" }),
      answer(plan),
    ]);
    const spending = { ...deps, costOf: (stats) => stats.assistantMessages * 0.4 };
    await assert.rejects(runAgent(payload(repo.dir, { maxCostUsd: 0.5 }), spending), /reached its budget/);
    // $0.40 a turn against $0.50: over after the second turn, so no plan comes back.
    assert.equal(events.at(-1).ev, "usage");
  } finally {
    repo.cleanup();
  }
});
