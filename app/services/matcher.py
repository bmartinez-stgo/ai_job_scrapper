import json
import logging
from sqlalchemy.orm import Session
from app.models import JobPosting, JobMatch, Resume, RoleProfile, ScrapeRun
from app.llm import client as llm
from app.llm import prompts
from app.config import settings

logger = logging.getLogger(__name__)


async def match_new_jobs(db: Session, run_id: int) -> int:
    run = db.get(ScrapeRun, run_id)
    resume = db.query(Resume).filter(Resume.is_active == True).first()
    if not resume:
        logger.warning("No active resume for matching")
        return 0

    resume_json = json.loads(resume.structured_json or "{}")
    profiles = db.query(RoleProfile).filter(RoleProfile.is_active == True).all()
    profile_map = {p.id: p for p in profiles}

    jobs = (
        db.query(JobPosting)
        .filter(JobPosting.run_id == run_id)
        .filter(~JobPosting.id.in_(
            db.query(JobMatch.posting_id)
        ))
        .all()
    )

    matched = 0
    for job in jobs:
        best_profile = _pick_profile(job, profiles)
        profile_name = best_profile.name if best_profile else "Senior Manager / Director"

        try:
            msgs = prompts.score_job(resume_json, _job_dict(job), profile_name)
            raw = await llm.complete(msgs, temperature=0.1)
            data = _parse_json(raw)
        except Exception as e:
            logger.warning("Scoring failed for job %d: %s", job.id, e)
            continue

        score = int(data.get("score", 0))
        recommend = data.get("recommend", False)

        if score < settings.score_threshold and not recommend:
            continue

        match = JobMatch(
            posting_id=job.id,
            resume_id=resume.id,
            profile_id=best_profile.id if best_profile else None,
            score=score,
            reasoning=data.get("reasoning", ""),
            highlights_json=json.dumps(data.get("highlights", [])),
            red_flags_json=json.dumps(data.get("red_flags", [])),
            recommend=recommend,
        )
        db.add(match)
        matched += 1

    run.jobs_matched = matched
    db.commit()
    return matched


def _pick_profile(job: JobPosting, profiles: list[RoleProfile]) -> RoleProfile | None:
    title = (job.title or "").lower()
    for p in profiles:
        focus = (p.focus or "").lower()
        if focus == "sre" and any(k in title for k in ("sre", "reliability", "platform", "infra")):
            return p
        if focus == "ai" and any(k in title for k in ("ai", "ml", "machine learning", "data")):
            return p
    return profiles[0] if profiles else None


def _job_dict(job: JobPosting) -> dict:
    return {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "is_remote": job.is_remote,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "description": job.description,
    }


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(raw[start:end])
    return {}
