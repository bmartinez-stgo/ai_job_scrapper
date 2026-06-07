import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler
from app.routers import chat, resumes, applications, runs, settings, profiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info("Job scraper started")
    yield
    stop_scheduler()


app = FastAPI(title="Job Scraper", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(resumes.router)
app.include_router(applications.router)
app.include_router(runs.router)
app.include_router(settings.router)
app.include_router(profiles.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
