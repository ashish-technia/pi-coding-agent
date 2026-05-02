import logging

from fastapi import FastAPI, Header, HTTPException, status

from .config import settings
from .models import JiraWebhookPayload
from .queue_worker import AsyncEventQueue
from .service import AutomationService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)
automation = AutomationService()
event_queue = AsyncEventQueue(
    max_size=settings.queue_max_size,
    handler=lambda event: automation.process_issue(JiraWebhookPayload(**event).to_issue()),
)


def _is_allowed_project(project_key: str) -> bool:
    allowed = settings.allowed_projects_set()
    if not allowed:
        return True
    return project_key.upper() in allowed


@app.on_event("startup")
async def startup_event() -> None:
    if settings.use_queue:
        await event_queue.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if settings.use_queue:
        await event_queue.stop()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/jira")
async def jira_webhook(
    payload: JiraWebhookPayload,
    x_webhook_secret: str = Header(default=""),
) -> dict[str, str]:
    if x_webhook_secret != settings.webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )

    issue = payload.to_issue()
    if not _is_allowed_project(issue.project_key):
        logger.info("Ignored issue=%s from project=%s", issue.key, issue.project_key)
        return {"status": "ignored", "reason": "project_not_allowed"}

    if settings.use_queue:
        await event_queue.enqueue(payload.model_dump(by_alias=True))
        return {"status": "queued", "issue": issue.key}

    await automation.process_issue(issue)
    return {"status": "processed", "issue": issue.key}
