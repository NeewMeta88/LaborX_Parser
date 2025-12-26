import asyncio

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import ClientSession
from aiohttp.client import ClientTimeout

from .config import Config
from .parser import parser_loop
from .state import RuntimeState

router = Router()

class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = RuntimeState()
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.stop_event = asyncio.Event()
        self.parser_task: asyncio.Task | None = None
        self.sender_task: asyncio.Task | None = None

    async def sender_loop(self, bot: Bot):
        while True:
            text = await self.queue.get()
            chat_id = self.state.target_chat_id
            if not chat_id:
                continue

            await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
            await asyncio.sleep(0.2)

    def is_running(self) -> bool:
        return self.parser_task is not None and not self.parser_task.done()


def setup_bot(cfg: Config) -> tuple[Bot, Dispatcher, App]:
    timeout = ClientTimeout(
        total=90,
        connect=30,
        sock_connect=30,
        sock_read=60
    )
    session = AiohttpSession(session=ClientSession(timeout=timeout))

    bot = Bot(
        token=cfg.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
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
            "\n".join([
                f"Running: {running}",
                f"Sent: {app.state.sent_count}",
                f"Last top href: {app.state.last_seen_href}",
                f"Seen cache size: {len(app.state.seen_set)} (limit {cfg.seen_limit})",
                f"Last error: {app.state.last_error or '—'}",
            ])
        )

    dp.include_router(router)
    return bot, dp, app
