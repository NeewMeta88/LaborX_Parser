from __future__ import annotations
from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
import asyncio
import html
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta, time
from urllib.parse import urljoin
from aiogram.exceptions import TelegramNetworkError
import re
import aiohttp

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import Config
from .formatter import format_result_messages, format_ai_answer_messages
from .openrouter import openrouter_generate
from .openrouter import openrouter_get_free_daily_limit, openrouter_get_key
from .parser import JobData, parser_loop
from .proposal_prompt import build_filled_prompt
from .state import RuntimeState

router = Router()

JOB_URL_RE = re.compile(r"https?://(?:www\.)?laborx\.com/jobs/[^\s<>()]+", re.IGNORECASE)
JOBS_LIST_URL_RE = re.compile(r"^https?://(?:www\.)?laborx\.com/jobs/?(?:\?.*)?$", re.IGNORECASE)

PAGE_NOT_FOUND_RE = re.compile(
    r'class="page-title"[^>]*>.*?class="primary"[^>]*>\s*404\s*<.*?Sorry,\s*page\s*not\s*found\.',
    re.IGNORECASE | re.DOTALL
)


async def retry_bool(fn, *args, attempts: int = 5, base_delay: float = 2.0, **kwargs) -> bool:
    for i in range(attempts):
        ok = await fn(*args, **kwargs)
        if ok:
            return True
        await asyncio.sleep(min(base_delay * (2 ** i), 20))
    return False


async def safe_reply(msg: Message, text: str, **kwargs) -> bool:
    try:
        await msg.reply(text, **kwargs)
        return True
    except TelegramNetworkError:
        return False


async def safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return True
    except TelegramNetworkError:
        return False


_CB_TOO_OLD_MARKERS = (
    "query is too old",
    "response timeout expired",
    "query id is invalid",
)


async def safe_answer(query: CallbackQuery, text: str = "", **kwargs) -> bool:
    try:
        await query.answer(text, **kwargs)
        return True
    except TelegramBadRequest as e:
        msg = str(e).lower()
        # Важно: это НЕ причина прекращать обработку клика
        if any(m in msg for m in _CB_TOO_OLD_MARKERS):
            return True
        return False
    except TelegramNetworkError:
        return False


async def safe_edit_text(msg: Message, text: str, **kwargs) -> bool:
    try:
        await msg.edit_text(text, **kwargs)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return True
        return False
    except TelegramNetworkError:
        return False


def _extract_job_url(text: str) -> str | None:
    if not text:
        return None
    m = JOB_URL_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip(").,;]}>\n\r\t")


async def _ensure_http_session(app: "App") -> aiohttp.ClientSession:
    if getattr(app, "http", None) is None or app.http.closed:
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            )
        }
        app.http = aiohttp.ClientSession(timeout=timeout, headers=headers)
    return app.http


async def _job_page_exists(app: "App", job_url: str) -> bool:
    try:
        session = await _ensure_http_session(app)
        async with session.get(job_url, allow_redirects=True) as resp:
            if resp.status in (404, 410):
                return False

            final_url = str(resp.url)

            if resp.history and JOBS_LIST_URL_RE.match(final_url):
                return False

            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type or content_type == "":
                page_html = await resp.text(errors="ignore")
                if PAGE_NOT_FOUND_RE.search(page_html):
                    return False

            return True
    except Exception:
        return True


class JobActionCb(CallbackData, prefix="job"):
    act: str
    jid: str


