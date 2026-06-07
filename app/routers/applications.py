import json
from datetime import datetime
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Application, ApplicationLifecycle, JobMatch, JobPosting
from app.services import filler

router = APIRouter()
templates = Jinja2Templates(directory="app/ui/templates")


@router.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(request: Request, db: Session = Depends(get_db)):
    apps = _get_applications(db)
    return templates.TemplateResponse("pipeline.html", {"request": request, "applications": apps})


@router.get("/api/applications")
async def list_applications(db: Session = Depends(get_db)):
    return {"applications": _get_applications(db)}


@router.get("/api/applications/{app_id}/screenshot")
async def get_screenshot(app_id: int, db: Session = Depends(get_db)):
    app = db.get(Application, app_id)
    if not app or not app.fill_screenshot:
        return {"error": "No screenshot"}
    return FileResponse(app.fill_screenshot)


@router.post("/api/applications/{app_id}/approve")
async def approve(app_id: int, db: Session = Depends(get_db)):
    app = db.get(Application, app_id)
    if not app:
        return {"ok": False, "error": "Not found"}
    result = await filler.fill_linkedin(db, app_id)
    return result


@router.post("/api/applications/{app_id}/submit")
async def submit(app_id: int, db: Session = Depends(get_db)):
    result = await filler.submit_linkedin(db, app_id)
    return result


@router.post("/api/applications/{app_id}/reject")
async def reject(app_id: int, db: Session = Depends(get_db)):
    app = db.get(Application, app_id)
    if not app:
        return {"ok": False}
    app.status = "rejected_self"
    db.commit()
    return {"ok": True}


@router.post("/api/applications/{app_id}/lifecycle")
async def update_lifecycle(app_id: int, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    stage = body.get("stage")
    notes = body.get("notes")
    entry = ApplicationLifecycle(application_id=app_id, stage=stage, notes=notes)
    db.add(entry)
    db.commit()
    return {"ok": True}


def _get_applications(db: Session) -> list[dict]:
    apps = db.query(Application).order_by(Application.created_at.desc()).limit(100).all()
    result = []
    for app in apps:
        match = db.get(JobMatch, app.match_id)
        job = db.get(JobPosting, match.posting_id) if match else None
        latest_stage = (
            db.query(ApplicationLifecycle)
            .filter(ApplicationLifecycle.application_id == app.id)
            .order_by(ApplicationLifecycle.created_at.desc())
            .first()
        )
        result.append({
            "id": app.id,
            "match_id": app.match_id,
            "status": app.status,
            "stage": latest_stage.stage if latest_stage else "—",
            "title": job.title if job else "?",
            "company": job.company if job else "?",
            "location": job.location if job else "?",
            "url": job.url if job else "",
            "score": match.score if match else None,
            "cover_letter": app.cover_letter or "",
            "has_screenshot": bool(app.fill_screenshot),
            "submitted_at": str(app.submitted_at or ""),
            "created_at": str(app.created_at),
        })
    return result
