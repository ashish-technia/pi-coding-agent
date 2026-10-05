import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .models import JiraIssue, JiraWebhookPayload
from .queue_worker import make_job_queue
from .service import AutomationService, ConflictError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_SPA_DIR = _STATIC_DIR / "dist"

automation = AutomationService()


async def _handle_job(job: dict) -> None:
    kind = job.get("kind")
    if kind == "start":
        inline = JiraIssue(**job["issue"]) if job.get("issue") else None
        await automation.start_run(
            job["issue_key"],
            channel=job.get("channel", "ui"),
            inline_issue=inline,
            repos=job.get("repos"),
        )
    elif kind == "comment":
        await automation.submit_comment(
            job["issue_key"], job.get("body", ""), job.get("author_account_id", ""), job.get("comment_id", "")
        )
    else:
        logger.warning("Unknown job kind: %s", kind)


job_queue = make_job_queue(settings.redis_url, settings.queue_max_size, _handle_job)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await automation.start()
    if settings.use_queue:
        await job_queue.start()
    yield
    if settings.use_queue:
        await job_queue.stop()
    await automation.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
# check_dir=False: a fresh clone has no static/ until the UI is built (its dist/ is git-ignored),
# and the API must still start; "/" reports the missing build instead.
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR), check_dir=False), name="static")


