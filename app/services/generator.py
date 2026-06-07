import json
import logging
from sqlalchemy.orm import Session
from app.models import Application, JobMatch, JobPosting, Resume, RoleProfile
from app.llm import client as llm
from app.llm import prompts

logger = logging.getLogger(__name__)


async def draft_application(db: Session, match_id: int) -> Application:
    match = db.get(JobMatch, match_id)
    job = db.get(JobPosting, match.posting_id)
    resume = db.get(Resume, match.resume_id)
    profile = db.get(RoleProfile, match.profile_id) if match.profile_id else None

    resume_json = json.loads(resume.structured_json or "{}")
    profile_name = profile.name if profile else "Senior Manager / Director"

    cover_letter = await _gen_cover_letter(resume_json, job, match, profile_name)

    existing = db.query(Application).filter(Application.match_id == match_id).first()
    if existing:
        existing.cover_letter = cover_letter
        existing.status = "pending_approval"
        db.commit()
        return existing

    app = Application(
        match_id=match_id,
        status="pending_approval",
        cover_letter=cover_letter,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


async def _gen_cover_letter(resume_json: dict, job: JobPosting, match: JobMatch, profile_name: str) -> str:
    match_dict = {
        "score": match.score,
        "highlights_json": match.highlights_json,
    }
    msgs = prompts.generate_cover_letter(resume_json, _job_dict(job), match_dict, profile_name)
    try:
        return await llm.complete(msgs, temperature=0.5)
    except Exception as e:
        logger.error("Cover letter generation failed: %s", e)
        return ""


def _job_dict(job: JobPosting) -> dict:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": job.description,
    }
