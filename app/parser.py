import asyncio
import re
import shutil
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import List
from urllib.parse import urljoin

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

SEL_FIRST_CARD = ".root.job-card.child-card"
SEL_CARD_LINK = ".job-title.job-link.row"

SEL_CONTENT_WRAPPER = ".content-wrapper"
SEL_PAGE_CONTENT = ".page-content"
SEL_GENERAL_INFO_CARD = ".general-info-card"
SEL_JOB_NAME = ".job-name"

SEL_JOB_DESCRIPTION_ROOT = ".root.job-description"
SEL_JOB_INFO_SECTION = ".job-info-section"

SEL_TAGS = ".skills-container .lx-tag-cloudy.skill-tags.desktop-tags .tag-wrapper .tag.clickable"

SEL_STICKY_BLOCK = ".sticky-block"
SEL_JOB_INFO_BLOCK = ".job-info-block"

SEL_PRICE = ".info-item.budget-info .info-value"
SEL_DAYS = ".info-item.day-info .info-value"

_JOB_ID_RE = re.compile(r"-(\d+)$")

LABORX_BASE = "https://laborx.com"


def reset_user_data_dir(user_data_dir: str) -> str:
    p = Path(user_data_dir).resolve()
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def extract_job_id(href: str) -> int | None:
    m = _JOB_ID_RE.search(href or "")
    return int(m.group(1)) if m else None


@dataclass
class JobData:
    job_name: str
    description: str
    tags: List[str]
    price: str
    days: str
    deadline: str
    url: str


async def get_job_hrefs_from_list(page, limit: int) -> list[str]:
    await page.wait_for_selector(SEL_FIRST_CARD, timeout=30_000)
    cards = page.locator(SEL_FIRST_CARD)
    n = min(await cards.count(), limit)

    hrefs: list[str] = []
    for i in range(n):
        link = cards.nth(i).locator(SEL_CARD_LINK).first
        href = await link.get_attribute("href")
        if href:
            hrefs.append(href)
    return hrefs


async def safe_text(locator, default: str = "") -> str:
    try:
        txt = await locator.first.inner_text()
        return " ".join(txt.split())
    except Exception:
        return default


async def parse_job_page(page) -> JobData:
    try:
        await page.wait_for_selector(SEL_CONTENT_WRAPPER, timeout=15_000, state="attached")
    except PlaywrightTimeoutError:
        raise

    cw = page.locator(SEL_CONTENT_WRAPPER).first

    pc_loc = cw.locator(SEL_PAGE_CONTENT)
    pc = pc_loc.first if (await pc_loc.count() > 0) else cw

    job_name = await safe_text(pc.locator(f"{SEL_GENERAL_INFO_CARD} {SEL_JOB_NAME}"), default="")
    if not job_name:
        job_name = await safe_text(page.locator(SEL_JOB_NAME), default="")

    desc_section = pc.locator(f"{SEL_JOB_DESCRIPTION_ROOT} {SEL_JOB_INFO_SECTION} .description").first
    raw = await desc_section.inner_text()
    raw = raw.replace("\u00a0", " ").replace("\r\n", "\n")
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    description = "\n".join(lines).strip()

    tags: list[str] = []
    try:
        tag_texts = await pc.locator(SEL_TAGS).all_inner_texts()
        for t in tag_texts:
            t = (t or "").replace("\u00a0", " ").strip()
            if t:
                tags.append(t)
    except Exception:
        pass

    price = ""
    days = ""
    deadline = ""

    sticky_loc = cw.locator(SEL_STICKY_BLOCK)
    if await sticky_loc.count() > 0:
        actions_loc = sticky_loc.first.locator(".root.actions-card.actions-card")
        if await actions_loc.count() > 0:
            job_info_loc = actions_loc.first.locator(SEL_JOB_INFO_BLOCK)
            if await job_info_loc.count() > 0:
                job_info = job_info_loc.first

                price = await safe_text(job_info.locator(SEL_PRICE), default="")

                day_value_loc = job_info.locator(SEL_DAYS)
                if await day_value_loc.count() > 0:
                    day_value = day_value_loc.first

                    gray_loc = day_value.locator(".gray-info")
                    deadline = await safe_text(gray_loc, default="")

                    try:
                        days = await day_value.evaluate(
                            """(el) => {
                                const span = el.querySelector('.gray-info');
                                const spanText = span ? span.textContent : '';
                                return el.textContent.replace(spanText, '').trim();
                            }""",
                            timeout=2000
                        )
                    except Exception:
                        days = ""

    return JobData(
        job_name=job_name,
        description=description,
        tags=tags,
        price=price,
        days=days,
        deadline=deadline,
        url=page.url,
    )


