import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings


os.makedirs(settings.data_dir, exist_ok=True)
os.makedirs(settings.sessions_dir, exist_ok=True)
os.makedirs(settings.screenshots_dir, exist_ok=True)
os.makedirs(settings.resumes_dir, exist_ok=True)

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa
    Base.metadata.create_all(bind=engine)
