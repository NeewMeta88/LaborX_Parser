from __future__ import annotations

import asyncio
import html
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta, time
from urllib.parse import urljoin

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

    last_href = urljoin(cfg.list_url, app.state.last_seen_href) or "—"
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

        self.jobs: dict[str, JobData] = {}
        self.jobs_order: deque[str] = deque()
        self.jobs_limit: int = cfg.seen_limit

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
                is_first = i == 0
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=job_actions_kb(jid) if is_first else None,
                    disable_web_page_preview=True,
                )
                await asyncio.sleep(0.2)

    def is_running(self) -> bool:
        return self.parser_task is not None and not self.parser_task.done()


def _append_status(html_text: str, status: str) -> str:
    if html_text.rstrip().endswith("❌ Skipped") or html_text.rstrip().endswith("✅ Accepted"):
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
            await query.answer("No message")
            return

        html_text = msg.html_text or msg.text or ""
        new_text = _append_status(html_text, "❌ Skipped")
        await msg.edit_text(new_text, reply_markup=None, disable_web_page_preview=True)
        app._forget_job(callback_data.jid)
        await query.answer("❌ Skipped")

    @router.callback_query(JobActionCb.filter(F.act == "accept"))
    async def on_accept(query: CallbackQuery, callback_data: JobActionCb):
        msg = query.message
        if not msg:
            await query.answer("No message")
            return

        job = app.jobs.get(callback_data.jid)
        html_text = msg.html_text or msg.text or ""

        accepted_text = _append_status(html_text, "✅ Accepted")
        await msg.edit_text(
            accepted_text + "\n⏳ Generating reply…",
            reply_markup=None,
            disable_web_page_preview=True
        )

        if job is None:
            await query.answer("Job data not found (cache expired)")
            return

        await query.answer("Generating reply...")

        try:
            prompt = build_filled_prompt(job, app.cfg.portfolio_url)
            answer = await openrouter_generate(prompt, app.cfg, state=app.state, reasoning_enabled=False)

            _ensure_ai_day(app.state)
            app.state.ai_used_today += 1

            for i, text in enumerate(format_ai_answer_messages(job, answer)):
                if i == 0:
                    await msg.reply(text, disable_web_page_preview=True)
                else:
                    await query.bot.send_message(
                        chat_id=msg.chat.id,
                        text=text,
                        disable_web_page_preview=True,
                    )
                await asyncio.sleep(0.2)

            await msg.edit_text(
                accepted_text + "\n✅ Reply sent",
                reply_markup=None,
                disable_web_page_preview=True
            )


        except Exception as e:
            await msg.edit_text(
                accepted_text + "\n❗ OpenRouter error",
                reply_markup=None,
                disable_web_page_preview=True

            )
            await msg.reply(f"OpenRouter error: {e}")
        finally:
            app._forget_job(callback_data.jid)

    dp.include_router(router)
    return bot, dp, app
