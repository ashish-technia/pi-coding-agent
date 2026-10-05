import re
from typing import Any, Literal

from pydantic import BaseModel, Field

_BLOCK_NODE_TYPES = {
    "paragraph",
    "heading",
    "blockquote",
    "codeBlock",
    "panel",
}

_JIRA_SMART_LINK_RE = re.compile(r"\[(?P<label>[^\[\]\|]+)\|(?P<url>https?://[^\[\]\|]+)(?:\|smart-link)?\]")
_JIRA_ATTACHMENT_REF_RE = re.compile(r"\[\^([^\]]+)\]")
_JIRA_MEDIA_RE = re.compile(r"!(?P<name>[^!|]+)(?:\|[^!]*)?!")


def _normalize_text_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank:
            if not previous_blank:
                normalized.append("")
            previous_blank = True
            continue
        normalized.append(line)
        previous_blank = False
    return "\n".join(normalized).strip()


def _normalize_list_item_text(text: str) -> str:
    cleaned = _normalize_text_lines(text)
    if not cleaned:
        return ""

    lines = cleaned.splitlines()
    if len(lines) == 1:
        return lines[0]

    first, rest = lines[0], lines[1:]
    indented_rest = "\n".join(f"  {line}" if line else "" for line in rest)
    return f"{first}\n{indented_rest}".rstrip()


def _replace_jira_smart_link(match: re.Match[str]) -> str:
    label = match.group("label").strip()
    url = match.group("url").strip()
    if not label or label == url:
        return url
    return f"{label} ({url})"


def _normalize_jira_markup_string(text: str) -> str:
    normalized = _JIRA_SMART_LINK_RE.sub(_replace_jira_smart_link, text)
    normalized = _JIRA_ATTACHMENT_REF_RE.sub(r"Attachment: \1", normalized)
    normalized = _JIRA_MEDIA_RE.sub(
        lambda match: f"Attachment: {match.group('name').strip()}",
        normalized,
    )
    return _normalize_text_lines(normalized)


def _adf_node_to_text(node: Any, list_depth: int = 0) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_node_to_text(item, list_depth) for item in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type", "")
    content = node.get("content", [])
    attrs = node.get("attrs") or {}

    if node_type == "doc":
        return "".join(_adf_node_to_text(child, list_depth) for child in content)
    if node_type == "text":
        return node.get("text", "")
    if node_type == "hardBreak":
        return "\n"
    if node_type in {"inlineCard", "blockCard", "embedCard"}:
        url = attrs.get("url", "")
        if node_type == "inlineCard":
            return url
        return f"{url}\n\n" if url else ""
    if node_type == "mention":
        return attrs.get("text") or attrs.get("id") or ""
    if node_type == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""
    if node_type == "rule":
        return "---\n\n"
    if node_type in {"mediaSingle", "mediaGroup"}:
        return "".join(_adf_node_to_text(child, list_depth) for child in content)
    if node_type == "media":
        return attrs.get("alt") or attrs.get("text") or ""
    if node_type in _BLOCK_NODE_TYPES:
        text = "".join(_adf_node_to_text(child, list_depth) for child in content).strip()
        return f"{text}\n\n" if text else ""
    if node_type == "listItem":
        parts = [_adf_node_to_text(child, list_depth) for child in content]
        return _normalize_list_item_text("".join(parts))
    if node_type == "bulletList":
        items: list[str] = []
        indent = "  " * list_depth
        for item in content:
            item_text = _normalize_list_item_text(_adf_node_to_text(item, list_depth + 1))
            if item_text:
                items.append(f"{indent}- {item_text}")
        return "\n".join(items) + ("\n\n" if items else "")
    if node_type == "orderedList":
        items: list[str] = []
        indent = "  " * list_depth
        start = attrs.get("order", 1)
        for index, item in enumerate(content, start=start):
            item_text = _normalize_list_item_text(_adf_node_to_text(item, list_depth + 1))
            if item_text:
                items.append(f"{indent}{index}. {item_text}")
        return "\n".join(items) + ("\n\n" if items else "")
    if node_type == "table":
        rows = [_normalize_text_lines(_adf_node_to_text(child, list_depth)) for child in content]
        rows = [row for row in rows if row]
        return "\n".join(rows) + ("\n\n" if rows else "")
    if node_type == "tableRow":
        cells = [_normalize_text_lines(_adf_node_to_text(child, list_depth)) for child in content]
        cells = [cell for cell in cells if cell]
        return " | ".join(cells)
    if node_type in {"tableCell", "tableHeader"}:
        return _normalize_text_lines("".join(_adf_node_to_text(child, list_depth) for child in content))

    return "".join(_adf_node_to_text(child, list_depth) for child in content)


def normalize_jira_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _normalize_jira_markup_string(value)
    if isinstance(value, dict):
        return _normalize_text_lines(_adf_node_to_text(value))
    return str(value)


class JiraComment(BaseModel):
    id: str = ""
    author: str = ""
    author_account_id: str = ""
    created: str = ""
    body: str = ""


class JiraIssue(BaseModel):
    key: str
    summary: str
    description: str = ""
    project_key: str
    reporter: str | None = None
    # Atlassian account ids, used to decide who may answer this issue's gates from Jira.
    reporter_account_id: str = ""
    assignee_account_id: str = ""
    issue_type: str = ""
    status: str = ""
    labels: list[str] = []
    comments: list[JiraComment] = []
    url: str = ""

    def as_context(self) -> str:
        """Plain-text rendering handed to the LLM stages."""
        lines = [
            f"Jira key: {self.key}",
            f"Type: {self.issue_type or 'unknown'}   Status: {self.status or 'unknown'}",
            f"Summary: {self.summary}",
            f"Reporter: {self.reporter or ''}",
            f"Labels: {', '.join(self.labels) if self.labels else '-'}",
            "",
            "Description:",
            self.description or "(empty)",
        ]
        if self.comments:
            lines += ["", f"Comments ({len(self.comments)}):"]
            for c in self.comments:
                lines.append(f"--- {c.author or 'unknown'} @ {c.created}")
                lines.append(c.body)
        return "\n".join(lines)


