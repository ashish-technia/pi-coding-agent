import re
from typing import Any

from pydantic import BaseModel, Field


_BLOCK_NODE_TYPES = {
    "paragraph",
    "heading",
    "blockquote",
    "codeBlock",
    "panel",
}

_JIRA_SMART_LINK_RE = re.compile(
    r"\[(?P<label>[^\[\]\|]+)\|(?P<url>https?://[^\[\]\|]+)(?:\|smart-link)?\]"
)
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


class JiraIssue(BaseModel):
    key: str
    summary: str
    description: str = ""
    project_key: str
    reporter: str | None = None


class JiraWebhookPayload(BaseModel):
    webhook_event: str = Field(alias="webhookEvent")
    issue: dict

    def to_issue(self) -> JiraIssue:
        fields = self.issue.get("fields", {})
        project = fields.get("project", {}) or {}
        reporter = fields.get("reporter", {}) or {}
        return JiraIssue(
            key=self.issue["key"],
            summary=normalize_jira_text(fields.get("summary") or ""),
            description=normalize_jira_text(fields.get("description") or ""),
            project_key=project.get("key", ""),
            reporter=reporter.get("displayName"),
        )


class AgentResult(BaseModel):
    branch_name: str
    commit_message: str
    pr_title: str
    pr_description: str
    files_changed: list[str] = []


class PullRequestResult(BaseModel):
    pr_id: int
    pr_url: str
