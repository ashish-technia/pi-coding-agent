import process from "node:process";
import path from "node:path";
import { AuthStorage, ModelRegistry, SessionManager, createAgentSession } from "@mariozechner/pi-coding-agent";

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function buildPrompt(issue, systemPrompt, modelId, options) {
  const executeMode = options?.executeChanges ?? false;
  const branchName = options?.branchName ?? "";
  const instructions = executeMode
    ? [
        "You are running in repository execution mode.",
        "Apply the required code changes in the current repository for the Jira issue.",
        "Do not run git commit or git push.",
        branchName ? `Use this branch name in output: ${branchName}` : "Choose a branch name.",
      ]
    : ["Prepare a PR plan without modifying repository files."];

  return [
    `System guidance: ${systemPrompt}`,
    `Preferred model id: ${modelId}`,
    ...instructions,
    "Return ONLY strict JSON with keys:",
    "branch_name, commit_message, pr_title, pr_description, files_changed.",
    "files_changed must be an array of strings.",
    "No markdown, no prose outside JSON.",
    "",
    `Jira key: ${issue.key}`,
    `Project: ${issue.project_key}`,
    `Summary: ${issue.summary}`,
    `Description: ${issue.description ?? ""}`,
    `Reporter: ${issue.reporter ?? ""}`
  ].join("\n");
}

/**
 * First complete `{ ... }` in `text`, respecting strings and escapes.
 * Avoids `first {` + `last }` which breaks on `}` inside values or on `json}{more`.
 */
function extractFirstJsonObject(text) {
  const start = text.indexOf("{");
  if (start === -1) {
    throw new Error("No JSON object found in Pi output.");
  }
  let depth = 0;
  let inString = false;
  let escape = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (escape) {
      escape = false;
      continue;
    }
    if (inString) {
      if (c === "\\") {
        escape = true;
      } else if (c === '"') {
        inString = false;
      }
      continue;
    }
    if (c === '"') {
      inString = true;
      continue;
    }
    if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) {
        return text.slice(start, i + 1);
      }
    }
  }
  throw new Error("Unbalanced braces in Pi JSON output.");
}

/** Plain text from Pi `message.content` blocks (OpenAI responses + Anthropic). */
function textFromContentBlocks(content) {
  if (!Array.isArray(content)) return "";
  let out = "";
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    if (block.type === "text" && typeof block.text === "string") {
      out += block.text;
    }
  }
  return out;
}

function textFromAssistantMessage(message) {
  if (!message || message.role !== "assistant") return "";
  return textFromContentBlocks(message.content);
}

function readAssistantTextFromEvent(event) {
  if (!event || typeof event !== "object") return "";

  if (event.type === "message_end" && event.message) {
    return textFromAssistantMessage(event.message);
  }

  if (event.type === "agent_end" && Array.isArray(event.messages)) {
    for (let i = event.messages.length - 1; i >= 0; i--) {
      const t = textFromAssistantMessage(event.messages[i]);
      if (t) return t;
    }
    return "";
  }

  if (event.type !== "message_update") return "";

  const assistantEvent = event.assistantMessageEvent;
  if (!assistantEvent || typeof assistantEvent !== "object") return "";

  if (assistantEvent.type === "text_delta" && typeof assistantEvent.delta === "string") {
    return assistantEvent.delta;
  }
  if (assistantEvent.type === "text" && typeof assistantEvent.text === "string") {
    return assistantEvent.text;
  }
  if (assistantEvent.type === "content" && typeof assistantEvent.content === "string") {
    return assistantEvent.content;
  }
  if (assistantEvent.partial?.role === "assistant" && Array.isArray(assistantEvent.partial.content)) {
    return textFromContentBlocks(assistantEvent.partial.content);
  }
  if (event.message?.role === "assistant") {
    return textFromAssistantMessage(event.message);
  }
  return "";
}

async function main() {
  const input = await readStdin();
  const parsed = JSON.parse(input);
  const issue = parsed.issue;
  const modelId = parsed.model;
  const systemPrompt = parsed.systemPrompt;
  const provider = parsed.provider ?? "openai";
  const agentDirInput = parsed.agentDir ?? ".pi-agent";
  const repoCwdInput = parsed.repoCwd ?? process.cwd();
  const executeChanges = parsed.executeChanges ?? false;
  const branchName = parsed.branchName ?? "";
  const cwd = path.resolve(repoCwdInput);
  const agentDir = path.resolve(agentDirInput);

  const providerApiKey = process.env.PI_PROVIDER_API_KEY;
  if (!providerApiKey) {
    throw new Error("PI_PROVIDER_API_KEY env var is required.");
  }

  const authStorage = AuthStorage.create();
  authStorage.setRuntimeApiKey(provider, providerApiKey);
  const modelRegistry = ModelRegistry.create(authStorage);

  const { session } = await createAgentSession({
    cwd,
    agentDir,
    sessionManager: SessionManager.inMemory(),
    authStorage,
    modelRegistry
  });

  let streamedText = "";
  const eventTypes = [];
  session.subscribe((event) => {
    if (event?.type) {
      eventTypes.push(
        event.assistantMessageEvent?.type ? `${event.type}:${event.assistantMessageEvent.type}` : event.type
      );
    }
    const chunk = readAssistantTextFromEvent(event);
    if (!chunk) return;
    if (event.type === "message_end" || event.type === "agent_end") {
      if (!streamedText.trim()) streamedText = chunk;
    } else {
      streamedText += chunk;
    }
  });

  await session.prompt(
    buildPrompt(issue, systemPrompt, modelId, {
      executeChanges,
      branchName
    })
  );
  if (!streamedText.trim()) {
    throw new Error(
      `Pi returned empty assistant text. provider=${provider} model=${modelId} ` +
        `agentDir=${agentDir} seenEvents=${JSON.stringify(eventTypes.slice(0, 30))}`
    );
  }

  const jsonText = extractFirstJsonObject(streamedText);
  const result = JSON.parse(jsonText);
  process.stdout.write(JSON.stringify(result));
}

main().catch((error) => {
  process.stderr.write(`${error?.stack ?? String(error)}\n`);
  process.exit(1);
});
