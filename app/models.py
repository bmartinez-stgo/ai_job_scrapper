from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime, Date, ForeignKey
)
from app.database import Base


class Resume(Base):
    __tablename__ = "resumes"
    id = Column(Integer, primary_key=True)
    filename = Column(String)
    raw_text = Column(Text)
    structured_json = Column(Text)  # JSON: skills, experience, education
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RoleProfile(Base):
    __tablename__ = "role_profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String)  # e.g. "Senior Manager SRE"
    focus = Column(String)  # sre | ai | engineering
    market = Column(String, default="us_ca")  # us_ca | mx | both
    search_queries_json = Column(Text)  # JSON list of search terms
    tailoring_notes = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default="running")  # running | completed | failed
    jobs_found = Column(Integer, default=0)
    new_jobs = Column(Integer, default=0)
    jobs_matched = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)


class JobPosting(Base):
    __tablename__ = "job_postings"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("scrape_runs.id"))
    fingerprint = Column(String, unique=True, index=True)
    platform = Column(String)
    title = Column(String)
    company = Column(String, index=True)
    location = Column(String)
    is_remote = Column(Boolean, default=False)
    salary_min = Column(Float, nullable=True)
    salary_max = Column(Float, nullable=True)
    salary_currency = Column(String, nullable=True)
    description = Column(Text)
    url = Column(String)
    date_posted = Column(Date, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow)
    visa_status = Column(String, default="unknown")  # ok | no_sponsorship | unknown
    market = Column(String, default="us_ca")


class JobMatch(Base):
    __tablename__ = "job_matches"
    id = Column(Integer, primary_key=True)
    posting_id = Column(Integer, ForeignKey("job_postings.id"))
    resume_id = Column(Integer, ForeignKey("resumes.id"))
    profile_id = Column(Integer, ForeignKey("role_profiles.id"), nullable=True)
    score = Column(Integer)
    reasoning = Column(Text)
    highlights_json = Column(Text)  # JSON list
    red_flags_json = Column(Text)   # JSON list
    recommend = Column(Boolean)
    evaluated_at = Column(DateTime, default=datetime.utcnow)


class Application(Base):
    __tablename__ = "applications"
    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("job_matches.id"))
    # draft | pending_approval | approved | filling | awaiting_submit | submitted | rejected_self
    status = Column(String, default="draft")
    cover_letter = Column(Text, nullable=True)
    answers_json = Column(Text, nullable=True)   # JSON {question: answer}
    form_data_json = Column(Text, nullable=True)  # platform-specific fill data
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    fill_screenshot = Column(String, nullable=True)  # path under screenshots_dir


class ApplicationLifecycle(Base):
    __tablename__ = "application_lifecycle"
    id = Column(Integer, primary_key=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    # applied | phone_screen | technical | final_round | offer | negotiating
    # accepted | declined | rejected | ghosted
    stage = Column(String)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CompanyBlacklist(Base):
    __tablename__ = "company_blacklist"
    id = Column(Integer, primary_key=True)
    company_name = Column(String, unique=True, index=True)
    reason = Column(String, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    summary = Column(Text, nullable=True)  # condensed long-term memory


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"))
    role = Column(String)  # user | assistant | tool
    content = Column(Text)
    tool_calls_json = Column(Text, nullable=True)
    tool_call_id = Column(String, nullable=True)
    tool_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PlatformSession(Base):
    __tablename__ = "platform_sessions"
    id = Column(Integer, primary_key=True)
    platform = Column(String, unique=True)
    cookies_encrypted = Column(Text)
    saved_at = Column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)
