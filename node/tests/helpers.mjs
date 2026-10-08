// Shared by the runner tests: a scripted model (Pi's own fake provider) and throwaway repos.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createFauxCore, fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { ModelRuntime } from "@earendil-works/pi-coding-agent";

/** One model turn that calls a tool. */
export const call = (name, args) => fauxAssistantMessage([fauxToolCall(name, args)], { stopReason: "toolUse" });

/** The final model turn: the JSON object the runner parses. */
export const answer = (value) => fauxAssistantMessage(JSON.stringify(value));

/**
 * `deps` for runAgent: a model that replays `responses` in order, and the events it emitted.
 * No network and no API key are involved.
 */
export async function scripted(responses) {
  const core = createFauxCore({ api: "faux-api", provider: "faux", models: [{ id: "faux-1" }] });
  core.setResponses(responses);
  const modelRuntime = await ModelRuntime.create();
  modelRuntime.registerProvider("faux", {
    api: "faux-api",
    apiKey: "unused",
    baseUrl: "http://localhost",
    streamSimple: core.streamSimple,
    models: [
      {
        id: "faux-1",
        name: "faux-1",
        reasoning: false,
        input: ["text"],
        cost: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 100000,
        maxTokens: 8000,
      },
    ],
  });
  const events = [];
  return {
    events,
    deps: { modelRuntime, model: modelRuntime.getModel("faux", "faux-1"), emit: (event) => events.push(event) },
  };
}

/** A temp directory holding `files` ({relative path: content}); removed by `cleanup()`. */
export function tempDir(files = {}) {
  const dir = fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(), "pi-runner-")));
  for (const [rel, content] of Object.entries(files)) {
    const target = path.join(dir, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  }
  return { dir, cleanup: () => fs.rmSync(dir, { recursive: true, force: true }) };
}

export const issue = { key: "T-1", project_key: "T", summary: "Fix the greeting", description: "Say hello." };

/** The payload Python sends, for a repository at `repo`. */
export const payload = (repo, extra = {}) => ({
  issue,
  model: "faux-1",
  provider: "faux",
  systemPrompt: "test",
  agentDir: path.join(repo, "..", ".pi-agent-test"),
  repoCwd: repo,
  ...extra,
});