def job_actions_kb(jid: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Accept", callback_data=JobActionCb(act="accept", jid=jid).pack())
    kb.button(text="❌ Skip", callback_data=JobActionCb(act="skip", jid=jid).pack())
    kb.adjust(2)
    return kb.as_markup()


def fmt_reset_ms(ms: int | None) -> str:
    if not ms:
        return "—"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def fmt_bool(v: bool) -> str:
    return "✅ Yes" if v else "❌ No"


def next_utc_midnight_local_str() -> str:
    now = datetime.now(timezone.utc)
    next_midnight_utc = datetime.combine(now.date() + timedelta(days=1), time(0, 0), tzinfo=timezone.utc)
    return next_midnight_utc.astimezone().strftime("%Y-%m-%d %H:%M")


def status_html(app: App, cfg: Config, *, daily_limit, key_info, err) -> str:
    running = app.is_running()

    last_href = "—"
    if app.state.last_seen_href:
        last_href = urljoin("https://laborx.com", app.state.last_seen_href)
    last_href = f"<code>{html.escape(last_href)}</code>" if last_href != "—" else "—"

    last_error = html.escape(app.state.last_error or "—")
    status_err = html.escape(err or "—")

    free_reset_local = next_utc_midnight_local_str()

    free_tier = "—"
    limit_reset = "—"
    if key_info and isinstance(key_info, dict):
        d = key_info.get("data") or {}
        free_tier = "✅ True" if d.get("is_free_tier") is True else (
            "❌ False" if d.get("is_free_tier") is False else "—")
        limit_reset = html.escape(str(d.get("limit_reset") or "—"))

    key_limit_reset = limit_reset

    if daily_limit is not None:
        remaining = max(0, daily_limit - app.state.ai_used_today)
        ai_line = (
            f"Free remaining today: <code>{remaining}/{daily_limit}</code>\n"
            f"Used today (bot): <code>{app.state.ai_used_today}</code>"
        )
    else:
        ai_line = "Free remaining today: <code>—</code>\nUsed today (bot): <code>—</code>"

    return "\n".join(
        [
            "<b>LaborX Parser Status</b>",
            "",
            "<b>Parser</b>",
            "<blockquote>"
            f"Running: {fmt_bool(running)}\n"
            f"Sent: <code>{app.state.sent_count}</code>\n"
            f"Last top href: {last_href}"
            "</blockquote>",
            "",
            "<b>Cache</b>",
            "<blockquote>"
            f"Seen cache: <code>{len(app.state.seen_set)}/{cfg.seen_limit}</code>\n"
            f"Cached jobs: <code>{len(app.jobs)}/{app.jobs_limit}</code>"
            "</blockquote>",
            "",
            "<b>OpenRouter</b>",
            "<blockquote>"
            f"{ai_line}\n"
            f"Free daily reset: <code>00:00 UTC (next {free_reset_local} local)</code>\n"
            f"Key limit reset (spend limit): <code>{key_limit_reset}</code>\n"
            f"Free tier: {free_tier}"
            "</blockquote>",
            "",
            "<b>Errors</b>",
            "<blockquote>"
            f"Last error: <code>{last_error}</code>\n"
            f"OpenRouter status error: <code>{status_err}</code>"
            "</blockquote>",
        ]
    )


def _ensure_ai_day(state):
    today_utc = datetime.now(timezone.utc).date()
    if state.ai_utc_day != today_utc:
        state.ai_utc_day = today_utc
        state.ai_used_today = 0


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = RuntimeState()
        self.queue: asyncio.Queue[JobData] = asyncio.Queue()
        self.stop_event = asyncio.Event()
        self.parser_task: asyncio.Task | None = None
        self.sender_task: asyncio.Task | None = None

        self.http: aiohttp.ClientSession | None = None

        self.jobs: dict[str, JobData] = {}
        self.jobs_order: deque[str] = deque()
        self.jobs_limit: int = cfg.seen_limit

        self.generated_answers: dict[str, str] = {}
        self.processing: set[str] = set()

    def _remember_job(self, job: JobData) -> str:
        jid = uuid.uuid4().hex
        self.jobs[jid] = job
        self.jobs_order.append(jid)
        while len(self.jobs_order) > self.jobs_limit:
            old = self.jobs_order.popleft()
            self.jobs.pop(old, None)
        return jid

    def _forget_job(self, jid: str) -> None:
        self.jobs.pop(jid, None)

    async def sender_loop(self, bot: Bot):
        while True:
            job = await self.queue.get()
            chat_id = self.state.target_chat_id
            if not chat_id:
                continue

            if self.state.startup_chat_id and self.state.startup_message_id:
                try:
                    await bot.edit_message_text(
                        chat_id=self.state.startup_chat_id,
                        message_id=self.state.startup_message_id,
                        text="✅ Monitoring started. I’ll send new job listings here.",
                        disable_web_page_preview=True,
                    )
                except Exception:
                    pass
                finally:
                    self.state.startup_chat_id = None
                    self.state.startup_message_id = None

            parts = format_result_messages(job)
            jid = self._remember_job(job)

            for i, text in enumerate(parts):
                for i, text in enumerate(parts):
                    is_first = i == 0

                    sent = False
                    for attempt in range(3):
                        sent = await safe_send(
                            bot,
                            chat_id,
                            text,
                            reply_markup=job_actions_kb(jid) if is_first else None,
                            disable_web_page_preview=True,
                        )
                        if sent:
                            break
                        await asyncio.sleep(5 * (attempt + 1))

                    if not sent:
                        continue

                    await asyncio.sleep(1.05)

    def is_running(self) -> bool:
        return self.parser_task is not None and not self.parser_task.done()


def _append_status(html_text: str, status: str) -> str:
    already = (
        "❌ Skipped",
        "✅ Accepted",
        "😕 This job is no longer available.",
    )
    if any(html_text.rstrip().endswith(s) for s in already):
        return html_text
    return f"{html_text}\n\n{status}"


def setup_bot(cfg: Config) -> tuple[Bot, Dispatcher, App]:
    session = AiohttpSession(timeout=90)

    bot = Bot(
        token=cfg.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    app = App(cfg)

    @router.message(Command("start"))
    async def cmd_start(message: Message):
        app.state.target_chat_id = message.chat.id

        if app.is_running():
            await message.answer("Already running. /status, /stop.")
            return

        app.state.last_seen_href = None
        app.state.max_seen_job_id = 0
        app.state.seen_set.clear()
        app.state.seen_order.clear()
        app.jobs.clear()
        app.jobs_order.clear()
        app.stop_event.clear()
        app.state.running = True
        app.state.last_error = None

        if app.sender_task is None or app.sender_task.done():
            app.sender_task = asyncio.create_task(app.sender_loop(bot))

        app.parser_task = asyncio.create_task(parser_loop(cfg, app.state, app.queue, app.stop_event))
        m = await message.answer("⏳ Starting… getting initial data from LaborX. Please wait…")
        app.state.startup_chat_id = m.chat.id
        app.state.startup_message_id = m.message_id

    @router.message(Command("stop"))
    async def cmd_stop(message: Message):
        if not app.is_running():
            await message.answer("Parser is not started yet. /start")
            return

        app.stop_event.set()
        await message.answer("Stopping…")

    @router.message(Command("status"))
    async def cmd_status(message: Message):
        progress = await message.answer("⏳ Getting status…")

        today_utc = datetime.now(timezone.utc).date()
        if app.state.ai_utc_day != today_utc:
            app.state.ai_utc_day = today_utc
            app.state.ai_used_today = 0

        daily_limit = None
        key_info = None
        err = None

        try:
            daily_limit = await openrouter_get_free_daily_limit(cfg)
            key_info = await openrouter_get_key(cfg)
        except Exception as e:
            err = str(e)

        await progress.edit_text(
            status_html(app, cfg, daily_limit=daily_limit, key_info=key_info, err=err),
            disable_web_page_preview=True
        )

    @router.callback_query(JobActionCb.filter(F.act == "skip"))
    async def on_skip(query: CallbackQuery, callback_data: JobActionCb):
        msg = query.message
        if not msg:
            await safe_answer(query, "No message")
            return

        await safe_answer(query, "Working...")

        job = app.jobs.get(callback_data.jid)
        html_text = msg.html_text or msg.text or ""
        job_url = (job.url if job else None) or _extract_job_url(html_text)

        if job_url and not await _job_page_exists(app, job_url):
            stale_text = _append_status(html_text, "😕 This job is no longer available.")
            ok = await safe_edit_text(msg, stale_text, reply_markup=None, disable_web_page_preview=True)
            if not ok:
                return
            app._forget_job(callback_data.jid)
            return

        new_text = _append_status(html_text, "❌ Skipped")
        ok = await retry_bool(
            safe_edit_text,
            msg,
            new_text,
            attempts=5,
            base_delay=1.5,
            reply_markup=None,
            disable_web_page_preview=True,
        )
        if not ok:
            await safe_answer(query, "Telegram network issue. Try again.", show_alert=True)
            return

        app._forget_job(callback_data.jid)

    @router.callback_query(JobActionCb.filter(F.act == "accept"))
    async def on_accept(query: CallbackQuery, callback_data: JobActionCb):
        jid = callback_data.jid

        if jid in app.processing:
            await safe_answer(query, "Still working...")
            return

        app.processing.add(jid)
        try:
            msg = query.message
            if not msg:
                await safe_answer(query, "No message")
                return

            await safe_answer(query, "Working...")

            html_text = getattr(msg, "html_text", None) or msg.text or ""
            job = app.jobs.get(jid)

            if job is None:
                await safe_answer(query, "Job data not found (cache expired)", show_alert=True)
                return

            job_url = (job.url if job else None) or _extract_job_url(html_text)
            if job_url and not await _job_page_exists(app, job_url):
                stale_text = _append_status(html_text, "😕 This job is no longer available.")
                await retry_bool(
                    safe_edit_text,
                    msg,
                    stale_text,
                    attempts=3,
                    base_delay=1.5,
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
                app.generated_answers.pop(jid, None)
                app._forget_job(jid)
                return

            accepted_text = _append_status(html_text, "✅ Accepted")

            await retry_bool(
                safe_edit_text,
                msg,
                accepted_text + "\n⏳ Generating reply…",
                attempts=5,
                base_delay=1.5,
                reply_markup=job_actions_kb(jid),
                disable_web_page_preview=True,
            )

            answer = app.generated_answers.get(jid)

            if answer is None:
                prompt = build_filled_prompt(job, app.cfg.portfolio_url)
                try:
                    answer = await asyncio.wait_for(
                        openrouter_generate(
                            prompt,
                            app.cfg,
                            state=app.state,
                            reasoning_enabled=False,
                            timeout_seconds=60,
                            max_retries=1,
                        ),
                        timeout=80,
                    )
                except asyncio.TimeoutError:
                    await retry_bool(
                        safe_edit_text,
                        msg,
                        accepted_text + "\n⚠️ Generation timed out. Tap Accept to retry.",
                        attempts=3,
                        base_delay=2.0,
                        reply_markup=job_actions_kb(jid),
                        disable_web_page_preview=True,
                    )
                    return
                except Exception as e:
                    await retry_bool(
                        safe_edit_text,
                        msg,
                        accepted_text + "\n❗ OpenRouter error. Tap Accept to retry.",
                        attempts=3,
                        base_delay=2.0,
                        reply_markup=job_actions_kb(jid),
                        disable_web_page_preview=True,
                    )
                    await safe_reply(msg, f"OpenRouter error: {e}")
                    return

                app.generated_answers[jid] = answer

            sent_all = True
            for i, text in enumerate(format_ai_answer_messages(job, answer)):
                if i == 0:
                    ok = await retry_bool(
                        safe_reply,
                        msg,
                        text,
                        attempts=5,
                        base_delay=2.0,
                        disable_web_page_preview=True,
                    )
                else:
                    ok = await retry_bool(
                        safe_send,
                        query.bot,
                        msg.chat.id,
                        text,
                        attempts=5,
                        base_delay=2.0,
                        disable_web_page_preview=True,
                    )

                if not ok:
                    sent_all = False
                    break

            if not sent_all:
                await retry_bool(
                    safe_edit_text,
                    msg,
                    accepted_text + "\n⚠️ Telegram send failed. Tap Accept again to retry.",
                    attempts=3,
                    base_delay=2.0,
                    reply_markup=job_actions_kb(jid),
                    disable_web_page_preview=True,
                )
                return

            await retry_bool(
                safe_edit_text,
                msg,
                accepted_text + "\n✅ Reply sent",
                attempts=3,
                base_delay=1.5,
                reply_markup=None,
                disable_web_page_preview=True,
            )

            app.generated_answers.pop(jid, None)
            app._forget_job(jid)

        finally:
            app.processing.discard(jid)

    dp.include_router(router)
    return bot, dp, app
