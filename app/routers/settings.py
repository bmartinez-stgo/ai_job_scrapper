import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AppSetting, PlatformSession, RoleProfile, CompanyBlacklist
from app.services.crypto import encrypt
from app.config import settings as app_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/ui/templates")


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request})


@router.post("/api/setup/complete")
async def complete_setup(request: Request, db: Session = Depends(get_db)):
    body = await request.json()

    gmail_password = body.get("gmail_app_password", "")
    if gmail_password:
        from app.services.crypto import encrypt
        _upsert_setting(db, "gmail_app_password_enc", encrypt(gmail_password))

    _upsert_setting(db, "setup_complete", "true")
    return {"ok": True}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    cfg = {r.key: r.value for r in db.query(AppSetting).all()}
    profiles = db.query(RoleProfile).all()
    blacklist = db.query(CompanyBlacklist).order_by(CompanyBlacklist.added_at.desc()).all()
    sessions = {r.platform: str(r.saved_at) for r in db.query(PlatformSession).all()}
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "cfg": cfg,
        "profiles": profiles,
        "blacklist": blacklist,
        "sessions": sessions,
        "defaults": {
            "score_threshold": app_settings.score_threshold,
            "scrape_interval_hours": app_settings.scrape_interval_hours,
        },
    })


@router.post("/api/settings")
async def save_settings(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    for key, value in body.items():
        _upsert_setting(db, key, str(value))
    return {"ok": True}


@router.post("/api/session/{platform}/cookies")
async def save_cookies(platform: str, request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    cookies_raw = json.dumps(body.get("cookies", []))
    encrypted = encrypt(cookies_raw)

    existing = db.query(PlatformSession).filter(PlatformSession.platform == platform).first()
    if existing:
        existing.cookies_encrypted = encrypted
        from datetime import datetime
        existing.saved_at = datetime.utcnow()
    else:
        session = PlatformSession(platform=platform, cookies_encrypted=encrypted)
        db.add(session)
    db.commit()
    return {"ok": True}


@router.delete("/api/blacklist/{company_id}")
async def remove_blacklist(company_id: int, db: Session = Depends(get_db)):
    entry = db.get(CompanyBlacklist, company_id)
    if entry:
        db.delete(entry)
        db.commit()
    return {"ok": True}


@router.post("/api/profiles/{profile_id}/queries")
async def generate_queries(profile_id: int, db: Session = Depends(get_db)):
    from app.models import Resume
    from app.llm import client as llm, prompts

    profile = db.get(RoleProfile, profile_id)
    resume = db.query(Resume).filter(Resume.is_active == True).first()
    if not resume or not profile:
        return {"ok": False, "error": "Missing resume or profile"}

    resume_json = json.loads(resume.structured_json or "{}")
    msgs = prompts.generate_search_queries(resume_json, profile.name, profile.focus, profile.market)
    try:
        raw = await llm.complete(msgs, temperature=0.2)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end]) if start >= 0 else {}
        queries = data.get("queries", [])
    except Exception:
        queries = []

    profile.search_queries_json = json.dumps(queries)
    db.commit()
    return {"ok": True, "queries": queries}


def _upsert_setting(db: Session, key: str, value: str):
    from datetime import datetime
    existing = db.query(AppSetting).filter(AppSetting.key == key).first()
    if existing:
        existing.value = value
        existing.updated_at = datetime.utcnow()
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()
