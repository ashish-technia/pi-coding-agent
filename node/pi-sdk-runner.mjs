import fs from "node:fs";
import os from "node:os";
import process from "node:process";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  createAgentSession,
} from "@earendil-works/pi-coding-agent";

/**
 * Pi's registry uses dated API ids per provider. Common short names (e.g. claude-sonnet-4) may
 * exist only under other providers — map them to real Anthropic ids before createAgentSession.
 */
const PROVIDER_MODEL_ALIASES = {
  anthropic: {
    "claude-sonnet-4": ["claude-sonnet-4-0", "claude-sonnet-4-20250514"],
  },
  openai: {
    "gpt-4o": ["gpt-4o-2024-11-20", "gpt-4o-2024-08-06", "gpt-4o-2024-05-13"],
    "gpt-4o-mini": ["gpt-4o-mini-2024-07-18"],
    "gpt-4.1": ["gpt-4.1-2025-04-14"],
    "gpt-4.1-mini": ["gpt-4.1-mini-2025-04-14"],
    "o3": ["o3-2025-04-16"],
    "o4-mini": ["o4-mini-2025-04-16"],
  },
};

/**
 * Plan mode: the agent may inspect the repository but cannot change it.
 * These are Pi's built-in read-only tools; bash/edit/write are deliberately excluded.
 */
const PLAN_MODE_TOOLS = ["read", "grep", "find", "ls"];

/**
 * Attached repositories, Claude Code's `--add-dir` in miniature.
 *
 * Pi's tools resolve an absolute path as given (see resolveToCwd in the SDK), so several
 * checkouts need no common parent and no sandbox trickery - listing them is enough. `cwd`
 * stays the primary repo, because bash has only one working directory.
 *
 * With a single root we keep the old shape exactly: no repo name anywhere, paths relative
 * to that one clone.
 */
function normaliseRoots(repoRoots, cwd) {
  const list = Array.isArray(repoRoots)
    ? repoRoots.filter((r) => r && typeof r.path === "string" && r.path.trim())
    : [];
  if (list.length === 0) return [{ name: "", path: cwd, properties: {} }];
  return list.map((r) => ({
    name: typeof r.name === "string" ? r.name : "",
    path: path.resolve(r.path),
    properties: r.properties && typeof r.properties === "object" ? r.properties : {},
  }));
}

function relPosix(from, to) {
  return path.relative(from, to).split(path.sep).join("/");
}

/** The root containing `absPath`; the deepest one wins so nested checkouts resolve inward. */
function rootFor(roots, absPath) {
  let best = null;
  for (const root of roots) {
    const rel = path.relative(root.path, absPath);
    if (rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel))) {
      if (!best || root.path.length > best.path.length) best = root;
    }
  }
  return best;
}

/** How a path is written back to Python: "rel" for one repo, "name/rel" for several. */
function canonicalKey(multi, root, rel) {
  return multi && root.name ? `${root.name}/${rel}` : rel;
}

/**
 * Resolve whatever the model wrote - absolute path, `repo` field plus relative path, or a
 * "repo/rel" string - to a concrete file. Returns null when it lands outside every root.
 */
function resolveRepoPath(roots, multi, repoName, filePath) {
  const cleaned = String(filePath ?? "").trim().replace(/\s*\(new\)\s*$/i, "");
  if (!cleaned) return null;

  if (path.isAbsolute(cleaned)) {
    const root = rootFor(roots, cleaned);
    return root ? { root, rel: relPosix(root.path, cleaned), abs: cleaned } : null;
  }

  let root = repoName ? roots.find((r) => r.name === repoName) : null;
  let rest = cleaned;
  if (!root && multi) {
    // The model wrote "api/routes/user.py" instead of filling in `repo`.
    const [head, ...tail] = cleaned.split("/");
    const match = roots.find((r) => r.name === head);
    if (match && tail.length) {
      root = match;
      rest = tail.join("/");
    }
  }
  root = root || roots[0];
  const abs = path.resolve(root.path, rest);
  // A "../.." path can climb out of the repo it started in.
  return rootFor(roots, abs) ? { root, rel: relPosix(root.path, abs), abs } : null;
}

const WRITE_TOOLS = new Set(["edit", "write"]);
const READ_TOOLS = new Set(["read", "grep", "find", "ls"]);
const SHELL_TOOLS = new Set(["bash", "powershell"]);

/**
 * Shell commands refused in execute mode. A command string is easy to disguise, so this is
 * defence in depth, not the boundary: it stops the agent doing by accident what the service
 * does itself (commit, push) and the obvious ways of moving data off the machine.
 */
