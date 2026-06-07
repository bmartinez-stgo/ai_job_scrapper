import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_base_url: str = "http://job-scraper-llm:8000/v1"
    llm_model: str = "Qwen/Qwen2.5-7B-Instruct-AWQ"
    data_dir: str = "/data"
    db_path: str = "/data/jobs.db"
    score_threshold: int = 70
    scrape_interval_hours: int = 6
    ghosted_days: int = 30
    gmail_from: str = ""
    gmail_to: str = ""
    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_app_password: str = ""
    salary_threshold_mxn: int = 130000
    secret_key: str = "change-me"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def sessions_dir(self) -> str:
        return os.path.join(self.data_dir, "sessions")

    @property
    def screenshots_dir(self) -> str:
        return os.path.join(self.data_dir, "screenshots")

    @property
    def resumes_dir(self) -> str:
        return os.path.join(self.data_dir, "resumes")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
