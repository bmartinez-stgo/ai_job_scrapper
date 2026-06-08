from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.ui import templates

from app.database import get_db
from app.models import ScrapeRun

router = APIRouter()


@router.get("/runs", response_class=HTMLResponse)
async def runs_page(request: Request, db: Session = Depends(get_db)):
    runs = _get_runs(db)
    return templates.TemplateResponse("runs.html", {"request": request, "runs": runs})


@router.get("/api/runs")
async def list_runs(db: Session = Depends(get_db)):
    return {"runs": _get_runs(db)}


@router.get("/api/runs/{run_id}")
async def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if not run:
        return {"error": "Not found"}
    return {
        "id": run.id,
        "status": run.status,
        "jobs_found": run.jobs_found,
        "new_jobs": run.new_jobs,
        "jobs_matched": run.jobs_matched,
        "error": run.error_message,
        "progress_log": run.progress_log,
        "started_at": str(run.started_at),
        "completed_at": str(run.completed_at or ""),
    }


@router.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: int, db: Session = Depends(get_db)):
    from app.models import JobPosting, JobMatch
    job = db.get(JobPosting, job_id)
    if not job:
        return {"error": "Not found"}
    match = db.query(JobMatch).filter(JobMatch.posting_id == job_id).first()
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "is_remote": job.is_remote,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "platform": job.platform,
        "url": job.url,
        "visa_status": job.visa_status,
        "description": job.description or "",
        "date_posted": str(job.date_posted or ""),
        "match": {
            "score": match.score,
            "reasoning": match.reasoning,
        } if match else None,
    }


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
