import json
from typing import Optional
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.ui import templates

from app.database import get_db
from app.models import ScrapeRun

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    return templates.TemplateResponse("jobs.html", {"request": request})


@router.get("/api/jobs")
async def list_jobs(
    min_score: int = Query(0),
    market: Optional[str] = Query(None),
    is_remote: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(200),
    db: Session = Depends(get_db),
):
    from app.models import JobPosting, JobMatch, Application
    q = (
        db.query(JobPosting, JobMatch, Application)
        .join(JobMatch, JobMatch.posting_id == JobPosting.id, isouter=True)
        .join(Application, Application.match_id == JobMatch.id, isouter=True)
        .filter(JobPosting.visa_status != "no_sponsorship")
    )
    if min_score:
        q = q.filter(JobMatch.score >= min_score)
    if market:
        q = q.filter(JobPosting.market == market)
    if is_remote is not None:
        q = q.filter(JobPosting.is_remote == is_remote)
    if search:
        term = f"%{search}%"
        q = q.filter(
            (JobPosting.title.ilike(term)) | (JobPosting.company.ilike(term))
        )
    results = q.order_by(JobMatch.score.desc().nullslast()).limit(limit).all()

    jobs = []
    for job, match, app in results:
        salary = ""
        if job.salary_min and job.salary_max:
            salary = f"{int(job.salary_min/1000)}k–{int(job.salary_max/1000)}k {job.salary_currency or 'USD'}"
        elif job.salary_min:
            salary = f"{int(job.salary_min/1000)}k+ {job.salary_currency or 'USD'}"
        jobs.append({
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "is_remote": job.is_remote,
            "salary": salary,
            "visa": job.visa_status,
            "market": job.market,
            "platform": job.platform,
            "date_posted": str(job.date_posted or ""),
            "url": job.url,
            "score": match.score if match else None,
            "match_id": match.id if match else None,
            "application": {"id": app.id, "status": app.status} if app else None,
        })
    return {"jobs": jobs, "total": len(jobs)}


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
    salary = ""
    if job.salary_min and job.salary_max:
        salary = f"{int(job.salary_min/1000)}k–{int(job.salary_max/1000)}k {job.salary_currency or 'USD'}"
    elif job.salary_min:
        salary = f"{int(job.salary_min/1000)}k+ {job.salary_currency or 'USD'}"
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "is_remote": job.is_remote,
        "salary": salary,
        "platform": job.platform,
        "url": job.url,
        "visa_status": job.visa_status,
        "description": job.description or "",
        "date_posted": str(job.date_posted or ""),
        "match": {
            "score": match.score,
            "reasoning": match.reasoning,
            "highlights": json.loads(match.highlights_json or "[]"),
            "red_flags": json.loads(match.red_flags_json or "[]"),
        } if match else None,
    }


@router.post("/api/jobs/{job_id}/draft")
async def draft_job_application(job_id: int, db: Session = Depends(get_db)):
    from app.models import JobMatch, Application
    from app.services import generator
    match = db.query(JobMatch).filter(JobMatch.posting_id == job_id).first()
    if not match:
        return {"error": "Job has not been scored yet — run the matcher first"}
    existing = db.query(Application).filter(Application.match_id == match.id).first()
    if existing:
        return {"error": f"Application already exists (status: {existing.status})"}
    result = await generator.draft_application(db, match.id)
    return result


@router.post("/api/jobs/{job_id}/blacklist")
async def blacklist_job_company(job_id: int, db: Session = Depends(get_db)):
    from app.models import JobPosting, CompanyBlacklist
    job = db.get(JobPosting, job_id)
    if not job:
        return {"error": "Not found"}
    existing = db.query(CompanyBlacklist).filter(
        CompanyBlacklist.company_name == job.company
    ).first()
    if existing:
        return {"ok": True, "message": f"{job.company} is already blacklisted"}
    bl = CompanyBlacklist(company_name=job.company, reason="Blacklisted from jobs dashboard")
    db.add(bl)
    db.commit()
    return {"ok": True, "message": f"{job.company} added to blacklist"}


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