@app.get("/assets/{file_path:path}", include_in_schema=False)
async def spa_assets(file_path: str) -> FileResponse:
    """Built SPA assets (frontend/ -> static/dist). Resolved per request so a build after
    startup is picked up without restarting."""
    target = (_SPA_DIR / "assets" / file_path).resolve()
    if not str(target).startswith(str((_SPA_DIR / "assets").resolve())) or not target.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(target), headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_: Request, exc: RuntimeError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_error_handler(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(LookupError)
async def lookup_error_handler(_: Request, exc: LookupError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


# --------------------------------------------------------------------------- models


class StartRunRequest(BaseModel):
    issue_key: str
    # Repositories this run may work in, by name from repos.json. Empty/omitted uses the
    # repos flagged default_selected.
    repos: list[str] | None = None
    # Optional inline issue for development without Jira access.
    summary: str | None = None
    description: str | None = None
    project_key: str | None = None
    reporter: str | None = None


class DecisionRequest(BaseModel):
    action: str
    # `pending.gate_id` of the gate being answered. Required, so a decision sent twice or
    # from a stale page cannot answer a later gate of the same kind.
    gate_id: str
    notes: str | None = None
    requirements: dict | None = None
    acknowledge_scope: bool | None = None
    mode: str | None = None
    pr_title: str | None = None
    pr_description: str | None = None

    def payload(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class JiraTriggerRequest(BaseModel):
    """Body of the Jira Automation 'Send web request' for assignment/label triggers."""

    issue_key: str
    # Optional repo names from repos.json; omitted uses the default_selected repos.
    repos: list[str] | None = None


class JiraCommentRequest(BaseModel):
    """Body of the Jira Automation 'Send web request' for the 'Issue commented' trigger."""

    issue_key: str
    comment_body: str
    author_account_id: str = ""
    # Jira's id of the comment; lets a redelivered web request be recognised and ignored.
    comment_id: str = ""


def _is_allowed_project(project_key: str) -> bool:
    allowed = settings.allowed_projects_set()
    if not allowed:
        return True
    return project_key.upper() in allowed


def _check_secret(provided: str) -> None:
    # Constant-time: an ordinary comparison answers faster the earlier the first wrong
    # character is, which lets a caller find the secret one character at a time.
    if not hmac.compare_digest(provided.encode(), settings.webhook_secret.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")


# --------------------------------------------------------------------------- UI


@app.get("/", include_in_schema=False)
async def index():
    spa = _SPA_DIR / "index.html"
    if not spa.is_file():
        return JSONResponse(
            status_code=503,
            content={"detail": "UI not built. Run `npm run build` in frontend/ (or use `npm run dev` there)."},
        )
    # The UI polls the API; never let the browser serve a stale copy.
    return FileResponse(str(spa), headers={"Cache-Control": "no-cache"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
async def get_config() -> dict:
    return settings.public_view()


# --------------------------------------------------------------------------- runs


@app.get("/api/runs")
async def list_runs(limit: int = 50) -> list[dict]:
    return await automation.list_runs(limit)


@app.post("/api/runs")
async def start_run(req: StartRunRequest) -> dict:
    key = req.issue_key.strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="issue_key is required")
    inline = None
    if req.summary:
        inline = JiraIssue(
            key=key,
            summary=req.summary,
            description=req.description or "",
            project_key=req.project_key or key.split("-")[0],
            reporter=req.reporter,
        )
    project = inline.project_key if inline else key.split("-")[0]
    if not _is_allowed_project(project):
        raise HTTPException(status_code=403, detail=f"Project {project} is not in ALLOWED_PROJECTS")
    return await automation.start_run(key, channel="ui", inline_issue=inline, repos=req.repos)


@app.get("/api/runs/{issue_key}")
async def get_run(issue_key: str) -> dict:
    return await automation.get_status(issue_key)


@app.post("/api/runs/{issue_key}/decision")
async def submit_decision(issue_key: str, req: DecisionRequest) -> dict:
    return await automation.submit_decision(issue_key, req.payload())


@app.post("/api/runs/{issue_key}/retry")
async def retry_run(issue_key: str) -> dict:
    return await automation.retry(issue_key)


# --------------------------------------------------------------------------- Jira ingress (flow 2)


@app.post("/webhooks/jira/trigger")
async def jira_trigger(req: JiraTriggerRequest, x_webhook_secret: str = Header(default="")) -> dict:
    """Called by a Jira Automation rule when the agent label is added or the issue is assigned."""
    _check_secret(x_webhook_secret)
    key = req.issue_key.strip().upper()
    if not _is_allowed_project(key.split("-")[0]):
        return {"status": "ignored", "reason": "project_not_allowed", "issue": key}
    job = {"kind": "start", "issue_key": key, "channel": "jira", "repos": req.repos}
    if settings.use_queue:
        await job_queue.enqueue(job)
        return {"status": "queued", "issue": key}
    await _handle_job(job)
    return {"status": "started", "issue": key}


@app.post("/webhooks/jira/comment")
async def jira_comment(req: JiraCommentRequest, x_webhook_secret: str = Header(default="")) -> dict:
    """Called by a Jira Automation rule on 'Issue commented'. Non-command comments are ignored."""
    _check_secret(x_webhook_secret)
    key = req.issue_key.strip().upper()
    job = {
        "kind": "comment",
        "issue_key": key,
        "body": req.comment_body,
        "author_account_id": req.author_account_id,
        "comment_id": req.comment_id,
    }
    if settings.use_queue:
        await job_queue.enqueue(job)
        return {"status": "queued", "issue": key}
    return await automation.submit_comment(key, req.comment_body, req.author_account_id, req.comment_id)


@app.post("/webhooks/jira")
async def jira_webhook(payload: JiraWebhookPayload, x_webhook_secret: str = Header(default="")) -> dict:
    """Classic Jira webhook (full issue payload). Kept for existing setups."""
    _check_secret(x_webhook_secret)
    issue = payload.to_issue()
    if not _is_allowed_project(issue.project_key):
        logger.info("Ignored issue=%s from project=%s", issue.key, issue.project_key)
        return {"status": "ignored", "reason": "project_not_allowed"}
    job = {"kind": "start", "issue_key": issue.key, "channel": "jira", "issue": issue.model_dump()}
    if settings.use_queue:
        await job_queue.enqueue(job)
        return {"status": "queued", "issue": issue.key}
    await _handle_job(job)
    return {"status": "started", "issue": issue.key}


# --------------------------------------------------------------------------- legacy routes (old UI)


@app.post("/run", include_in_schema=False)
async def run_manual_legacy(req: StartRunRequest) -> dict:
    return await start_run(req)


@app.get("/runs/{issue_key}/status", include_in_schema=False)
async def get_status_legacy(issue_key: str) -> dict:
    return await automation.get_status(issue_key)


# SPA fallback: any non-API path renders the app so client-side routes work on refresh.
@app.get("/{path:path}", include_in_schema=False)
async def spa_fallback(path: str):
    if path.startswith(("api/", "webhooks/", "static/", "assets/", "docs", "openapi.json")):
        raise HTTPException(status_code=404)
    return await index()
