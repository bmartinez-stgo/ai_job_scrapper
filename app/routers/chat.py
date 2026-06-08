import json
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.ui import templates

from app.database import get_db
from app.models import Conversation, Message, Resume, RoleProfile, ScrapeRun, JobMatch, Application
from app.llm import client as llm
from app.llm import prompts
from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

TOOLS = [
    {"type": "function", "function": {
        "name": "get_jobs",
        "description": "Get scraped job postings with optional filters. Returns a markdown table.",
        "parameters": {"type": "object", "properties": {
            "min_score": {"type": "integer", "description": "Minimum match score"},
            "platform": {"type": "string"},
            "market": {"type": "string", "description": "us_ca or mx"},
            "is_remote": {"type": "boolean"},
            "company": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "days_ago": {"type": "integer"},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_pipeline",
        "description": "Get all applications and their current lifecycle stage.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_job_detail",
        "description": "Get full details of a job posting including description and match analysis.",
        "parameters": {"type": "object", "properties": {
            "job_id": {"type": "integer"},
        }, "required": ["job_id"]},
    }},
    {"type": "function", "function": {
        "name": "draft_application",
        "description": "Generate a cover letter and application draft for a job match.",
        "parameters": {"type": "object", "properties": {
            "match_id": {"type": "integer"},
        }, "required": ["match_id"]},
    }},
    {"type": "function", "function": {
        "name": "approve_application",
        "description": "Approve an application for LinkedIn Easy Apply auto-fill.",
        "parameters": {"type": "object", "properties": {
            "application_id": {"type": "integer"},
        }, "required": ["application_id"]},
    }},
    {"type": "function", "function": {
        "name": "reject_application",
        "description": "Reject or dismiss an application.",
        "parameters": {"type": "object", "properties": {
            "application_id": {"type": "integer"},
            "reason": {"type": "string"},
        }, "required": ["application_id"]},
    }},
    {"type": "function", "function": {
        "name": "trigger_scrape",
        "description": "Trigger a manual job scraping run.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_stats",
        "description": "Get overall statistics: total jobs, matches, pending, submitted.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "blacklist_company",
        "description": "Add a company to the blacklist so its postings are filtered out.",
        "parameters": {"type": "object", "properties": {
            "company_name": {"type": "string"},
            "reason": {"type": "string"},
        }, "required": ["company_name"]},
    }},
    {"type": "function", "function": {
        "name": "update_lifecycle",
        "description": "Update the lifecycle stage of an application.",
        "parameters": {"type": "object", "properties": {
            "application_id": {"type": "integer"},
            "stage": {"type": "string", "enum": [
                "applied", "phone_screen", "technical", "final_round",
                "offer", "negotiating", "accepted", "declined", "rejected", "ghosted",
            ]},
            "notes": {"type": "string"},
        }, "required": ["application_id", "stage"]},
    }},
]


@router.get("/", response_class=HTMLResponse)
async def root(request: Request, db: Session = Depends(get_db)):
    from app.models import AppSetting
    setup_done = db.query(AppSetting).filter(AppSetting.key == "setup_complete").first()
    if not setup_done or setup_done.value != "true":
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/job_scrapper/setup")
    return templates.TemplateResponse("chat.html", {"request": request})


@router.websocket("/ws/chat")
async def chat_ws(ws: WebSocket, db: Session = Depends(get_db)):
    await ws.accept()
    conversation_id = None
    try:
        while True:
            data = await ws.receive_json()

            # Client restoring a previous session
            if data.get("type") == "restore":
                cid = data.get("conversation_id")
                conv = db.get(Conversation, cid) if cid else None
                if conv:
                    conversation_id = cid
                    history = _load_history(db, conversation_id)
                    await ws.send_json({
                        "type": "history",
                        "conversation_id": conversation_id,
                        "messages": history,
                    })
                else:
                    await ws.send_json({"type": "no_history"})
                continue

            user_message = data.get("message", "").strip()
            conversation_id = data.get("conversation_id") or conversation_id

            if not user_message:
                continue

            if not conversation_id:
                conv = Conversation()
                db.add(conv)
                db.commit()
                db.refresh(conv)
                conversation_id = conv.id
                await ws.send_json({"type": "conversation_id", "id": conversation_id})

            _save_message(db, conversation_id, "user", user_message)

            sys_prompt = _build_system_prompt(db)
            history = _load_history(db, conversation_id)
            messages = [
                {"role": "system", "content": sys_prompt},
                *history,
                {"role": "user", "content": user_message},
            ]

            full_response = ""
            async for event in _run_agent(ws, db, messages):
                if event["type"] == "token":
                    full_response += event["content"]

            if full_response:
                _save_message(db, conversation_id, "assistant", full_response)

            _update_conversation(db, conversation_id)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("Chat WS error: %s", e)
        try:
            await ws.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass


async def _run_agent(ws: WebSocket, db: Session, messages: list[dict]):
    while True:
        tool_calls_buf: dict[int, dict] = {}
        content_buf = ""
        finish_event = None

        async for event in llm.stream_chat(messages, tools=TOOLS):
            if event["type"] == "token":
                content_buf += event["content"]
                await ws.send_json({"type": "token", "content": event["content"]})
                yield event
            elif event["type"] == "finish":
                finish_event = event
                break

        if finish_event is None:
            break

        if finish_event["reason"] == "stop":
            await ws.send_json({"type": "done"})
            break

        if finish_event["reason"] == "tool_calls":
            tool_calls = finish_event.get("tool_calls", [])
            messages.append({
                "role": "assistant",
                "content": content_buf or None,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                await ws.send_json({"type": "tool_start", "name": name})
                result = await _execute_tool(db, name, args)
                await ws.send_json({"type": "tool_end", "name": name, "result": result})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": name,
                    "content": json.dumps(result),
                })

            # Continue loop with tool results
        else:
            await ws.send_json({"type": "done"})
            break


async def _execute_tool(db: Session, name: str, args: dict) -> dict:
    from app.models import (
        JobPosting, JobMatch, Application, ApplicationLifecycle,
        CompanyBlacklist, ScrapeRun,
    )
    from app.services import generator, filler

    if name == "get_jobs":
        return _tool_get_jobs(db, **args)
    if name == "get_pipeline":
        return _tool_get_pipeline(db)
    if name == "get_job_detail":
        return _tool_get_job_detail(db, **args)
    if name == "get_stats":
        return _tool_get_stats(db)
    if name == "blacklist_company":
        return _tool_blacklist(db, **args)
    if name == "update_lifecycle":
        return _tool_update_lifecycle(db, **args)
    if name == "reject_application":
        return _tool_reject(db, **args)

    if name == "draft_application":
        app = await generator.draft_application(db, args["match_id"])
        return {"ok": True, "application_id": app.id, "status": app.status}

    if name == "approve_application":
        app_id = args["application_id"]
        app = db.get(Application, app_id)
        if not app:
            return {"ok": False, "error": "Application not found"}
        result = await filler.fill_linkedin(db, app_id)
        return result

    if name == "trigger_scrape":
        from app.scheduler import trigger_scrape_now
        trigger_scrape_now()
        return {"ok": True, "message": "Scrape triggered in background"}

    return {"error": f"Unknown tool: {name}"}


def _tool_get_jobs(db: Session, min_score: int = 0, platform: str = None,
                   market: str = None, is_remote: bool = None,
                   company: str = None, limit: int = 20, days_ago: int = None,
                   **kwargs) -> dict:
    from app.models import JobPosting, JobMatch
    q = (
        db.query(JobPosting, JobMatch)
        .join(JobMatch, JobMatch.posting_id == JobPosting.id, isouter=True)
    )
    if min_score:
        q = q.filter(JobMatch.score >= min_score)
    if platform:
        q = q.filter(JobPosting.platform == platform)
    if market:
        q = q.filter(JobPosting.market == market)
    if is_remote is not None:
        q = q.filter(JobPosting.is_remote == is_remote)
    if company:
        q = q.filter(JobPosting.company.ilike(f"%{company}%"))
    if days_ago:
        cutoff = datetime.utcnow() - timedelta(days=days_ago)
        q = q.filter(JobPosting.scraped_at >= cutoff)

    q = q.filter(JobPosting.visa_status != "no_sponsorship")
    results = q.order_by(JobMatch.score.desc().nullslast()).limit(limit).all()

    jobs = []
    for job, match in results:
        salary = ""
        if job.salary_min and job.salary_max:
            salary = f"{int(job.salary_min/1000)}k-{int(job.salary_max/1000)}k {job.salary_currency or 'USD'}"
        elif job.salary_min:
            salary = f"{int(job.salary_min/1000)}k+ {job.salary_currency or 'USD'}"

        jobs.append({
            "id": job.id,
            "match_id": match.id if match else None,
            "score": match.score if match else None,
            "title": job.title,
            "company": job.company,
            "location": job.location + (" (Remote)" if job.is_remote else ""),
            "salary": salary or "—",
            "platform": job.platform,
            "visa": job.visa_status,
            "url": job.url,
            "posted": str(job.date_posted or ""),
        })

    header = "| Score | Title | Company | Location | Salary | Visa |"
    sep = "|-------|-------|---------|----------|--------|------|"
    rows = [header, sep]
    for j in jobs:
        score = j["score"] if j["score"] is not None else "—"
        title_link = f"[{j['title']}](#job-{j['id']})"
        rows.append(f"| {score} | {title_link} | {j['company']} | {j['location']} | {j['salary']} | {j['visa']} |")

    return {"jobs": jobs, "total": len(jobs), "table_md": "\n".join(rows)}


def _tool_get_pipeline(db: Session) -> dict:
    from app.models import Application, ApplicationLifecycle, JobMatch, JobPosting
    apps = db.query(Application).filter(
        Application.status.in_(["submitted", "pending_approval", "approved", "awaiting_submit"])
    ).all()

    rows = []
    for app in apps:
        match = db.get(JobMatch, app.match_id)
        job = db.get(JobPosting, match.posting_id) if match else None
        latest = (
            db.query(ApplicationLifecycle)
            .filter(ApplicationLifecycle.application_id == app.id)
            .order_by(ApplicationLifecycle.created_at.desc())
            .first()
        )
        rows.append({
            "application_id": app.id,
            "title": job.title if job else "?",
            "company": job.company if job else "?",
            "status": app.status,
            "stage": latest.stage if latest else "—",
            "submitted": str(app.submitted_at or ""),
        })

    return {"pipeline": rows}


def _tool_get_job_detail(db: Session, job_id: int) -> dict:
    from app.models import JobPosting, JobMatch
    job = db.get(JobPosting, job_id)
    if not job:
        return {"error": "Job not found"}
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
        "description": (job.description or "")[:3000],
        "match": {
            "id": match.id,
            "score": match.score,
            "reasoning": match.reasoning,
            "highlights": json.loads(match.highlights_json or "[]"),
            "red_flags": json.loads(match.red_flags_json or "[]"),
        } if match else None,
    }


def _tool_get_stats(db: Session) -> dict:
    from app.models import JobPosting, JobMatch, Application, ScrapeRun
    total_jobs = db.query(JobPosting).count()
    high_matches = db.query(JobMatch).filter(JobMatch.score >= 70).count()
    pending = db.query(Application).filter(Application.status == "pending_approval").count()
    week_ago = datetime.utcnow() - timedelta(days=7)
    submitted_week = db.query(Application).filter(
        Application.status == "submitted",
        Application.submitted_at >= week_ago
    ).count()
    last_run = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "completed")
        .order_by(ScrapeRun.completed_at.desc())
        .first()
    )
    today = datetime.utcnow().replace(hour=0, minute=0, second=0)
    new_today = db.query(JobPosting).filter(JobPosting.scraped_at >= today).count()
    return {
        "total_jobs": total_jobs,
        "high_matches": high_matches,
        "pending_review": pending,
        "submitted_week": submitted_week,
        "new_today": new_today,
        "last_scrape": str(last_run.completed_at) if last_run else "never",
    }


