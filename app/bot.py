import asyncio
import uuid
from collections import deque

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
from .proposal_prompt import build_filled_prompt
from .parser import JobData, parser_loop
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
        self.jobs_limit: int = 200

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
        await message.answer("Parser started. I’ll send new job listings here.")

    @router.message(Command("stop"))
    async def cmd_stop(message: Message):
        if not app.is_running():
            await message.answer("Parser is not started yet. /start")
            return

        app.stop_event.set()
        await message.answer("Stopping…")

    @router.message(Command("status"))
    async def cmd_status(message: Message):
        running = app.is_running()
        await message.answer(
            "\n".join(
                [
                    f"Running: {running}",
                    f"Sent: {app.state.sent_count}",
                    f"Last top href: {app.state.last_seen_href}",
                    f"Seen cache size: {len(app.state.seen_set)} (limit {cfg.seen_limit})",
                    f"Cached jobs: {len(app.jobs)} (limit {app.jobs_limit})",
                    f"Last error: {app.state.last_error or '—'}",
                ]
            )
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
        await msg.edit_text(_append_status(html_text, "✅ Accepted"), reply_markup=None, disable_web_page_preview=True)

        if job is None:
            await query.answer("Job data not found (cache expired)")
            return

        await query.answer("Generating reply...")

        try:
            prompt = build_filled_prompt(job, app.cfg.portfolio_url)
            answer = await openrouter_generate(prompt, app.cfg, reasoning_enabled=False)

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

        except Exception as e:
            await msg.reply(f"OpenRouter error: {e}")

        finally:
            app._forget_job(callback_data.jid)

    dp.include_router(router)
    return bot, dp, app
