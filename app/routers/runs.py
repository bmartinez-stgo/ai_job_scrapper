from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ScrapeRun

router = APIRouter()
templates = Jinja2Templates(directory="app/ui/templates")


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(request: Request, db: Session = Depends(get_db)):
    runs = _get_runs(db)
    return templates.TemplateResponse("runs.html", {"request": request, "runs": runs})


@router.get("/api/runs")
async def list_runs(db: Session = Depends(get_db)):
    return {"runs": _get_runs(db)}


@router.post("/api/runs/trigger")
async def trigger_run(db: Session = Depends(get_db)):
    from app.scheduler import trigger_scrape_now
    trigger_scrape_now()
    return {"ok": True, "message": "Scrape started in background"}


def _get_runs(db: Session) -> list[dict]:
    runs = (
        db.query(ScrapeRun)
        .order_by(ScrapeRun.started_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": r.id,
            "started_at": str(r.started_at),
            "completed_at": str(r.completed_at or ""),
            "status": r.status,
            "jobs_found": r.jobs_found,
            "new_jobs": r.new_jobs,
            "jobs_matched": r.jobs_matched,
            "error": r.error_message or "",
        }
        for r in runs
    ]