def issue_from_api(payload: dict, base_url: str = "") -> JiraIssue:
    """Build a JiraIssue from the REST v3 GET /issue response (or a webhook `issue`)."""
    fields = payload.get("fields", {}) or {}
    project = fields.get("project", {}) or {}
    reporter = fields.get("reporter", {}) or {}
    issue_type = fields.get("issuetype", {}) or {}
    status = fields.get("status", {}) or {}
    comment_block = fields.get("comment", {}) or {}
    comments = []
    for c in comment_block.get("comments", []) or []:
        author = c.get("author", {}) or {}
        comments.append(
            JiraComment(
                id=str(c.get("id", "")),
                author=author.get("displayName", ""),
                author_account_id=author.get("accountId", ""),
                created=c.get("created", ""),
                body=normalize_jira_text(c.get("body")),
            )
        )
    key = payload["key"]
    return JiraIssue(
        key=key,
        summary=normalize_jira_text(fields.get("summary") or ""),
        description=normalize_jira_text(fields.get("description") or ""),
        project_key=project.get("key", ""),
        reporter=reporter.get("displayName"),
        reporter_account_id=reporter.get("accountId") or "",
        assignee_account_id=(fields.get("assignee") or {}).get("accountId") or "",
        issue_type=issue_type.get("name", ""),
        status=status.get("name", ""),
        labels=list(fields.get("labels") or []),
        comments=comments,
        url=f"{base_url.rstrip('/')}/browse/{key}" if base_url else "",
    )


class JiraWebhookPayload(BaseModel):
    webhook_event: str = Field(alias="webhookEvent")
    issue: dict

    def to_issue(self) -> JiraIssue:
        return issue_from_api(self.issue)


# --- Repositories ------------------------------------------------------------


class RepoConfig(BaseModel):
    """One repository the agents may work in, as listed in repos.json.

    ``name`` is the identifier everything else keys on: the prefix on plan-step
    paths, the key in the per-repo diff and PR maps, and the value the UI
    checkboxes and the ``/repos`` command send back.
    """

    name: str = Field(description="Short identifier, used as the path prefix in plan steps.")
    path: str = Field(default="", description="Local clone the agents read and edit.")
    clone_url: str = Field(
        default="",
        description=(
            "Git URL to clone from when `path` does not exist yet (Docker). Empty means "
            "derive it from the Bitbucket host, workspace and repo slug."
        ),
    )
    bitbucket_repo_slug: str = Field(default="", description="Falls back to BITBUCKET_REPO_SLUG.")
    target_branch: str = Field(default="", description="PR destination; falls back to BITBUCKET_TARGET_BRANCH.")
    default_selected: bool = Field(default=False, description="Ticked by default in the UI picker.")
    properties: dict[str, str] = Field(
        default_factory=dict,
        description="Free-form key/value facts about the repo, passed to the planning and coding agents.",
    )


# --- Requirements stage ------------------------------------------------------


class RequirementsSpec(BaseModel):
    """The framed requirement the human approves before any planning happens."""

    title: str
    problem: str = Field(description="What is wrong or missing today, in the user's terms.")
    goals: list[str] = Field(default_factory=list, description="Outcomes the change must achieve.")
    acceptance_criteria: list[str] = Field(default_factory=list, description="Testable statements of done.")
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list, description="Things only the reporter can answer.")
    sources: list[str] = Field(
        default_factory=list,
        description="Where each requirement came from: description, a comment author, or inference.",
    )


class ScopeFinding(BaseModel):
    item: str
    kind: Literal["added", "removed", "changed"]
    verdict: Literal["in_scope", "out_of_scope", "unclear"]
    reason: str


class ScopeCheck(BaseModel):
    """Result of comparing the user's edited requirements against the original issue."""

    findings: list[ScopeFinding] = []

    @property
    def out_of_scope_items(self) -> list[str]:
        return [f.item for f in self.findings if f.verdict == "out_of_scope"]


# --- Plan stage --------------------------------------------------------------


class PlanPhase(BaseModel):
    name: str
    description: str = ""
    step_indexes: list[int] = Field(default_factory=list, description="0-based indexes into plan_steps.")


class PlanStep(BaseModel):
    """One concrete edit in an implementation plan, grounded in the repository."""

    file: str
    action: Literal["modify", "create", "delete"] = "modify"
    change: str
    evidence: str = ""
    # Which repository `file` lives in. Empty on single-repo runs, where there is
    # nothing to disambiguate and the path is relative to the only clone.
    repo: str = ""


class AgentResult(BaseModel):
    branch_name: str
    commit_message: str
    pr_title: str
    pr_description: str
    files_changed: list[str] = []
    # Structured plan fields. Filled by the planning agent (plan mode); the coding
    # agent receives them as its work order and may echo them back.
    analysis: str = ""
    plan_steps: list[PlanStep] = []
    verification: list[str] = []
    open_questions: list[str] = []
    # Planner's direct reply to the reviewer's refinement notes (empty on a first plan).
    notes_response: str = ""
    # Optional grouping of plan_steps into phases the user may execute one at a time.
    phases: list[PlanPhase] = []


class PullRequestResult(BaseModel):
    pr_id: int
    pr_url: str
