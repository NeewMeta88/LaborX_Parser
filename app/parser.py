import asyncio
import re
from collections import deque
from dataclasses import dataclass
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
    await page.wait_for_selector(SEL_CONTENT_WRAPPER, timeout=30_000)

    cw = page.locator(SEL_CONTENT_WRAPPER).first
    pc = cw.locator(SEL_PAGE_CONTENT).first

    job_name = await safe_text(pc.locator(f"{SEL_GENERAL_INFO_CARD} {SEL_JOB_NAME}"), default="")

    desc_section = pc.locator(f"{SEL_JOB_DESCRIPTION_ROOT} {SEL_JOB_INFO_SECTION} .description").first
    raw = await desc_section.inner_text()
    raw = raw.replace("\u00a0", " ").replace("\r\n", "\n")
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    description = "\n".join(lines).strip()

    tags: list[str] = []
    tag_loc = pc.locator(SEL_TAGS)
    try:
        count = await tag_loc.count()
        for i in range(count):
            t = await safe_text(tag_loc.nth(i), default="")
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
        url=page.url
    )


def mark_seen(href: str, seen_hrefs: set[str], seen_order: deque, limit: int) -> None:
    if href in seen_hrefs:
        return
    if len(seen_order) >= limit:
        old = seen_order.popleft()
        seen_hrefs.discard(old)
    seen_order.append(href)
    seen_hrefs.add(href)


async def parser_loop(cfg, state, out_queue: asyncio.Queue[JobData], stop_event: asyncio.Event) -> None:
    list_url = cfg.list_url
    interval_seconds = cfg.interval_seconds
    max_list_items = cfg.max_list_items
    seen_limit = cfg.seen_limit
    user_data_dir = cfg.user_data_dir
    headless = cfg.headless

    if getattr(state, "seen_set", None) is None:
        state.seen_set = set()
    if getattr(state, "seen_order", None) is None:
        state.seen_order = deque()
    if getattr(state, "sent_count", None) is None:
        state.sent_count = 0
    if getattr(state, "max_seen_job_id", None) is None:
        state.max_seen_job_id = 0
    if getattr(state, "last_seen_href", None) is None:
        state.last_seen_href = None

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
        )
        page = await context.new_page()
        page.set_default_timeout(20_000)

        try:
            await page.goto(list_url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")

            while not stop_event.is_set():
                try:
                    await page.evaluate("window.scrollTo(0, 0)")
                    hrefs = await get_job_hrefs_from_list(page, limit=max_list_items)

                    if hrefs:
                        ids = [extract_job_id(h) for h in hrefs]
                        ids = [i for i in ids if i is not None]
                        top_max_id = max(ids) if ids else 0

                        first_run = (state.max_seen_job_id == 0)

                        if first_run:
                            candidates = hrefs[:]
                            new_hrefs = [h for h in candidates if h not in state.seen_set]
                        else:
                            if state.last_seen_href is None:
                                candidates = hrefs[:]
                            else:
                                if state.last_seen_href in hrefs:
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

                                job_url = urljoin(list_url, href)

                                try:
                                    await page.goto(job_url, wait_until="domcontentloaded")
                                    await page.wait_for_load_state("networkidle")

                                    data = await parse_job_page(page)
                                    await out_queue.put(data)
                                    state.sent_count += 1

                                    mark_seen(href, state.seen_set, state.seen_order, seen_limit)

                                finally:
                                    await page.goto(list_url, wait_until="domcontentloaded")
                                    await page.wait_for_load_state("networkidle")

                                await asyncio.sleep(0.8)

                        state.max_seen_job_id = max(state.max_seen_job_id, top_max_id)
                        state.last_seen_href = hrefs[0]
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                    except asyncio.TimeoutError:
                        pass

                    if not stop_event.is_set():
                        await page.reload(wait_until="domcontentloaded")
                        await page.wait_for_load_state("networkidle")

                except PlaywrightTimeoutError as e:
                    state.last_error = f"Timeout: {e}"
                    await page.goto(list_url, wait_until="domcontentloaded")
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(2)

                except Exception as e:
                    state.last_error = str(e)
                    await asyncio.sleep(2)

        finally:
            await context.close()
