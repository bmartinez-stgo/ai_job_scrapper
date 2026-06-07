import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        jobstore_url = f"sqlite:///{settings.data_dir}/scheduler.db"
        _scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=jobstore_url)},
            job_defaults={"coalesce": True, "max_instances": 1},
        )
    return _scheduler


def start_scheduler():
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        scheduler.add_job(
            _scrape_job,
            trigger="interval",
            hours=settings.scrape_interval_hours,
            id="scrape_and_match",
            replace_existing=True,
        )
        scheduler.add_job(
            _ghosted_check,
            trigger="cron",
            hour=9,
            minute=0,
            id="ghosted_check",
            replace_existing=True,
        )
        logger.info("Scheduler started")


def stop_scheduler():
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)


def trigger_scrape_now():
    get_scheduler().add_job(
        _scrape_job,
        trigger="date",
        run_date=datetime.utcnow(),
        id="manual_scrape",
        replace_existing=True,
    )


def _scrape_job():
    asyncio.run(_scrape_async())


async def _scrape_async():
    from app.models import ScrapeRun
    from app.services.scraper import run_scrape
    from app.services.matcher import match_new_jobs
    from app.services.email_notify import notify_new_matches

    db: Session = SessionLocal()
    run = None
    try:
        run = ScrapeRun(status="running")
        db.add(run)
        db.commit()
        db.refresh(run)

        result = run_scrape(db, run.id)
        matched = await match_new_jobs(db, run.id)

        run.status = "completed"
        run.completed_at = datetime.utcnow()
        run.jobs_matched = matched
        db.commit()

        if result.get("new", 0) > 0:
            from app.models import JobMatch, JobPosting
            new_matches = (
                db.query(JobMatch, JobPosting)
                .join(JobPosting, JobPosting.id == JobMatch.posting_id)
                .filter(JobMatch.score >= settings.score_threshold)
                .filter(JobPosting.run_id == run.id)
                .all()
            )
            await notify_new_matches([
                {"score": m.score, "title": j.title, "company": j.company, "location": j.location}
                for m, j in new_matches
            ])
    except Exception as e:
        logger.error("Scrape job failed: %s", e)
        if run:
            run.status = "failed"
            run.error_message = str(e)
            run.completed_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def _ghosted_check():
    asyncio.run(_ghosted_async())


async def _ghosted_async():
    from app.models import Application, ApplicationLifecycle
    from app.services.email_notify import notify_ghosted

    db: Session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=settings.ghosted_days)
        ghosted_apps = (
            db.query(Application)
            .filter(Application.status == "submitted")
            .filter(Application.submitted_at <= cutoff)
            .filter(
                ~db.query(ApplicationLifecycle)
                .filter(
                    ApplicationLifecycle.application_id == Application.id,
                    ApplicationLifecycle.stage.in_([
                        "phone_screen", "technical", "final_round",
                        "offer", "rejected", "accepted", "declined", "ghosted",
                    ])
                )
                .exists()
            )
            .all()
        )

        from app.models import JobMatch, JobPosting
        notify_list = []
        for app in ghosted_apps:
            entry = ApplicationLifecycle(application_id=app.id, stage="ghosted",
                                         notes=f"Auto-tagged: no response in {settings.ghosted_days} days")
            db.add(entry)
            match = db.get(JobMatch, app.match_id)
            job = db.get(JobPosting, match.posting_id) if match else None
            if job:
                notify_list.append({
                    "title": job.title, "company": job.company,
                    "submitted_at": str(app.submitted_at or "")[:10],
                })
        db.commit()

        if notify_list:
            await notify_ghosted(notify_list)
    finally:
        db.close()
