import hashlib
import json
import logging
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.models import JobPosting, ScrapeRun, CompanyBlacklist, RoleProfile

logger = logging.getLogger(__name__)

NO_SPONSORSHIP_RE = re.compile(
    r"no\s+(visa\s+)?(sponsorship|sponsor)|"
    r"must\s+(be\s+)?(authorized|eligible)\s+to\s+work|"
    r"(us|u\.s\.|american|canadian)\s+citizen(s)?\s+only|"
    r"green\s*card|"
    r"no\s+work\s+permit|"
    r"authorized\s+to\s+work\s+in\s+(the\s+)?(us|u\.s\.|united\s+states|canada)|"
    r"not\s+(eligible|able)\s+to\s+sponsor",
    re.IGNORECASE,
)

SPONSORS_RE = re.compile(
    r"(will\s+)?(sponsor|provide)\s+(visa|work\s+permit|h-?1b|sponsorship)|"
    r"visa\s+sponsorship\s+(available|provided|offered)|"
    r"open\s+to\s+sponsoring",
    re.IGNORECASE,
)

_executor = ThreadPoolExecutor(max_workers=2)


def _fingerprint(title: str, company: str, location: str) -> str:
    key = f"{title.lower().strip()}|{company.lower().strip()}|{location.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _visa_status(description: str, market: str) -> str:
    if market == "mx":
        return "ok"
    text = description or ""
    if NO_SPONSORSHIP_RE.search(text):
        return "no_sponsorship"
    if SPONSORS_RE.search(text):
        return "ok"
    return "unknown"


def _scrape_us(search_term: str, location: str, remote_only: bool) -> list[dict]:
    try:
        from jobspy import scrape_jobs
        df = scrape_jobs(
            site_name=["linkedin", "indeed", "glassdoor", "zip_recruiter"],
            search_term=search_term,
            location=location,
            results_wanted=30,
            hours_old=96,
            linkedin_fetch_description=True,
            is_remote=remote_only,
        )
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    except Exception as e:
        logger.warning("US scrape failed for '%s': %s", search_term, e)
        return []


def _scrape_mx(search_term: str) -> list[dict]:
    try:
        from jobspy import scrape_jobs
        df = scrape_jobs(
            site_name=["indeed"],
            search_term=search_term,
            location="México",
            results_wanted=20,
            hours_old=96,
            country_indeed="Mexico",
        )
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    except Exception as e:
        logger.warning("MX scrape failed for '%s': %s", search_term, e)
        return []


def run_scrape(db: Session, run_id: int) -> dict:
    run = db.get(ScrapeRun, run_id)
    blacklisted = {
        r[0].lower()
        for r in db.query(CompanyBlacklist.company_name).all()
    }
    profiles = db.query(RoleProfile).filter(RoleProfile.is_active == True).all()

    raw_jobs: list[dict] = []

    for profile in profiles:
        queries = json.loads(profile.search_queries_json or "[]")
        for q in queries:
            search_term = q.get("search_term", "")
            location = q.get("location", "United States")
            remote_only = q.get("remote_only", False)

            if profile.market in ("us_ca", "both"):
                raw_jobs.extend(_scrape_us(search_term, location, remote_only))
            if profile.market in ("mx", "both"):
                raw_jobs.extend(_scrape_mx(search_term))

    found = 0
    new = 0
    for raw in raw_jobs:
        title = str(raw.get("title") or "")
        company = str(raw.get("company") or "")
        location = str(raw.get("location") or "")
        description = str(raw.get("description") or "")
        url = str(raw.get("job_url") or raw.get("url") or "")
        platform = str(raw.get("site") or "unknown")
        is_remote = bool(raw.get("is_remote") or raw.get("remote") or False)
        market = "mx" if "mexico" in location.lower() or "mx" in platform.lower() else "us_ca"

        if not title or not company:
            continue
        if company.lower() in blacklisted:
            continue
        if market == "mx":
            sal_min = raw.get("min_amount")
            if sal_min and float(sal_min) < 130000:
                continue

        vis = _visa_status(description, market)
        if market == "us_ca" and vis == "no_sponsorship":
            continue

        fp = _fingerprint(title, company, location)
        found += 1

        existing = db.query(JobPosting).filter(JobPosting.fingerprint == fp).first()
        if existing:
            continue

        salary_min = raw.get("min_amount") or raw.get("salary_min")
        salary_max = raw.get("max_amount") or raw.get("salary_max")
        currency = str(raw.get("currency") or raw.get("salary_currency") or "USD")
        date_posted = None
        dp = raw.get("date_posted")
        if dp:
            try:
                date_posted = dp if hasattr(dp, "year") else datetime.fromisoformat(str(dp)).date()
            except Exception:
                pass

        job = JobPosting(
            run_id=run_id,
            fingerprint=fp,
            platform=platform,
            title=title,
            company=company,
            location=location,
            is_remote=is_remote,
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            salary_currency=currency,
            description=description,
            url=url,
            date_posted=date_posted,
            visa_status=vis,
            market=market,
        )
        db.add(job)
        new += 1

    db.commit()

    run.jobs_found = found
    run.new_jobs = new
    db.commit()

    return {"found": found, "new": new}
