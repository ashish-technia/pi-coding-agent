import process from "node:process";
import { ModelRuntime, SessionManager, createAgentSession } from "@earendil-works/pi-coding-agent";

async function main() {
  const provider = process.env.PI_PROVIDER ?? "openai";
  const apiKey = process.env.PI_PROVIDER_API_KEY;
  if (!apiKey) {
    throw new Error("PI_PROVIDER_API_KEY is required");
  }

  const modelRuntime = await ModelRuntime.create();
  await modelRuntime.setRuntimeApiKey(provider, apiKey);

  const { session } = await createAgentSession({
    cwd: process.cwd(),
    agentDir: process.cwd(),
    sessionManager: SessionManager.inMemory(process.cwd()),
    modelRuntime
  });

  session.subscribe((event) => {
    process.stdout.write(`${JSON.stringify(event)}\n`);
  });

  const result = await session.prompt("Reply with exactly: OK");
  process.stderr.write(`PROMPT_RESULT_TYPE=${typeof result}\n`);
  process.stderr.write(`PROMPT_RESULT=${JSON.stringify(result)}\n`);
  session.dispose();
}

main().catch((error) => {
  process.stderr.write(`${error?.stack ?? String(error)}\n`);
  process.exit(1);
});