def _tool_blacklist(db: Session, company_name: str, reason: str = None) -> dict:
    from app.models import CompanyBlacklist
    existing = db.query(CompanyBlacklist).filter(
        CompanyBlacklist.company_name.ilike(company_name)
    ).first()
    if existing:
        return {"ok": True, "message": f"{company_name} already blacklisted"}
    entry = CompanyBlacklist(company_name=company_name, reason=reason)
    db.add(entry)
    db.commit()
    return {"ok": True, "message": f"{company_name} added to blacklist"}


def _tool_update_lifecycle(db: Session, application_id: int, stage: str, notes: str = None) -> dict:
    from app.models import ApplicationLifecycle
    entry = ApplicationLifecycle(application_id=application_id, stage=stage, notes=notes)
    db.add(entry)
    db.commit()
    return {"ok": True, "stage": stage}


def _tool_reject(db: Session, application_id: int, reason: str = None) -> dict:
    from app.models import Application
    app = db.get(Application, application_id)
    if not app:
        return {"ok": False, "error": "Not found"}
    app.status = "rejected_self"
    app.notes = reason
    db.commit()
    return {"ok": True}


def _build_system_prompt(db: Session) -> str:
    resume = db.query(Resume).filter(Resume.is_active == True).first()
    resume_summary = ""
    if resume and resume.structured_json:
        try:
            rj = json.loads(resume.structured_json)
            resume_summary = rj.get("summary", "")
        except Exception:
            pass

    profiles = db.query(RoleProfile).filter(RoleProfile.is_active == True).all()
    profile_names = [p.name for p in profiles]

    stats = _tool_get_stats(db)
    return prompts.system_prompt(resume_summary, stats, profile_names)


def _load_history(db: Session, conversation_id: int) -> list[dict]:
    msgs = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(40)
        .all()
    )
    result = []
    for m in msgs:
        entry = {"role": m.role, "content": m.content}
        if m.tool_calls_json:
            entry["tool_calls"] = json.loads(m.tool_calls_json)
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        if m.tool_name:
            entry["name"] = m.tool_name
        result.append(entry)
    return result


def _save_message(db: Session, conversation_id: int, role: str, content: str,
                  tool_calls: list = None, tool_call_id: str = None, tool_name: str = None):
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls_json=json.dumps(tool_calls) if tool_calls else None,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    db.add(msg)
    db.commit()


def _update_conversation(db: Session, conversation_id: int):
    conv = db.get(Conversation, conversation_id)
    if conv:
        conv.last_active = datetime.utcnow()
        db.commit()