def mark_seen(href: str, seen_hrefs: set[str], seen_order: deque, limit: int) -> None:
    if href in seen_hrefs:
        return
    if len(seen_order) >= limit:
        old = seen_order.popleft()
        seen_hrefs.discard(old)
    seen_order.append(href)
    seen_hrefs.add(href)


async def parser_loop(cfg, state, out_queue: asyncio.Queue["JobData"], stop_event: asyncio.Event) -> None:
    list_url = cfg.list_url
    interval_seconds = cfg.interval_seconds
    max_list_items = cfg.max_list_items
    seen_limit = cfg.seen_limit
    headless = cfg.headless

    state.seen_set = set()
    state.seen_order = deque()
    state.sent_count = 0
    state.last_error = None
    state.max_seen_job_id = 0
    state.last_seen_href = None

    user_data_dir = reset_user_data_dir(cfg.user_data_dir)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
        )
        page = await context.new_page()
        page.set_default_timeout(20_000)

        try:
            await page.goto(list_url, wait_until="domcontentloaded")
            await page.wait_for_selector(SEL_FIRST_CARD, timeout=30_000)

            while not stop_event.is_set():
                try:
                    await page.evaluate("window.scrollTo(0, 0)")

                    first_run = (state.max_seen_job_id == 0)

                    scan_limit = (max_list_items * 5) if first_run else max_list_items
                    hrefs = await get_job_hrefs_from_list(page, limit=scan_limit)

                    if not hrefs:
                        await asyncio.sleep(1)
                    else:
                        ids = [extract_job_id(h) for h in hrefs]
                        ids = [i for i in ids if i is not None]
                        top_max_id = max(ids) if ids else 0

                        if first_run:
                            candidates = hrefs[:]  # много ссылок
                            targets: list[str] = []
                            for h in candidates:
                                if h not in state.seen_set:
                                    targets.append(h)
                                if len(targets) >= max_list_items:
                                    break
                            new_hrefs = targets
                        else:
                            if state.last_seen_href and state.last_seen_href in hrefs:
                                idx = hrefs.index(state.last_seen_href)
                                candidates = hrefs[:idx]
                            else:
                                candidates = hrefs[:]

                            new_hrefs = []
                            for h in candidates:
                                jid = extract_job_id(h)
                                if jid is not None and jid > state.max_seen_job_id and h not in state.seen_set:
                                    new_hrefs.append(h)

                        if new_hrefs:
                            for href in reversed(new_hrefs):
                                if stop_event.is_set():
                                    break

                                job_url = urljoin(LABORX_BASE, href)

                                data = None
                                try:
                                    resp = await page.goto(job_url, wait_until="domcontentloaded")

                                    if resp is not None and resp.status >= 400:
                                        state.last_error = f"HTTP {resp.status}: {job_url}"
                                        mark_seen(href, state.seen_set, state.seen_order, seen_limit)
                                        continue

                                    try:
                                        await page.wait_for_selector(SEL_CONTENT_WRAPPER, timeout=15_000,
                                                                     state="attached")
                                    except PlaywrightTimeoutError:
                                        state.last_error = f"No content wrapper: {job_url}"
                                        mark_seen(href, state.seen_set, state.seen_order, seen_limit)
                                        continue

                                    try:
                                        data = await parse_job_page(page)
                                    except PlaywrightTimeoutError as e:
                                        state.last_error = f"Timeout parsing job page: {job_url} | {e}"
                                    except Exception as e:
                                        state.last_error = f"Parse error: {job_url} | {e}"

                                finally:
                                    await page.goto(list_url, wait_until="domcontentloaded")
                                    await page.wait_for_selector(SEL_FIRST_CARD, timeout=30_000)

                                if data is None:
                                    mark_seen(href, state.seen_set, state.seen_order, seen_limit)
                                    await asyncio.sleep(0.3)
                                    continue

                                await out_queue.put(data)
                                state.sent_count += 1
                                mark_seen(href, state.seen_set, state.seen_order, seen_limit)
                                await asyncio.sleep(0.6)

                        state.max_seen_job_id = max(state.max_seen_job_id, top_max_id)
                        state.last_seen_href = hrefs[0]

                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                    except asyncio.TimeoutError:
                        pass

                    if not stop_event.is_set():
                        await page.reload(wait_until="domcontentloaded")
                        await page.wait_for_selector(SEL_FIRST_CARD, timeout=30_000)

                except PlaywrightTimeoutError as e:
                    state.last_error = f"Timeout: {e}"
                    await page.goto(list_url, wait_until="domcontentloaded")
                    await page.wait_for_selector(SEL_FIRST_CARD, timeout=30_000)
                    await asyncio.sleep(2)

                except Exception as e:
                    state.last_error = str(e)
                    await asyncio.sleep(2)

        finally:
            await context.close()
