import json
import os
from fastapi import APIRouter, Depends, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Resume, RoleProfile
from app.llm import client as llm
from app.llm import prompts
from app.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/ui/templates")

DEFAULT_PROFILES = [
    {"name": "Senior Manager SRE", "focus": "sre", "market": "us_ca",
     "tailoring_notes": "Emphasize reliability engineering, SLO/SLA ownership, oncall leadership, platform engineering, incident management, team building at scale."},
    {"name": "Director AI Platform", "focus": "ai", "market": "us_ca",
     "tailoring_notes": "Emphasize ML infrastructure, LLMOps, model serving, GPU clusters, data platform, cross-functional leadership, AI strategy."},
    {"name": "Sr. Director Engineering", "focus": "engineering", "market": "us_ca",
     "tailoring_notes": "Emphasize org design, multi-team leadership, technical strategy, hiring, roadmap, P&L awareness, executive communication."},
    {"name": "Director Ingeniería", "focus": "engineering", "market": "mx",
     "tailoring_notes": "Posición en México. Énfasis en liderazgo técnico, gestión de equipos, arquitectura, y resultados de negocio medibles."},
]


@router.get("/resume", response_class=HTMLResponse)
async def resume_page(request: Request, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.is_active == True).first()
    return templates.TemplateResponse("resume.html", {"request": request, "resume": resume})


@router.post("/api/resume")
async def upload_resume(
    db: Session = Depends(get_db),
    file: UploadFile = File(None),
    raw_text: str = Form(None),
):
    if file and file.filename:
        content = await file.read()
        filename = file.filename
        if filename.endswith(".pdf"):
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        else:
            text = content.decode("utf-8", errors="ignore")
        save_path = os.path.join(settings.resumes_dir, filename)
        with open(save_path, "wb") as f:
            f.write(content)
    elif raw_text:
        text = raw_text
        filename = "resume_pasted.txt"
    else:
        raise HTTPException(400, "Provide a file or text")

    msgs = prompts.extract_resume(text)
    try:
        raw = await llm.complete(msgs, temperature=0.1)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        structured = json.loads(raw[start:end]) if start >= 0 else {}
    except Exception:
        structured = {}

    db.query(Resume).update({"is_active": False})
    resume = Resume(
        filename=filename,
        raw_text=text,
        structured_json=json.dumps(structured),
        is_active=True,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    _ensure_profiles(db, resume.id, structured)

    return {"ok": True, "resume_id": resume.id, "structured": structured}


@router.get("/api/resume/active")
async def get_active_resume(db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.is_active == True).first()
    if not resume:
        return JSONResponse(status_code=404, content={"error": "No active resume"})
    return {
        "id": resume.id,
        "filename": resume.filename,
        "structured": json.loads(resume.structured_json or "{}"),
        "created_at": str(resume.created_at),
    }


def _ensure_profiles(db: Session, resume_id: int, structured: dict):
    existing = db.query(RoleProfile).count()
    if existing > 0:
        return
    for p in DEFAULT_PROFILES:
        profile = RoleProfile(
            name=p["name"],
            focus=p["focus"],
            market=p["market"],
            tailoring_notes=p["tailoring_notes"],
            search_queries_json=json.dumps([]),
        )
        db.add(profile)
    db.commit()
