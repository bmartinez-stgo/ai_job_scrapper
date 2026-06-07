import base64
import json
import logging
import os
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import Application, JobMatch, JobPosting, PlatformSession, ApplicationLifecycle
from app.config import settings
from app.services.crypto import decrypt

logger = logging.getLogger(__name__)


async def fill_linkedin(db: Session, application_id: int) -> dict:
    app = db.get(Application, application_id)
    match = db.get(JobMatch, app.match_id)
    job = db.get(JobPosting, match.posting_id)

    cookies = _load_cookies(db, "linkedin")
    if not cookies:
        return {"ok": False, "error": "No LinkedIn session. Add cookies in Settings."}

    form_data = json.loads(app.form_data_json or "{}")
    cover_letter = app.cover_letter or ""

    app.status = "filling"
    db.commit()

    try:
        screenshot_path = await _playwright_fill(job.url, cookies, form_data, cover_letter, application_id)
        app.fill_screenshot = screenshot_path
        app.status = "awaiting_submit"
        db.commit()
        return {"ok": True, "screenshot": screenshot_path}
    except Exception as e:
        logger.error("Playwright fill failed for app %d: %s", application_id, e)
        app.status = "pending_approval"
        db.commit()
        return {"ok": False, "error": str(e)}


async def submit_linkedin(db: Session, application_id: int) -> dict:
    app = db.get(Application, application_id)
    match = db.get(JobMatch, app.match_id)
    job = db.get(JobPosting, match.posting_id)

    cookies = _load_cookies(db, "linkedin")
    if not cookies:
        return {"ok": False, "error": "No LinkedIn session."}

    form_data = json.loads(app.form_data_json or "{}")
    cover_letter = app.cover_letter or ""

    try:
        screenshot_path = await _playwright_fill_and_submit(
            job.url, cookies, form_data, cover_letter, application_id
        )
        app.status = "submitted"
        app.submitted_at = datetime.utcnow()
        app.fill_screenshot = screenshot_path

        lifecycle = ApplicationLifecycle(application_id=application_id, stage="applied")
        db.add(lifecycle)
        db.commit()
        return {"ok": True, "screenshot": screenshot_path}
    except Exception as e:
        logger.error("Playwright submit failed for app %d: %s", application_id, e)
        return {"ok": False, "error": str(e)}


def _load_cookies(db: Session, platform: str) -> list[dict] | None:
    session = db.query(PlatformSession).filter(PlatformSession.platform == platform).first()
    if not session:
        return None
    try:
        raw = decrypt(session.cookies_encrypted)
        return json.loads(raw)
    except Exception:
        return None


async def _playwright_fill(
    job_url: str,
    cookies: list[dict],
    form_data: dict,
    cover_letter: str,
    app_id: int,
    submit: bool = False,
) -> str:
    from playwright.async_api import async_playwright

    screenshot_dir = settings.screenshots_dir
    screenshot_path = os.path.join(screenshot_dir, f"app_{app_id}.png")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()

        await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        easy_apply = page.locator("button:has-text('Easy Apply')").first
        if not await easy_apply.is_visible():
            await browser.close()
            raise RuntimeError("Easy Apply button not found — may require manual application")

        await easy_apply.click()
        await page.wait_for_timeout(1500)

        await _fill_form_steps(page, form_data, cover_letter, submit)

        await page.screenshot(path=screenshot_path, full_page=False)
        await browser.close()

    return screenshot_path


async def _playwright_fill_and_submit(
    job_url: str,
    cookies: list[dict],
    form_data: dict,
    cover_letter: str,
    app_id: int,
) -> str:
    return await _playwright_fill(job_url, cookies, form_data, cover_letter, app_id, submit=True)


async def _fill_form_steps(page, form_data: dict, cover_letter: str, submit: bool):
    from playwright.async_api import Page

    max_steps = 10
    for step in range(max_steps):
        await _fill_visible_fields(page, form_data, cover_letter)
        await page.wait_for_timeout(500)

        next_btn = page.locator("button:has-text('Next'), button:has-text('Continue')").first
        review_btn = page.locator("button:has-text('Review')").first
        submit_btn = page.locator("button:has-text('Submit application')").first

        if await submit_btn.is_visible():
            if submit:
                await submit_btn.click()
                await page.wait_for_timeout(2000)
            return

        if await review_btn.is_visible():
            await review_btn.click()
            await page.wait_for_timeout(1000)
            continue

        if await next_btn.is_visible():
            await next_btn.click()
            await page.wait_for_timeout(1000)
        else:
            break


async def _fill_visible_fields(page, form_data: dict, cover_letter: str):
    # Phone
    phone_field = page.locator("input[id*='phoneNumber'], input[name*='phone']").first
    if await phone_field.is_visible() and form_data.get("phone"):
        await phone_field.fill(form_data["phone"])

    # Cover letter textarea
    cl_field = page.locator("textarea[id*='coverLetter'], textarea[name*='cover']").first
    if await cl_field.is_visible() and cover_letter:
        await cl_field.fill(cover_letter)

    # Generic text inputs from form_data
    for field_id, value in form_data.items():
        if field_id in ("phone", "name", "email"):
            continue
        locator = page.locator(f"input[id*='{field_id}'], textarea[id*='{field_id}']").first
        if await locator.is_visible():
            await locator.fill(str(value))
