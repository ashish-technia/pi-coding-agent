// R-51: review mode reads the change through its own tools, cannot modify anything, and
// only keeps findings about files it actually opened.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { runAgent } from "../pi-sdk-runner.mjs";
import { answer, call, payload, scripted, tempDir } from "./helpers.mjs";

const git = (cwd, ...args) =>
  execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false", ...args], {
    cwd,
    encoding: "utf8",
  }).trim();

/** A repository with one base commit and one commit of changes; returns the range to review. */
function changedRepo(before, after) {
  const repo = tempDir(before);
  git(repo.dir, "init", "-q");
  git(repo.dir, "add", "-A");
  git(repo.dir, "commit", "-q", "-m", "base");
  const base = git(repo.dir, "rev-parse", "HEAD");
  for (const [rel, content] of Object.entries(after)) {
    const target = path.join(repo.dir, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  }
  git(repo.dir, "add", "-A");
  git(repo.dir, "commit", "-q", "-m", "change");
  return { ...repo, range: { base, tree: git(repo.dir, "rev-parse", "HEAD") } };
}

const reviewPayload = (repo, review = {}, extra = {}) =>
  payload(repo.dir, {
    mode: "review",
    review: { diff: { "": repo.range }, rules: "## Must\n- No secrets in code", ...review },
    ...extra,
  });

/** The text of the last message the model was sent. */
const lastText = (context) =>
  context.messages
    .at(-1)
    .content.map((block) => block.text ?? "")
    .join("");

test("findings are numbered by severity and only grounded ones survive", async () => {
  const repo = changedRepo(
    {
      "src/app.js": "export const greet = () => 'hi';\n",
      "src/other.js": "export const x = 1;\n",
      "package-lock.json": "{}\n",
    },
    {
      "src/app.js": "export const greet = (name) => 'hi ' + name.trim();\n",
      "src/other.js": "export const x = 2;\n",
      "package-lock.json": '{"lockfileVersion": 3}\n',
    },
  );
  try {
    const finding = (file, severity, category, claim) => ({ file, line: file ? 1 : 0, severity, category, claim, suggestion: "" });
    const { deps, events } = await scripted([
      call("changed_files", {}),
      call("file_diff", { path: "src/app.js" }),
      answer({
        summary: "One crash on a missing name.",
        findings: [
          finding("src/app.js", "should", "tests", "No test covers greet"),
          finding("src/app.js", "must", "bug", "greet(undefined) throws"),
          finding("src/other.js", "must", "bug", "x changed silently"),
          finding("", "must", "unmet_criterion", "Nothing says goodbye; searched for bye"),
          finding("", "must", "bug", "Something is off somewhere"),
        ],
      }),
    ]);
    const result = await runAgent(reviewPayload(repo), deps);

    assert.deepEqual(
      result.findings.map((f) => [f.number, f.severity, f.category, f.file, f.line]),
      [
        [1, "must", "bug", "src/app.js", 1],
        [2, "must", "unmet_criterion", "", null],
        [3, "should", "tests", "src/app.js", 1],
      ],
    );
    assert.equal(result.dropped_findings.length, 2);
    assert.match(result.dropped_findings[0], /src\/other\.js: the reviewer never opened this file/);
    // The lockfile is exempt; the file nobody opened is named.
    assert.deepEqual(result.not_reviewed, ["src/other.js"]);
    assert.deepEqual(result.files_changed.sort(), ["package-lock.json", "src/app.js", "src/other.js"]);
    assert.ok(result.usage.input > 0);

    const start = events.find((e) => e.ev === "start");
    assert.equal(start.mode, "review");
    assert.equal(start.tools, "read, grep, find, ls, changed_files, file_diff");
  } finally {
    repo.cleanup();
  }
});

test("the diff tools show the change, new files included, without a shell", async () => {
  const repo = changedRepo({ "a.txt": "one\n" }, { "a.txt": "two\n", "new/b.txt": "brand new\n" });
  try {
    const seen = [];
    const { deps } = await scripted([
      call("changed_files", {}),
      call("file_diff", { path: "new/b.txt" }),
      call("file_diff", { path: "README.md" }),
      (context) => {
        // What the tools returned, as the model would see it.
        for (const message of context.messages) {
          if (message.role !== "toolResult") continue;
          seen.push(message.content.map((block) => block.text ?? "").join(""));
        }
        return answer({ summary: "ok", findings: [] });
      },
    ]);
    const result = await runAgent(reviewPayload(repo), deps);

    assert.match(seen[0], /2 file\(s\) changed/);
    assert.match(seen[0], /new\/b\.txt {2}\+1 -0/);
    assert.match(seen[1], /\+brand new/);
    assert.match(seen[2], /README\.md is not part of this change/);
    assert.deepEqual(result.not_reviewed, ["a.txt"]);
    assert.deepEqual(result.findings, []);
  } finally {
    repo.cleanup();
  }
});

test("a review session cannot write, run a shell or read outside the repository", async () => {
  const repo = changedRepo({ "a.txt": "one\n" }, { "a.txt": "two\n" });
  const other = tempDir({ "secret.env": "TOKEN=1" });
  try {
    const { deps, events } = await scripted([
      call("write", { path: "a.txt", content: "owned" }),
      call("bash", { command: "echo owned > a.txt" }),
      call("read", { path: path.join(other.dir, "secret.env") }),
      answer({ summary: "ok", findings: [] }),
    ]);
    await runAgent(reviewPayload(repo), deps);

    assert.equal(fs.readFileSync(path.join(repo.dir, "a.txt"), "utf8"), "two\n");
    assert.deepEqual(
      events.filter((e) => e.ev === "tool_done").map((e) => e.error),
      [true, true, true],
    );
  } finally {
    repo.cleanup();
    other.cleanup();
  }
});

test("a re-review reports which earlier findings are fixed", async () => {
  const repo = changedRepo({ "a.txt": "one\n" }, { "a.txt": "two\n" });
  try {
    const prompts = [];
    const { deps } = await scripted([
      (context) => {
        prompts.push(lastText(context));
        return call("file_diff", { path: "a.txt" });
      },
      answer({
        summary: "One left.",
        findings: [{ file: "a.txt", line: 1, severity: "must", category: "bug", claim: "still wrong", suggestion: "" }],
        resolved: [1, 7, 1],
      }),
    ]);
    const previous = [
      { number: 1, severity: "must", file: "a.txt", line: 1, repo: "", claim: "says one" },
      { number: 2, severity: "must", file: "a.txt", line: 1, repo: "", claim: "no newline" },
    ];
    const result = await runAgent(reviewPayload(repo, { previous, notes: "Also keep the newline." }), deps);

    assert.match(prompts[0], /THIS IS A RE-REVIEW/);
    assert.match(prompts[0], /1\. \[must\] a\.txt:1 says one/);
    assert.match(prompts[0], /Also keep the newline\./);
    // 7 was never a finding, and 1 is only counted once.
    assert.deepEqual(result.resolved, [1]);
    assert.equal(result.findings.length, 1);
  } finally {
    repo.cleanup();
  }
});

test("without a requirement or an issue the reviewer is told not to look for missing features", async () => {
  const repo = changedRepo({ "a.txt": "one\n" }, { "a.txt": "two\n" });
  try {
    const prompts = [];
    const { deps } = await scripted([
      (context) => {
        prompts.push(lastText(context));
        return answer({ summary: "ok", findings: [] });
      },
    ]);
    await runAgent(reviewPayload(repo, {}, { issue: null }), deps);
    assert.match(prompts[0], /There is no approved requirement for this change/);
    assert.match(prompts[0], /No secrets in code/);
    assert.doesNotMatch(prompts[0], /Jira key/);
  } finally {
    repo.cleanup();
  }
});

test("several repositories: files are labelled with their repository", async () => {
  const web = changedRepo({ "ui.js": "a\n" }, { "ui.js": "b\n" });
  const api = changedRepo({ "routes.py": "a\n" }, { "routes.py": "b\n" });
  try {
    const { deps } = await scripted([
      call("file_diff", { repo: "api", path: "routes.py" }),
      answer({
        summary: "ok",
        findings: [
          {
            repo: "api",
            file: "routes.py",
            line: 1,
            severity: "must",
            category: "consistency",
            claim: "ui.js still calls the old route",
            suggestion: "",
          },
        ],
      }),
    ]);
    const result = await runAgent(
      payload(web.dir, {
        mode: "review",
        repoRoots: [
          { name: "web", path: web.dir },
          { name: "api", path: api.dir },
        ],
        review: { diff: { web: web.range, api: api.range } },
      }),
      deps,
    );
    assert.deepEqual(result.files_changed.sort(), ["api/routes.py", "web/ui.js"]);
    assert.deepEqual(result.not_reviewed, ["web/ui.js"]);
    assert.equal(result.findings[0].repo, "api");
    assert.equal(result.findings[0].file, "routes.py");
  } finally {
    web.cleanup();
    api.cleanup();
  }
});