const SHELL_DENYLIST = [
  [/\bgit\b[^|;&\n]*\s(push|remote|commit)\b/i, "git commit, git push and git remote are done by the service, not by the agent"],
  [
    /(^|[\s;&|(`])(curl|wget|ssh|scp|sftp|nc|ncat|telnet|ftp|iwr|irm|invoke-webrequest|invoke-restmethod)(\.exe)?(\s|$)/i,
    "network clients are not available in a run",
  ],
];

/** Where a tool path really points: `~` expanded, relative to `cwd`, symlinks followed. */
function realTarget(cwd, raw) {
  let p = String(raw ?? "").trim();
  if (p.startsWith("@")) p = p.slice(1);
  if (p === "~" || p.startsWith("~/") || p.startsWith("~\\")) p = path.join(os.homedir(), p.slice(1));
  const abs = path.resolve(cwd, p || ".");
  // Resolve links through the deepest part that exists, so a link inside a repository cannot
  // point a tool outside it and a file that is about to be created is still judged.
  let existing = abs;
  const tail = [];
  while (!fs.existsSync(existing)) {
    const parent = path.dirname(existing);
    if (parent === existing) break;
    tail.unshift(path.basename(existing));
    existing = parent;
  }
  try {
    return path.join(fs.realpathSync.native(existing), ...tail);
  } catch {
    return abs;
  }
}

/** A glob that climbs out of its search directory: absolute, or with a `..` segment. */
const escapesSearchDir = (pattern) =>
  typeof pattern === "string" && (path.isAbsolute(pattern) || /(^|[\\/])\.\.([\\/]|$)/.test(pattern));

/**
 * The pre-tool check (R-11). Returns why a call is refused, or null to let it run.
 *
 * - `edit` and `write` may only touch the attached repositories, in every mode.
 * - In a read-only mode `read`, `grep`, `find` and `ls` are held to the same roots, because a
 *   read-only session could otherwise be talked into reading the service's own files.
 * - Shell commands are checked against SHELL_DENYLIST, and refused outright when read-only.
 */
export function makeToolCheck({ roots, cwd, readOnly }) {
  const realRoots = roots.map((r) => ({ ...r, path: realTarget(r.path, ".") }));
  const names = roots.map((r) => r.name || r.path).join(", ");
  const outside = (p) => !rootFor(realRoots, realTarget(cwd, p));
  return (toolName, input) => {
    const args = input && typeof input === "object" ? input : {};
    if (WRITE_TOOLS.has(toolName)) {
      if (readOnly) return `${toolName} is not available: this session is read-only`;
      if (outside(args.path)) return `${toolName} of ${args.path} is outside the attached repositories (${names})`;
      return null;
    }
    if (SHELL_TOOLS.has(toolName)) {
      if (readOnly) return `${toolName} is not available: this session is read-only`;
      const command = String(args.command ?? "");
      for (const [pattern, why] of SHELL_DENYLIST) {
        if (pattern.test(command)) return `${toolName} command refused: ${why}`;
      }
      return null;
    }
    if (READ_TOOLS.has(toolName) && readOnly) {
      if (outside(args.path ?? ".")) {
        return `${toolName} of ${args.path} is outside the attached repositories (${names})`;
      }
      if (escapesSearchDir(toolName === "find" ? args.pattern : args.glob)) {
        return `${toolName} pattern leaves the directory it searches; search inside the attached repositories (${names})`;
      }
    }
    return null;
  };
}

/** How many correction rounds a plan gets when validation finds unverified paths. */
const PLAN_MAX_CORRECTIONS = 2;

const VALID_THINKING_LEVELS = new Set(["off", "minimal", "low", "medium", "high", "xhigh"]);

function resolveSessionModel(modelRuntime, provider, modelId) {
  const direct = modelRuntime.getModel(provider, modelId);
  if (direct) return direct;
  const chain = PROVIDER_MODEL_ALIASES[provider]?.[modelId];
  if (chain) {
    for (const id of chain) {
      const m = modelRuntime.getModel(provider, id);
      if (m) return m;
    }
  }
  return undefined;
}

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

const outputSchema = (multi) => [
  "Return ONLY one strict JSON object (no markdown, no prose outside it) with keys:",
  "  branch_name      string",
  "  commit_message   string",
  "  pr_title         string",
  "  pr_description   string (Markdown; sections: Context, Changes, Risks, Test plan)",
  multi
    ? "  files_changed    string[]  'repo-name/path' for every file, one per plan step"
    : "  files_changed    string[]  repo-relative paths, one per plan step",
  "  analysis         string    what you found in the code and why the change is needed,",
  "                             citing files and function/class names you actually read",
  multi
    ? "  plan_steps       array of { repo: string, file: string, action: 'modify'|'create'|'delete',"
    : "  plan_steps       array of { file: string, action: 'modify'|'create'|'delete',",
  "                             change: string, evidence: string }",
  multi ? "                   repo     = which attached repository, by name" : null,
  multi ? "                   file     = path relative to THAT repository's root, no repo prefix" : null,
  "                   change   = the exact edit: which function/class/block, what becomes what",
  "                   evidence = what you saw in that file that justifies the edit",
  "                              (function signature, current line of code, caller list)",
  "  verification     string[]  concrete checks to prove it works (commands, tests, manual steps)",
  "  open_questions   string[]  ambiguities the reviewer must resolve; [] if none",
  "  notes_response   string    direct reply to the reviewer's notes when refining; '' otherwise",
  "  phases           array of { name: string, description: string, step_indexes: number[] }",
  "                   0-based indexes into plan_steps. Group steps into 2-4 phases ONLY when the",
  "                   work has natural, independently reviewable stages (e.g. data model first,",
  "                   then API, then UI). Every step must appear in exactly one phase. Use []",
  "                   when the change is small enough to land at once.",
].filter((line) => line !== null);

/** The attached-repo briefing. Empty for a single repo, so those prompts are unchanged. */
function repoRootsBlock(roots, multi) {
  if (!multi) return [];
  const lines = ["", "ATTACHED REPOSITORIES (this issue spans several; all are checked out locally):"];
  roots.forEach((root, index) => {
    lines.push(`  ${root.name}${index === 0 ? "   [primary - the shell's working directory]" : ""}`);
    lines.push(`    path: ${root.path}`);
    for (const [key, value] of Object.entries(root.properties ?? {})) {
      lines.push(`    ${key}: ${value}`);
    }
  });
  lines.push(
    "How to address files:",
    "  - Tools (read, grep, find, ls, edit, write): use the ABSOLUTE path above for anything",
    "    outside the primary repo. A bare relative path only ever resolves inside the primary.",
    "  - plan_steps: put the repository name in `repo` and the path relative to that",
    "    repository's root in `file`. Never repeat the repo name inside `file`.",
    "  - Never read or write outside these roots.",
    "  - Make the repos agree: a call you add in one must match the signature you define in",
    "    the other, and both sides of a contract change belong in the same plan.",
  );
  return lines;
}

function issueBlock(issue) {
  const lines = [
    `Jira key: ${issue.key}`,
    `Project: ${issue.project_key}`,
    `Summary: ${issue.summary}`,
    `Description: ${issue.description ?? ""}`,
    `Reporter: ${issue.reporter ?? ""}`,
  ];
  const comments = Array.isArray(issue.comments) ? issue.comments : [];
  if (comments.length) {
    lines.push(`Comments (${comments.length}):`);
    for (const c of comments) {
      lines.push(`--- ${c.author || "unknown"} @ ${c.created || ""}`);
      lines.push(c.body || "");
    }
  }
  return lines;
}

function requirementsBlock(requirements) {
  if (!requirements) return [];
  const list = (label, items) =>
    Array.isArray(items) && items.length ? [`${label}:`, ...items.map((i) => `  - ${i}`)] : [];
  return [
    "",
    "APPROVED REQUIREMENTS (the human signed these off; they override the raw issue text):",
    `Title: ${requirements.title ?? ""}`,
    `Problem: ${requirements.problem ?? ""}`,
    ...list("Goals", requirements.goals),
    ...list("Acceptance criteria", requirements.acceptance_criteria),
    ...list("In scope", requirements.in_scope),
    ...list("Out of scope (do NOT implement)", requirements.out_of_scope),
    ...list("Assumptions", requirements.assumptions),
    ...list("Open questions (unresolved; choose the safest interpretation)", requirements.open_questions),
  ];
}

function reviewFeedbackBlock(feedback) {
  if (!feedback) return [];
  return [
    "",
    "REVIEW FEEDBACK ON YOUR PREVIOUS ATTEMPT (the reviewer rejected it; fix every point):",
    feedback,
  ];
}

/** "repo/path" when the plan spans repositories, plain path otherwise. */
const stepLabel = (s) => (s.repo ? `${s.repo}/${s.file}` : s.file);

function planBlock(plan) {
  if (!plan) return [];
  const lines = ["", "APPROVED PLAN (this is your work order; implement it fully):"];
  if (plan.analysis) lines.push(`Analysis: ${plan.analysis}`);
  const steps = Array.isArray(plan.plan_steps) ? plan.plan_steps : [];
  if (steps.length) {
    lines.push("Steps:");
    steps.forEach((s, i) => {
      lines.push(`  ${i + 1}. [${s.action ?? "modify"}] ${stepLabel(s)}: ${s.change}`);
      if (s.evidence) lines.push(`     evidence: ${s.evidence}`);
    });
  }
  if (Array.isArray(plan.verification) && plan.verification.length) {
    lines.push("Verification to run after editing:");
    plan.verification.forEach((v) => lines.push(`  - ${v}`));
  }
  if (Array.isArray(plan.open_questions) && plan.open_questions.length) {
    lines.push("Open questions the reviewer accepted as-is (use your best judgement):");
    plan.open_questions.forEach((q) => lines.push(`  - ${q}`));
  }
  if (plan.pr_description) lines.push(`PR description draft:\n${plan.pr_description}`);
  return lines;
}

function previousPlanBlock(plan) {
  if (!plan) return [];
  const lines = ["", "YOUR PREVIOUS PLAN (the reviewer has read this):"];
  if (plan.analysis) lines.push(`Analysis: ${plan.analysis}`);
  const steps = Array.isArray(plan.plan_steps) ? plan.plan_steps : [];
  steps.forEach((s, i) => {
    lines.push(`  ${i + 1}. [${s.action ?? "modify"}] ${stepLabel(s)}: ${s.change}`);
    if (s.evidence) lines.push(`     evidence: ${s.evidence}`);
  });
  if (Array.isArray(plan.verification) && plan.verification.length) {
    lines.push("Verification:");
    plan.verification.forEach((v) => lines.push(`  - ${v}`));
  }
  if (Array.isArray(plan.open_questions) && plan.open_questions.length) {
    lines.push("Open questions:");
    plan.open_questions.forEach((q) => lines.push(`  - ${q}`));
  }
  return lines;
}

function buildRefinePrompt(issue, systemPrompt, modelId, previousPlan, reviewerNotes, requirements, roots, multi) {
  return [
    `System guidance: ${systemPrompt}`,
    `Preferred model id: ${modelId}`,
    "",
    "MODE: PLAN (REFINEMENT). You can read this repository (read, grep, find, ls) but you",
    "cannot modify it. A human reviewer read your previous plan and replied with notes.",
    "",
    "What to do:",
    "1. Read the reviewer's notes carefully. They may ask questions, request changes to the",
    "   plan, or point out something you missed.",
    "2. Investigate only what the notes require: re-read the files you plan to change (you",
    "   must open each one with the read tool again in this session) and grep for anything",
    "   the notes mention, such as callers, consumers, configs or tests.",
    "3. Answer every question in the notes explicitly in notes_response, citing the files",
    "   and symbols you checked. If the answer changes the plan, change the plan.",
    "4. Return the COMPLETE revised plan, not a diff of it. Keep steps that are still",
    "   correct; fix, add or remove steps the notes call for.",
    "",
    "Hard rules:",
    "- Every path in plan_steps must be a real repo-relative path you opened with the read",
    "  tool in this session, or a new file with action 'create'.",
    "- Every 'modify' step must name the exact function/class/block and the precise edit.",
    "- evidence must quote or paraphrase what is currently in the file at that spot.",
    "- verification must be runnable in this repo.",
    "- Do not create, edit or delete any files.",
    "",
    ...outputSchema(multi),
    ...repoRootsBlock(roots, multi),
    "",
    ...issueBlock(issue),
    ...requirementsBlock(requirements),
    ...previousPlanBlock(previousPlan),
    "",
    "REVIEWER NOTES:",
    reviewerNotes,
  ].join("\n");
}

function buildPlanPrompt(issue, systemPrompt, modelId, requirements, roots, multi) {
  return [
    `System guidance: ${systemPrompt}`,
    `Preferred model id: ${modelId}`,
    "",
    "MODE: PLAN. You can read this repository (read, grep, find, ls) but you cannot modify it.",
    "Your output is an implementation plan that another engineer will execute verbatim,",
    "so it must be grounded in the actual code, not in the issue text alone.",
    "",
    "Procedure (do all of it before answering):",
    "1. Orient: ls the repo root; read README / package / pyproject / config files to learn the",
    "   stack, entry points and conventions.",
    "2. Locate: grep for every identifier, endpoint, config key, error string or feature named",
    "   in the issue. Follow the hits to the defining files.",
    "3. Read: open every file you intend to change and read the relevant functions in full.",
    "   Trace callers and callees of the code you will touch (grep for the symbol names).",
    "4. Check tests and docs: find existing tests or docs covering this area and plan updates.",
    "5. Decide: pick the smallest change set that fully satisfies every acceptance criterion",
    "   in the approved requirements and nothing in their out-of-scope list.",
    "6. Split into phases only if the work has independently reviewable stages.",
    "",
    "Hard rules:",
    "- Every path in plan_steps and files_changed must be a real repo-relative path you opened",
    "  with the read tool, or a new file with action 'create'. Never guess a path.",
    "- Every 'modify' step must name the exact function/class/block and describe the edit",
    "  precisely enough to implement without re-deriving it.",
    "- evidence must quote or paraphrase what is currently in the file at that spot.",
    "- verification must be runnable in this repo (real test command, real endpoint, real file)",
    "  and must cover each acceptance criterion.",
    "- If a requirement cannot be implemented as described, say so in open_questions and plan the",
    "  closest safe change.",
    "- Do not create, edit or delete any files.",
    "",
    ...outputSchema(multi),
    ...repoRootsBlock(roots, multi),
    "",
    ...issueBlock(issue),
    ...requirementsBlock(requirements),
  ].join("\n");
}

function buildExecutePrompt(issue, systemPrompt, modelId, branchName, plan, requirements, reviewFeedback, roots, multi) {
  return [
    `System guidance: ${systemPrompt}`,
    `Preferred model id: ${modelId}`,
    "",
    "MODE: EXECUTE. Apply the required code changes in the current repository for the Jira issue.",
    plan
      ? "Implement every step of the approved plan below. Re-read each file before editing it;"
      : "Explore the repository first (read, grep, find, ls), then make the change.",
    plan
      ? "if the code differs from the plan's evidence, adapt the edit to the real code and say so."
      : null,
    "After editing, run the verification steps that can run here (tests, linters, imports) with",
    "the bash tool and fix what they surface. Do not run git commit, git push or git checkout.",
    "This is a fresh checkout: dependencies are not installed. Do not install them; skip the",
    "checks that need them.",
    multi
      ? "The bash tool always starts in the primary repo; `cd` to another repository's absolute path first to verify it."
      : null,
    branchName ? `Use this branch name in output: ${branchName}` : "Choose a branch name.",
    "files_changed must list exactly the files you modified or created.",
    "In plan_steps, report what you actually did per file (evidence = what you verified).",
    "Do not touch anything listed as out of scope in the approved requirements.",
    "",
    ...outputSchema(multi),
    ...repoRootsBlock(roots, multi),
    "",
    ...issueBlock(issue),
    ...requirementsBlock(requirements),
    ...planBlock(plan),
    ...reviewFeedbackBlock(reviewFeedback),
  ]
    .filter((line) => line !== null)
    .join("\n");
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
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  let out = "";
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    if (block.type === "text" && typeof block.text === "string") {
      out += block.text;
    } else if (block.type === "thinking" && typeof block.thinking === "string") {
      out += block.thinking;
    } else if (block.type === "toolCall" && block.arguments != null) {
      try {
        out += typeof block.arguments === "string" ? block.arguments : JSON.stringify(block.arguments);
      } catch {
        out += String(block.arguments);
      }
    } else if (typeof block.text === "string") {
      out += block.text;
    }
  }
  return out;
}

function summarizeAssistantMessage(msg) {
  if (!msg || msg.role !== "assistant") return "(not assistant)";
  const sr = msg.stopReason ?? "?";
  const err = msg.errorMessage ? ` errorMessage=${JSON.stringify(msg.errorMessage)}` : "";
  const c = msg.content;
  if (c == null) return `stopReason=${sr}${err} content=null`;
  if (typeof c === "string") return `stopReason=${sr}${err} content.len=${c.length}`;
  if (!Array.isArray(c)) return `stopReason=${sr}${err} contentType=${typeof c}`;
  const types = c.map((b) => (b && typeof b === "object" && b.type ? b.type : "?")).join(",");
  return `stopReason=${sr}${err} blocks=${c.length} types=[${types}]`;
}

/** Reliable: agent state is updated before async session listeners run. */
function lastAssistantTextFromAgentState(session) {
  const messages = session?.messages;
  if (!Array.isArray(messages)) return "";
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (!msg || msg.role !== "assistant") continue;
    if (msg.stopReason === "error" || msg.stopReason === "aborted") {
      const err = msg.errorMessage || msg.stopReason || "assistant error";
      throw new Error(`Pi assistant failed: ${err}`);
    }
    const t = textFromContentBlocks(msg.content);
    if (t.trim()) return t;
  }
  return "";
}

/** Normalise a path the agent reported to a repo-relative POSIX form. */
/** A path as it should appear in logs and activity events. */
function displayPath(roots, multi, p) {
  const resolved = resolveRepoPath(roots, multi, "", p);
  if (resolved) return canonicalKey(multi, resolved.root, resolved.rel);
  // Outside every attached repo: show it verbatim rather than a misleading relative path.
  return typeof p === "string" ? p.trim() : "";
}

/**
 * Coerce the model's JSON into the AgentResult contract so Python always gets
 * the structured fields, even from a model that omits the optional ones.
 */
function normaliseResult(raw, roots, multi, rejected) {
  const result = { ...raw };
  result.analysis = typeof result.analysis === "string" ? result.analysis : "";
  result.notes_response = typeof result.notes_response === "string" ? result.notes_response : "";
  result.verification = Array.isArray(result.verification) ? result.verification.map(String) : [];
  result.open_questions = Array.isArray(result.open_questions) ? result.open_questions.map(String) : [];
  const steps = Array.isArray(result.plan_steps) ? result.plan_steps : [];
  result.plan_steps = steps
    .filter((s) => s && typeof s === "object" && typeof s.file === "string")
    .map((s) => {
      const resolved = resolveRepoPath(roots, multi, typeof s.repo === "string" ? s.repo : "", s.file);
      if (!resolved) {
        // Outside every attached repo. Dropped here and reported by the caller, so a plan
        // never reaches Python with a path nobody authorised.
        rejected.push(String(s.file));
        return null;
      }
      return {
        // `repo` stays empty on single-repo runs, where there is nothing to disambiguate.
        repo: multi ? resolved.root.name : "",
        file: resolved.rel,
        action: ["modify", "create", "delete"].includes(s.action) ? s.action : "modify",
        change: typeof s.change === "string" ? s.change : "",
        evidence: typeof s.evidence === "string" ? s.evidence : "",
      };
    })
    .filter(Boolean);
  const declared = (Array.isArray(result.files_changed) ? result.files_changed : [])
    .map((f) => {
      const resolved = resolveRepoPath(roots, multi, "", f);
      if (!resolved) {
        rejected.push(String(f));
        return "";
      }
      return canonicalKey(multi, resolved.root, resolved.rel);
    });
  const fromSteps = result.plan_steps.map(stepLabel);
  result.files_changed = [...new Set([...declared, ...fromSteps])].filter(Boolean);

  // Phases: keep only well-formed ones; drop the grouping entirely if it does not cover
  // every step exactly once, so the Python side never sees an inconsistent split.
  const stepCount = result.plan_steps.length;
  const rawPhases = Array.isArray(result.phases) ? result.phases : [];
  const phases = rawPhases
    .filter((p) => p && typeof p === "object" && typeof p.name === "string")
    .map((p) => ({
      name: p.name,
      description: typeof p.description === "string" ? p.description : "",
      step_indexes: (Array.isArray(p.step_indexes) ? p.step_indexes : [])
        .map((i) => Number(i))
        .filter((i) => Number.isInteger(i) && i >= 0 && i < stepCount),
    }))
    .filter((p) => p.step_indexes.length > 0);
  const covered = phases.flatMap((p) => p.step_indexes);
  const coversAllOnce = covered.length === stepCount && new Set(covered).size === stepCount;
  result.phases = phases.length >= 2 && coversAllOnce ? phases : [];
  return result;
}

/**
 * Plan-mode guardrail: the plan must only reference files that exist and that the
 * agent actually opened (or explicitly marks as new). Returns a list of problems.
 */
function validatePlan(result, roots, multi, readPaths, reviewerNotes, rejected) {
  const problems = [];
  const repoNames = roots.map((r) => r.name || "the repository").join(", ");
  for (const bad of rejected) {
    problems.push(`${bad}: is outside every attached repository (${repoNames}). Plan only files inside them.`);
  }
  if (!result.analysis.trim()) problems.push("analysis is empty; describe what you found in the code.");
  if (reviewerNotes && !result.notes_response.trim()) {
    problems.push("notes_response is empty; reply to the reviewer's notes explicitly.");
  }
  if (result.plan_steps.length === 0) problems.push("plan_steps is empty; list one concrete edit per file.");
  for (const step of result.plan_steps) {
    // Steps are already normalised, so this resolves; it is how we reach the real file.
    const resolved = resolveRepoPath(roots, multi, step.repo, step.file);
    const label = stepLabel(step);
    const where = multi && step.repo ? `repo ${step.repo}` : "the repository";
    const exists = Boolean(resolved && fs.existsSync(resolved.abs));
    if (step.action === "create") {
      if (exists) problems.push(`${label}: marked 'create' but it already exists; use 'modify'.`);
      continue;
    }
    if (!exists) {
      problems.push(`${label}: does not exist in ${where}. Use find/ls to locate the real path.`);
      continue;
    }
    if (!readPaths.has(label)) {
      problems.push(`${label}: you did not open it with the read tool. Read it before planning edits to it.`);
    }
    if (!step.change.trim()) problems.push(`${label}: change is empty.`);
    if (!step.evidence.trim()) problems.push(`${label}: evidence is empty; cite what is currently in the file.`);
  }
  if (result.verification.length === 0) problems.push("verification is empty; give at least one runnable check.");
  return problems;
}

/**
 * Run one Pi session for `parsed` (the payload Python writes on stdin) and return the result.
 *
 * `deps` exists for tests: `modelRuntime` and `model` replace the real provider (Pi ships a
 * scripted one), and `emit` receives the live events instead of stderr.
 */
export async function runAgent(parsed, deps = {}) {
  const issue = parsed.issue;
  const modelId = parsed.model;
  const systemPrompt = parsed.systemPrompt;
  const provider = parsed.provider ?? "openai";
  const agentDirInput = parsed.agentDir ?? ".pi-agent";
  const repoCwdInput = parsed.repoCwd ?? process.cwd();
  const executeChanges = parsed.executeChanges ?? false;
  const branchName = parsed.branchName ?? "";
  const plan = parsed.plan ?? null;
  const requirements = parsed.requirements ?? null;
  const reviewerNotes = typeof parsed.reviewerNotes === "string" ? parsed.reviewerNotes.trim() : "";
  const reviewFeedback = typeof parsed.reviewFeedback === "string" ? parsed.reviewFeedback.trim() : "";
  const thinkingLevel = VALID_THINKING_LEVELS.has(parsed.thinkingLevel) ? parsed.thinkingLevel : "medium";
  // What the run may still spend, in USD (R-14). Absent means the run has no budget.
  const maxCostUsd = typeof parsed.maxCostUsd === "number" && parsed.maxCostUsd >= 0 ? parsed.maxCostUsd : null;
  const cwd = path.resolve(repoCwdInput);
  const agentDir = path.resolve(agentDirInput);
  // `cwd` is the primary repo (bash has only one working directory); `roots` is every repo
  // this run attached. One root behaves exactly as before, prefixes and all.
  const roots = normaliseRoots(parsed.repoRoots, cwd);
  const multi = roots.length > 1;

  // Pi 1.x: one ModelRuntime owns credentials and the model catalogue. The catalogue is the
  // one bundled with the pinned SDK and is not refreshed over the network here, so the same
  // SDK version always resolves the same models.
  let modelRuntime = deps.modelRuntime;
  if (!modelRuntime) {
    const providerApiKey = process.env.PI_PROVIDER_API_KEY;
    if (!providerApiKey) {
      throw new Error("PI_PROVIDER_API_KEY env var is required.");
    }
    modelRuntime = await ModelRuntime.create();
    await modelRuntime.setRuntimeApiKey(provider, providerApiKey);
  }

  const resolvedModel = deps.model ?? resolveSessionModel(modelRuntime, provider, modelId);
  if (!resolvedModel) {
    throw new Error(
      `Unknown model for provider "${provider}" id "${modelId}". ` +
        `Use a full Pi registry id (e.g. anthropic: claude-sonnet-4-20250514 or claude-sonnet-4-0).`
    );
  }

  const eventTypes = [];
  const toolCalls = {};
  const readPaths = new Set();
  const pendingReads = new Map();
  const blockedCalls = [];
  let turn = 0;
  const startedAt = Date.now();

  /**
   * Live activity for the Python side: one JSON object per line on stderr, prefixed with
   * "@@PI ". Python forwards these to the UI while the agent is still running.
   */
  const emit = (payload) => {
    const event = { t: Math.round((Date.now() - startedAt) / 1000), ...payload };
    if (deps.emit) deps.emit(event);
    else process.stderr.write(`@@PI ${JSON.stringify(event)}\n`);
  };

  // The pre-tool hook: Pi asks every `tool_call` handler before a tool runs, and a handler
  // that throws blocks the call too, so this fails closed.
  const checkToolCall = makeToolCheck({ roots, cwd, readOnly: !executeChanges });
  const toolGuard = (pi) => {
    pi.on("tool_call", (event) => {
      const reason = checkToolCall(event.toolName, event.input);
      if (!reason) return undefined;
      blockedCalls.push(`${event.toolName}: ${reason}`);
      emit({ ev: "blocked", tool: event.toolName, text: reason });
      return { block: true, reason: `Blocked by the run's policy: ${reason}.` };
    });
  };
  // `noExtensions`: extension files are code. Nothing from the repository under work, or from
  // the service account's own Pi configuration, is loaded into this process; only the guard.
  const resourceLoader = new DefaultResourceLoader({
    cwd,
    agentDir,
    noExtensions: true,
    extensionFactories: [toolGuard],
  });
  await resourceLoader.reload();

  const sessionOptions = {
    cwd,
    agentDir,
    sessionManager: SessionManager.inMemory(cwd),
    modelRuntime,
    model: resolvedModel,
    resourceLoader,
    // The SDK clamps this to "off" for models without reasoning support.
    thinkingLevel,
  };
  if (!executeChanges) {
    // Plan mode: read-only allowlist. Omitting `tools` would enable bash/edit/write.
    sessionOptions.tools = PLAN_MODE_TOOLS;
  }

  const { session } = await createAgentSession(sessionOptions);

  let overBudget = false;
  /** Tokens and cost of this session so far, as Pi counts them. */
  const usageNow = () => {
    const stats = session.getSessionStats();
    return {
      input: stats.tokens.input,
      output: stats.tokens.output,
      cache_read: stats.tokens.cacheRead,
      cache_write: stats.tokens.cacheWrite,
      // Pi prices the session from its catalogue. `deps.costOf` is for tests only: the
      // scripted provider reports tokens but always a zero cost.
      cost: deps.costOf ? deps.costOf(stats) : stats.cost,
    };
  };

  const summariseArgs = (toolName, args) => {
    if (!args || typeof args !== "object") return "";
    const show = (p) => displayPath(roots, multi, p);
    if (toolName === "read") return `${show(args.path)}${args.offset ? ` @${args.offset}` : ""}`;
    if (toolName === "grep") return `/${args.pattern}/ in ${args.path ? show(args.path) : "."}`;
    if (toolName === "find") return `${args.pattern} in ${args.path ? show(args.path) : "."}`;
    if (toolName === "ls") return args.path ? show(args.path) : ".";
    if (toolName === "bash") return String(args.command ?? "").slice(0, 200);
    if (toolName === "edit" || toolName === "write") return show(args.path);
    return JSON.stringify(args).slice(0, 200);
  };

  emit({
    ev: "start",
    mode: executeChanges ? "execute" : reviewerNotes ? "refine" : "plan",
    model: `${resolvedModel.provider}/${resolvedModel.id}`,
    thinking: thinkingLevel,
    tools: executeChanges ? "read, bash, edit, write" : PLAN_MODE_TOOLS.join(", "),
    repos: roots.map((r) => r.name).filter(Boolean).join(", "),
  });

  session.subscribe((event) => {
    if (event?.type) {
      eventTypes.push(
        event.assistantMessageEvent?.type ? `${event.type}:${event.assistantMessageEvent.type}` : event.type
      );
    }
    if (event?.type === "message_start" && event.message?.role === "assistant") {
      turn += 1;
      emit({ ev: "model_turn", turn });
    }
    if (event?.type === "message_end" && event.message?.role === "assistant") {
      if (maxCostUsd !== null && !overBudget && usageNow().cost > maxCostUsd) {
        // Stop here rather than let a long session run on after the money is gone.
        overBudget = true;
        void session.abort();
      }
      const text = textFromContentBlocks(
        Array.isArray(event.message.content)
          ? event.message.content.filter((b) => b && b.type === "text")
          : event.message.content
      ).trim();
      if (text) emit({ ev: "assistant", turn, text: text.slice(0, 400), chars: text.length });
    }
    if (event?.type === "tool_execution_start" && event.toolName) {
      toolCalls[event.toolName] = (toolCalls[event.toolName] ?? 0) + 1;
      if (event.toolName === "read" && event.args?.path) {
        pendingReads.set(event.toolCallId, event.args.path);
      }
      emit({ ev: "tool", tool: event.toolName, args: summariseArgs(event.toolName, event.args), id: event.toolCallId });
    }
    if (event?.type === "tool_execution_end" && event.toolName) {
      const readPath = pendingReads.get(event.toolCallId);
      pendingReads.delete(event.toolCallId);
      if (readPath && !event.isError) {
        // Only a read that returned content counts; a blocked or failed one did not show the
        // file. Keyed the same way plan steps are, so "did you read this file?" can be answered.
        readPaths.add(displayPath(roots, multi, readPath));
      }
      let size = 0;
      const r = event.result;
      if (typeof r === "string") size = r.length;
      else if (r && typeof r === "object") {
        const c = r.content;
        size = Array.isArray(c) ? c.reduce((n, b) => n + (typeof b?.text === "string" ? b.text.length : 0), 0) : JSON.stringify(r).length;
      }
      emit({ ev: "tool_done", tool: event.toolName, id: event.toolCallId, error: Boolean(event.isError), size });
    }
  });

  // Paths the model named that fall outside every attached repo, refreshed on each parse.
  let rejected = [];

  const promptWithinBudget = async (text) => {
    await session.prompt(text);
    if (!overBudget) return;
    const used = usageNow();
    // A failed session returns nothing to Python, so this event is the only record of it.
    emit({ ev: "usage", ...used });
    session.dispose();
    throw new Error(
      `The run reached its budget: this session spent $${used.cost.toFixed(2)} of the ` +
        `$${maxCostUsd.toFixed(2)} the run had left. Raise RUN_BUDGET_USD, restart and retry.`
    );
  };

  const collectResult = () => {
    // Do not rely on session.subscribe for text: AgentSession forwards events asynchronously,
    // so prompt() can return before user handlers run. Agent state is updated synchronously.
    const streamedText = lastAssistantTextFromAgentState(session);
    if (!streamedText.trim()) {
      const msgs = session?.messages ?? [];
      const lastAssistant = [...msgs].reverse().find((m) => m?.role === "assistant");
      throw new Error(
        `Pi returned empty assistant text. provider=${provider} model=${modelId} ` +
          `resolvedModel=${resolvedModel.provider}/${resolvedModel.id} agentDir=${agentDir} ` +
          `seenEvents=${JSON.stringify(eventTypes.slice(0, 30))} ` +
          `lastAssistant=${summarizeAssistantMessage(lastAssistant)}`
      );
    }
    rejected = [];
    return normaliseResult(JSON.parse(extractFirstJsonObject(streamedText)), roots, multi, rejected);
  };

  const isRefine = !executeChanges && Boolean(reviewerNotes);
  const mode = executeChanges ? "execute" : isRefine ? "refine" : "plan";
  let firstPrompt;
  if (executeChanges) {
    firstPrompt = buildExecutePrompt(issue, systemPrompt, modelId, branchName, plan, requirements, reviewFeedback, roots, multi);
  } else if (isRefine) {
    firstPrompt = buildRefinePrompt(issue, systemPrompt, modelId, plan, reviewerNotes, requirements, roots, multi);
  } else {
    firstPrompt = buildPlanPrompt(issue, systemPrompt, modelId, requirements, roots, multi);
  }

  await promptWithinBudget(firstPrompt);
  let result = collectResult();

  let corrections = 0;
  if (executeChanges && rejected.length) {
    // The pre-tool hook already refuses an edit or write outside the roots. This is the
    // second line: a file changed some other way (a shell command) and then reported.
    // Fail loudly rather than hand Python a result that touched something nobody attached.
    throw new Error(
      `The coding agent reported files outside the attached repositories ` +
        `(${roots.map((r) => r.name || r.path).join(", ")}): ${[...new Set(rejected)].join(", ")}`
    );
  }
  if (!executeChanges) {
    let problems = validatePlan(result, roots, multi, readPaths, reviewerNotes, rejected);
    while (problems.length > 0 && corrections < PLAN_MAX_CORRECTIONS) {
      corrections += 1;
      process.stderr.write(
        `[pi-runner] plan validation round ${corrections}: ${problems.length} problem(s)\n  - ${problems.join("\n  - ")}\n`
      );
      emit({ ev: "validation", round: corrections, problems: problems.slice(0, 10) });
      await promptWithinBudget(
        [
          "Your plan failed validation against the repository. Fix every item below, using the",
          "read/grep/find/ls tools as needed, then return the complete corrected JSON object again.",
          "",
          ...problems.map((p) => `- ${p}`),
        ].join("\n")
      );
      result = collectResult();
      problems = validatePlan(result, roots, multi, readPaths, reviewerNotes, rejected);
    }
    if (problems.length > 0) {
      throw new Error(
        `Plan still invalid after ${corrections} correction round(s):\n- ${problems.join("\n- ")}`
      );
    }
  }

  // Operational trace on stderr (stdout is reserved for the JSON contract).
  const toolSummary = Object.entries(toolCalls)
    .map(([name, count]) => `${name}x${count}`)
    .join(" ");
  process.stderr.write(
    `[pi-runner] mode=${mode} thinking=${thinkingLevel} tools=[${toolSummary || "none"}] ` +
      `filesRead=${readPaths.size} planSteps=${result.plan_steps.length} corrections=${corrections} ` +
      `blocked=${blockedCalls.length}\n`
  );
  emit({
    ev: "done",
    mode,
    tools: toolSummary || "none",
    files_read: readPaths.size,
    plan_steps: result.plan_steps.length,
    corrections,
    blocked: blockedCalls.length,
  });

  result.usage = usageNow();
  emit({ ev: "usage", ...result.usage });
  session.dispose();
  return result;
}

async function main() {
  const result = await runAgent(JSON.parse(await readStdin()));
  process.stdout.write(JSON.stringify(result));
}

// Only when run as the script: tests import this module and call runAgent themselves.
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`${error?.stack ?? String(error)}\n`);
    process.exit(1);
  });
}
